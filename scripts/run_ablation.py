"""RAG ablation driver — N3 ka measurement harness (METRICS.md §3 N3).

────────────────────────────────────────────────────────────────────────────
🔴 DRY RUN DEFAULT. `--write` ke bina sirf PLAN print hota hai.
────────────────────────────────────────────────────────────────────────────
    .venv/bin/python scripts/run_ablation.py                     # plan dekho
    .venv/bin/python scripts/run_ablation.py --write \
        --runs-per-cell 2 --faults F4 --auto-approve             # chhota trial

Ye script AGENT LOOP ko seedha chalati hai (worker process ke bina) kyunki:
  - eval mein insaan gate blocker hai → `--auto-approve`
  - HTTP request se ye nahi chal sakta (minutes-hours) — wahi wajah jo job
    queue ki hai, ab eval pe bhi

🔴 4 FAIRNESS CONTROLS (`METRICS.md` §3 N3 ki table) — teeno yahan enforced:
  1. Same seeds            → seed = base + deterministic offset per cell
  2. Pinned provider       → `settings.pin_provider` asli hai; chain disabled
  3. no-RAG arm ko sirf logs milte hain → `rag_enabled=False` incidents row mein
  4. Same prompts          → prompt text propose/diagnose modules mein hi hai,
                             is run ne unko touch nahi kiya

⚠️ TOKEN COST: har incident ~3,800–11,400 tokens (TOKEN_BUDGET §3). Poora plan
   (5 faults × 2 arms × 20 runs) ~1.06M tokens ≈ 5.3 din Groq ceiling. Isliye
   chhote trials se shuru karo.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _plan(faults: list[str], runs: int, arms: list[str]) -> int:
    """Plan print + total incidents return."""
    total = len(faults) * runs * len(arms)
    est_tokens = total * 5300  # typical-case estimate (TOKEN_BUDGET §3)
    print(f"cells      : {len(faults)} faults × {len(arms)} arms × {runs} runs = {total} incidents")
    print(f"arms       : {', '.join(arms)}")
    print(f"est tokens : ~{est_tokens:,} (~{est_tokens / 200_000:.1f} Groq-days)")
    for f in faults:
        for arm in arms:
            seeds = ", ".join(str(_seed_for(f, arm, i)) for i in range(runs))
            print(f"  {f} rag={arm:<5} seeds: {seeds}")
    return total


def _seed_for(fault: str, arm: str, run_idx: int) -> int:
    """Deterministic seed per (fault, arm, run). Do arms ko SAME fault-milna
    control #1 hai — isi liye seed fault+run pe depend karta hai, arm pe NAHI."""
    base = sum(ord(c) for c in fault) * 1000 + run_idx * 17
    return base % 99991


def _drive_one(fault_id: str, *, rag: bool, seed: int, auto_approve: bool) -> dict[str, object]:
    """Ek incident: inject → detect-phase → (approve) → exec-phase → outcome.

    Returns record dict — eval_results/*.jsonl ki ek line."""
    os.environ["ONCALL_RAG_ENABLED"] = "true" if rag else "false"
    from chaos.inject import inject
    from oncall.agent.loop import StepOutcome, run_until_pause_or_terminal
    from oncall.config import load_settings
    from oncall.db.queries import (
        advance_state,
        close_incident,
        decide_approval,
        get_approval,
        get_incident,
    )

    result = inject(
        fault_id, write=True, seed=seed, rag_enabled=load_settings().rag_enabled
    )
    iid = result.incident_id
    assert iid is not None

    outcome: dict[str, object] = {
        "fault": fault_id, "rag": rag, "seed": seed, "incident_id": iid,
        "ts": datetime.now(timezone.utc).isoformat(),
    }

    out = run_until_pause_or_terminal(iid)
    incident = get_incident(iid)
    state = str(incident["state"])

    if state == "AWAITING_APPROVAL":
        if not auto_approve:
            outcome["final"] = "awaiting_approval"
            return outcome
        approval = get_approval(iid, int(incident["attempt"]))
        decided = decide_approval(
            incident_id=iid, attempt=int(incident["attempt"]), decision="approved"
        )
        if decided is None:
            outcome["final"] = "approval_race"
            return outcome
        record_event_safe(iid, approval)
        advance_state(
            iid, frm="AWAITING_APPROVAL", to="EXECUTING", why="ablation auto-approve"
        )
        out2 = run_until_pause_or_terminal(iid)
        state = str(get_incident(iid)["state"])

    outcome["final"] = state
    return outcome


