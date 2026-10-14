"""Agent loop — state machine ka DRIVER. Ye file har transition chalati hai.

────────────────────────────────────────────────────────────────────────────
🔴 Ek step = ek transition. Aur ye jaan-boojh kar hai
────────────────────────────────────────────────────────────────────────────
`step()` incident ka CURRENT STATE DB se padhta hai aur sirf agla kadam uthata
hai. Poora pipeline ek lamba function hota toh beech mein marne pe do raaste
the: (a) shuru se dobara (kaam duplicate), (b) wahi latak jaana (silent hang).
Step-wise design dono ko impossible banata hai: naya worker bas agla step
uthata hai — **resumability** (`TDD.md` §1a).

Isi liye har step APNI JAANKAARI DB SE PADHTA hai, memory se nahi. Retry ek NAYA
job hai, naya function call — jo cheez steps ke beech bachi rehni chahiye wo
`events` / `retrievals` / `approvals` table mein hai. Memory mein kuch nahi.

────────────────────────────────────────────────────────────────────────────
Do phases, ek hi mechanism
────────────────────────────────────────────────────────────────────────────
Worker ko do baar bulaana padta hai, kyunki beech mein INSAAN hai:

    job 1: DETECTED → … → PROPOSING → AWAITING_APPROVAL   (phir job done)
    insaan approve/reject karta hai (routes_approve.py)
    job 2: EXECUTING → VERIFYING → RECOVERED | retry | ESCALATED

Approval gate ek paused DATABASE STATE hai — blocked thread nahi. Laptop band
karke kal approve karna bhi kaam karega.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any

from oncall.agent.collect import gather
from oncall.agent import diagnose as diagnose_mod
from oncall.agent import propose as propose_mod
from oncall.agent.states import State, assert_transition, is_terminal
from oncall.actions import broker
from oncall.config import MAX_ATTEMPTS, ORACLE_LOAD_RPS, settings

#: Length of the pre-diagnosis load burst (see `_step_collecting`).
LOAD_PROBE_SECONDS = 5
from oncall.db.queries import (
    advance_state,
    bump_attempt,
    close_incident,
    create_approval,
    enqueue_job,
    get_approval,
    get_incident,
    get_timeline,
    insert_verdict,
    list_verdicts,
    record_event,
)
from oncall.llm import router as router_module
from oncall.oracle.health import is_recovered
from oncall.oracle.load import generate_load
from oncall.retrieval.embed import EmbedUnavailableError
from oncall.retrieval.search import docs_for_prompt, hybrid


class StepOutcome(Enum):
    """Ek `step()` ka nateeja — worker loop isi pe decide karta hai aage chalein
    ya nahi."""

    CONTINUED = "continued"  #: agla step isi job mein chalega
    PAUSED_APPROVAL = "paused_approval"  #: 🔒 insaan ka wait — job DONE
    TERMINAL = "terminal"  #: RECOVERED ya ESCALATED — job DONE
    NO_JOB = "no_job"  #: state aisi hai jahan karne ko kuch nahi (defensive)


_MAX_STEPS_PER_JOB = 12

#: Defensive cap: 10 states hain, 12 se zyada transitions ek job mein bug hain.
#: Infinite loop se containers peet-te rehte — runaway guard (layer 5 ki tarah).


# ═════════════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════════════
def step(incident_id: int) -> StepOutcome:
    """CURRENT state ke hisaab se EK transition. Worker isi ko loop mein bulata hai."""
    incident = get_incident(incident_id)
    if incident is None:
        raise ValueError(f"incident {incident_id} exist nahi karta")

    state = State(str(incident["state"]))

    if is_terminal(state):
        return StepOutcome.TERMINAL

    handler = {
        State.DETECTED: _step_detected,
        State.COLLECTING: _step_collecting,
        State.DIAGNOSING: _step_diagnosing,
        State.RETRIEVING: _step_retrieving,
        State.PROPOSING: _step_proposing,
        State.AWAITING_APPROVAL: _step_paused,
        State.EXECUTING: _step_executing,
        State.VERIFYING: _step_verifying,
    }[state]
    return handler(int(incident["id"]), incident)


def run_until_pause_or_terminal(incident_id: int) -> StepOutcome:
    """Worker ka entry point: steps chalao jab tak pause/terminal na ho.

    `_MAX_STEPS_PER_JOB` ka guard yahan hai — 10 states ke machine mein 12+
    transitions ka matlab bug hai, aur us bug ka nateeja infinite restart-loop
    hota jo ASLI containers ko peet-ta rehta."""
    outcome = StepOutcome.CONTINUED
    for _ in range(_MAX_STEPS_PER_JOB):
        outcome = step(incident_id)
        if outcome is not StepOutcome.CONTINUED:
            return outcome
    raise RuntimeError(
        f"incident {incident_id}: {_MAX_STEPS_PER_JOB} steps ke baad bhi pause/terminal "
        "nahi hua — state machine mein cycle hai, ye bug hai"
    )


# ═════════════════════════════════════════════════════════════════════════════
# Per-state handlers — har ek chhota, padhne layak
# ═════════════════════════════════════════════════════════════════════════════


def _move(incident_id: int, frm: State, to: State, why: str) -> None:
    """Policy check + optimistic-guard UPDATE, saath-saath. Har handler isi se
    state badalta hai — koi seedha `queries.advance_state` nahi karta, taaki
    ALLOWED dict bypass karna IMPOSSIBLE ho."""
    assert_transition(frm, to)
    changed = (
        close_incident(incident_id, frm=frm.value, to=to.value, why=why)
        if to in {State.RECOVERED, State.ESCALATED}
        else advance_state(incident_id, frm=frm.value, to=to.value, why=why)
    )
    if not changed:
        # Optimistic guard ne rok diya: koi doosra worker pehle badal chuka.
        # Chup-chaap aage badhna DOUBLE-EXECUTION hai — isliye loud.
        raise RuntimeError(
            f"incident {incident_id}: {frm}→{to} race haar gaya — doosra worker "
            "pehle nikal gaya. Is job ko ab kuch nahi karna."
        )


def _escalate(
    incident_id: int,
    frm: State,
    reason: str,
    detail: str,
    *,
    extra_payload: dict[str, Any] | None = None,
) -> StepOutcome:
    """Honest stop. Chaar alag reasons alag-alag count hote hain (METRICS §3 N7),
    isliye reason string yahan standardised hai."""
    payload = {"reason": reason, "detail": detail[:500]}
    if extra_payload:
        payload.update(extra_payload)
    record_event(incident_id, "escalate", payload)
    _move(incident_id, frm, State.ESCALATED, reason)
    return StepOutcome.TERMINAL


def _step_detected(incident_id: int, _incident: dict[str, Any]) -> StepOutcome:
    _move(incident_id, State.DETECTED, State.COLLECTING, "worker picked up job")
    return StepOutcome.CONTINUED


def _step_collecting(incident_id: int, incident: dict[str, Any]) -> StepOutcome:
    service = str(incident["service"])
    # 🔴 Load BEFORE reading logs. Some faults only exist under traffic: F4 (pool
    #    exhaustion) answers an idle /health with 200 and logs nothing. Without
    #    this burst the agent diagnosed F4 as "healthy" and escalated (incidents
    #    #6 and #9). Same load generator and rate as the oracle, but 5 s, so the
    #    evidence matches what the verdict will later measure.
    load = asyncio.run(
        generate_load(f"{settings.shopapp_url}/orders", rps=ORACLE_LOAD_RPS, seconds=LOAD_PROBE_SECONDS)
    )
    collected = gather(service)
    record_event(incident_id, "logs", {**collected.to_payload(), "load_probe": load.to_payload()})
    _move(incident_id, State.COLLECTING, State.DIAGNOSING, "logs collected")
    return StepOutcome.CONTINUED


def _step_diagnosing(incident_id: int, incident: dict[str, Any]) -> StepOutcome:
    timeline = get_timeline(incident_id)
    logs_payload = _latest(timeline, "logs")
    attempt = int(incident["attempt"])

    try:
        diagnosis = diagnose_mod.run(
            incident_id=incident_id,
            attempt=attempt,
            service=str(incident["service"]),
            health_url=settings.shopapp_health_url,
            collected=_collected_from(logs_payload),
            load_probe=(logs_payload or {}).get("load_probe"),
        )
    except router_module.ParseError as exc:
        return _escalate(incident_id, State.DIAGNOSING, "parse_failed", str(exc))
    except router_module.LLMUnavailableError as exc:
        return _escalate(incident_id, State.DIAGNOSING, "llm_unavailable", str(exc))

    nxt = State.RETRIEVING if bool(incident["rag_enabled"]) else State.PROPOSING
    why = "diagnosis ready" + ("" if nxt is State.RETRIEVING else " (RAG off — ablation arm)")
    _move(incident_id, State.DIAGNOSING, nxt, why)
    return StepOutcome.CONTINUED


def _step_retrieving(incident_id: int, incident: dict[str, Any]) -> StepOutcome:
    timeline = get_timeline(incident_id)
    diag = diagnose_mod.latest_diagnosis_payload(timeline)
    summary = str((diag or {}).get("summary") or "")
    evidence = [str(e) for e in ((diag or {}).get("evidence") or [])][:3]

    # Query = diagnosis + exact tokens (search.py ka header dekho). Diagnosis
    # na mili toh symptom line fallback — kabhi khaali query mat bhejo,
    # plainto_tsquery khaali string pe kuch match nahi karta.
    query_text = " ".join([summary, *evidence]).strip() or str(incident["fault_class"])

    try:
        hybrid(
            incident_id=incident_id,
            attempt=int(incident["attempt"]),
            query_text=query_text,
        )
    except EmbedUnavailableError as exc:
        # 🔴 Embed service down = RAG arm apna KAAM nahi kar sakta. Chup-chaap
        #    "0 runbooks" ke saath aage badhna N3 ko corrupt karta: incident
        #    rag_enabled=true label ke saath bina-RAG chal jaata. Silent
        #    degradation measurement ki dushman hai — honest infra-stop hai.
        return _escalate(
            incident_id, State.RETRIEVING, "retrieval_unavailable", str(exc)[:300],
        )
    _move(incident_id, State.RETRIEVING, State.PROPOSING, "top-k runbooks ready")
    return StepOutcome.CONTINUED


def _step_proposing(incident_id: int, incident: dict[str, Any]) -> StepOutcome:
    attempt = int(incident["attempt"])
    timeline = get_timeline(incident_id)

    diag = diagnose_mod.latest_diagnosis_payload(timeline) or {}
    logs_payload = _latest(timeline, "logs") or {}
    runbooks = docs_for_prompt(incident_id, attempt)

    proposal = propose_mod.run(
        incident_id=incident_id,
        attempt=attempt,
        service=str(incident["service"]),
        container_state=str(logs_payload.get("container_state", "unknown")),
        health_line=str(diag.get("health", "unknown")),
        diagnosis_payload=diag,
        log_lines=[str(x) for x in (logs_payload.get("lines") or [])],
        runbooks=runbooks,
        history=_history(incident_id),
    )

    # 🔴 Voluntary escalation (D20): agent ne khud "mujhe nahi pata" bola.
    #    Kuch execute NAHI hota, koi approval row NAHI banti — ye honest stop
    #    hai, failure nahi. METRICS ise exhausted se ALAG ginta hai.
    if proposal.action == "escalate":
        reason = str(proposal.params.get("reason", "(no reason given)"))
        return _escalate(
            incident_id, State.PROPOSING, "voluntary", reason,
            extra_payload={"via": "agent_choice"},
        )

    create_approval(
        incident_id=incident_id,
        attempt=attempt,
        action=proposal.action,
        params=dict(proposal.params),
        rationale=proposal.reasoning,
    )
    _move(incident_id, State.PROPOSING, State.AWAITING_APPROVAL, "proposal ready")
    return StepOutcome.PAUSED_APPROVAL


def _step_paused(_incident_id: int, _incident: dict[str, Any]) -> StepOutcome:
    # Worker yahan kabhi nahi aana chahta — approve hone pe NAYA job banta hai.
    # Agar aa gaya (purani job requeue hui?), kuch MAT karo: faisla insaan ka hai.
    return StepOutcome.NO_JOB


def _step_executing(incident_id: int, incident: dict[str, Any]) -> StepOutcome:
    attempt = int(incident["attempt"])
    approval_row = get_approval(incident_id, attempt)
    if approval_row is None or approval_row.get("decision") != "approved":
        # Approve endpoint ke bina yahan pahunchna possible nahi hona chahiye
        # (transition table EXECUTING tak sirf AWAITING_APPROVAL se jaane deta
        # hai, aur wo edge approve endpoint chalata hai). Pahunch gaye toh bug.
        return _escalate(
            incident_id, State.EXECUTING, "broker_rejected",
            "no approved approval row found in EXECUTING state — invariant broken",
        )

    def log_fn(kind: str, payload: dict[str, Any]) -> None:
        # Broker ke log-before-execute callbacks → events table. Kind 'execute'
        # hai hi EVENT_KINDS mein; phase payload ke andar distinguish hota hai.
        record_event(incident_id, "execute", payload)

    try:
        result = broker.execute(
            str(approval_row["action"]),
            dict(approval_row["params"] or {}),
            approval=broker.ApprovalRow(
                incident_id=incident_id,
                attempt=attempt,
                action=str(approval_row["action"]),
                params=dict(approval_row["params"] or {}),
                decision="approved",
            ),
            incident_id=incident_id,
            attempt=attempt,
            log=log_fn,
        )
    except broker.RejectedError as exc:
        return _escalate(
            incident_id, State.EXECUTING, "broker_rejected",
            f"L{exc.layer}: {exc.reason}",
            extra_payload={"layer": exc.layer},
        )

    record_event(
        incident_id,
        "execute",
        {
            "phase": "result",
            "ok": result.ok,
            "exit_code": result.exit_code,
            "detail": result.detail,
            "output": result.output[:1000],
        },
    )
    _move(
        incident_id, State.EXECUTING, State.VERIFYING,
        f"action ran (exit={result.exit_code}) — the ORACLE decides next",
    )
    return StepOutcome.CONTINUED


def _step_verifying(incident_id: int, incident: dict[str, Any]) -> StepOutcome:
    attempt = int(incident["attempt"])
    service = str(incident["service"])

    # 🔥 Oracle. 3 probes / 30 s / ~20 rps. ZERO tokens. Yahi verdict likhta hai.
    # Oracle async hai; worker sync thread hai — `asyncio.run` yahan sahi hai
    # (worker FastAPI event loop share nahi karta, alag process hai).
    verdict = asyncio.run(
        is_recovered(settings.shopapp_health_url, f"{settings.shopapp_url}/orders")
    )
    insert_verdict(incident_id=incident_id, attempt=attempt, row=verdict.to_row())
    record_event(
        incident_id,
        "verify",
        {
            "attempt": attempt,
            "sustained_recovered": verdict.sustained_recovered,
            "naive_recovered": verdict.naive_recovered,
            "disagreed": verdict.disagreed,
            "probes": [p.to_payload() for p in verdict.probes],
            "load": verdict.load.to_payload() if verdict.load else None,
        },
    )

    if verdict.sustained_recovered:
        _move(incident_id, State.VERIFYING, State.RECOVERED, "oracle pass — sustained")
        return StepOutcome.TERMINAL

    # Fail → retry budget DB enforce karwao (queries.bump_attempt ka contract).
    new_attempt = bump_attempt(incident_id)
    if new_attempt is None:
        return _escalate(
            incident_id, State.VERIFYING, "attempts_exhausted",
            f"{MAX_ATTEMPTS} attempts failed — the oracle failed every time",
        )
    _move(
        incident_id, State.VERIFYING, State.COLLECTING,
        f"fail — retry #{new_attempt + 1} starts from fresh collection (D19)",
    )
    enqueue_job(incident_id)  # naya job: purana yahin khatam, naya COLLECTING se
    return StepOutcome.TERMINAL  # isi job ke liye terminal; kaam naye job mein


# ═════════════════════════════════════════════════════════════════════════════
# Chhote helpers
# ═════════════════════════════════════════════════════════════════════════════


def _latest(timeline: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    for row in reversed(timeline):
        if row.get("kind") == kind:
            return dict(row.get("payload") or {})
    return {}


def _collected_from(logs_payload: dict[str, Any]):
    """Timeline ke logs-payload ko wapas `CollectedLogs` mein — diagnose.run ka
    input shape. DB round-trip ke through dataclass wapas banti hai, isliye
    steps stateless rehte hain."""
    from oncall.agent.collect import CollectedLogs

    lines = tuple(str(x) for x in (logs_payload.get("lines") or []))
    exit_code = logs_payload.get("exit_code")
    return CollectedLogs(
        service=str(logs_payload.get("service", "shopapp")),
        container_state=str(logs_payload.get("container_state", "unknown")),
        exit_code=None if exit_code is None else int(exit_code),
        lines=lines,
        raw_line_count=int(logs_payload.get("raw_line_count") or len(lines)),
        injection_suspected=bool(logs_payload.get("injection_suspected", False)),
    )


def _history(incident_id: int) -> list[dict[str, Any]]:
    """Pichhle attempts ka saansaf propose ke liye — approvals + verdicts merge.

    🔴 Asli action/params chahiye, "(execution done)" nahi — model ko dikhna
       chahiye ki EXACTLY kya try hua tha aur oracle ne kya bola. Sirf "fail
       hua" likhne se model wahi fix dobara propose kar sakta."""
    history: list[dict[str, Any]] = []
    for attempt_no in range(MAX_ATTEMPTS):
        approval = get_approval(incident_id, attempt_no)
        if approval is None or approval.get("decision") is None:
            continue
        verdict = None
        for v in list_verdicts(incident_id):
            if int(v["attempt"]) == attempt_no:
                verdict = v
                break
        history.append(
            {
                "attempt": attempt_no,
                "action": str(approval["action"]),
                "params": dict(approval.get("params") or {}),
                "sustained": bool(verdict["sustained_recovered"]) if verdict else False,
            }
        )
    return history