def record_event_safe(iid: int, approval: dict[str, object] | None) -> None:
    """Approval event likho (eval trace ke liye). Fail hona run nahi maarta."""
    try:
        from oncall.db.queries import record_event

        record_event(
            iid, "approval",
            {"decision": "approved", "via": "ablation_auto",
             "action": str(approval["action"]) if approval else "?"},
        )
    except Exception:  # noqa: BLE001
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/run_ablation.py",
        description="N3 RAG-ablation driver (DRY RUN default).",
    )
    parser.add_argument("--faults", default="F1,F2,F3,F4,F5", help="comma-separated")
    parser.add_argument("--runs-per-cell", type=int, default=20)
    parser.add_argument("--arms", default="with,without", help="with|without|with,without")
    parser.add_argument("--auto-approve", action="store_true",
                        help="human gate ko auto-approved karo (EVAL ONLY)")
    parser.add_argument("--write", action="store_true", help="asli mein chalao")
    args = parser.parse_args(argv)

    faults = [f.strip().upper() for f in args.faults.split(",") if f.strip()]
    arms = []
    for a in args.arms.split(","):
        name = a.strip().lower()
        arms.append({"with": True, "without": False}.get(name, True) if name else True)
        if name not in ("with", "without"):
            print(f"⚠️ unknown arm '{a}' — 'true' treat hua")

    s = load_settings_pinned()
    total = _plan(faults, args.runs_per_cell, [t and "with" or "without" for t in arms])

    if not args.write:
        print("\n🔴 DRY-RUN — kuch inject nahi hua, ek bhi token kharch nahi hua.")
        print("   Chhota trial: --write --faults F4 --runs-per-cell 2 --auto-approve")
        return 0

    if not s.groq_api_key:
        print("❌ GROQ_API_KEY set nahi hai (.env) — LLM calls fail hongi.")
        return 1

    out_dir = REPO_ROOT / "eval_results"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_file = out_dir / f"ablation-{stamp}.jsonl"

    results = []
    for fault_id in faults:
        for rag in arms:
            for run_idx in range(args.runs_per_cell):
                seed = _seed_for(fault_id, "x", run_idx)
                rec = _drive_one(fault_id, rag=rag, seed=seed,
                                 auto_approve=args.auto_approve)
                results.append(rec)
                print(f"  {fault_id} rag={rag} seed={seed} → {rec['final']}")
                with open(out_file, "a") as fh:
                    fh.write(json.dumps(rec) + "\n")
                _reset_world()

    recovered = sum(1 for r in results if r["final"] == "RECOVERED")
    print(f"\n✅ {total} runs done → {recovered} RECOVERED. Raw: {out_file}")
    print("   N3 delta SQL: METRICS.md §3 N3 (per-class, kabhi aggregate nahi)")
    return 0


def load_settings_pinned():
    """Provider pin verify karo — ablation mein fallback band hona ZARURI hai
    (R8: confounded arms). Pin khaali ho toh loudly rok do."""
    from oncall.config import load_settings

    s = load_settings()
    if not s.pin_provider:
        raise SystemExit(
            "❌ ONCALL_PIN_PROVIDER khaali hai — fallback chain active hogi. "
            "Ablation ke liye pin ZARURI hai (R8). .env mein 'groq' set karo."
        )
    return s


def _reset_world() -> None:
    """Do runs ke beech world reset — warna pichhle fault ka leak agle run mein
    ghus jaata aur per-class rate mixture ban jaata (README ka warning)."""
    from chaos.inject import heal

    for fid in ("F1", "F2", "F3", "F4", "F5"):
        try:
            heal(fid, write=True)
        except Exception:  # noqa: BLE001 - container absent bhi normal hai
            pass


if __name__ == "__main__":
    sys.exit(main())
