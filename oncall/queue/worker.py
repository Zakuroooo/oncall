"""Queue worker — Postgres hi humari queue hai (D10). Celery nahi, Redis nahi.

────────────────────────────────────────────────────────────────────────────
Ek line mein
────────────────────────────────────────────────────────────────────────────
Loop: job claim karo (`FOR UPDATE SKIP LOCKED`) → agent steps chalao jab tak
pause/terminal na ho → job done mark karo. Exception? `fail_job` khud decide
karta hai retry ya dead (`queries.py` ka contract).

────────────────────────────────────────────────────────────────────────────
🔴 Do sweeps isi loop mein hain, alag processes NAHI
────────────────────────────────────────────────────────────────────────────
Har ~30s mein (jab queue khaali hoti hai):
  1. REAPER — atki hui `running` jobs wapas `queued`. Mara hua worker apni
     job ke saath apna Postgres lock bhi le jaata hai; sirf `locked_at`
     bachta hai. Usi column ki clock pe ye sweep chalta hai.
  2. 5-MIN RECHECK (R10) — RECOVERED incidents ko 5 minute baad ek aur probe.
     Oracle ko bhi measure kiya jaata hai: agar 5 min baad service giri, toh
     mera 30-second window chhota tha — aur wo tripwire event mein dikhega.

Alag process banane the toh teen processes ka jivan-sanchalan karna padta
(8 GB machine). Ek loop, teen kaam — aur har kaam idempotent hai.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from typing import Any

from oncall.agent.loop import StepOutcome, run_until_pause_or_terminal
from oncall.config import settings
from oncall.db.pool import ping
from oncall.db.queries import (
    claim_job,
    fail_job,
    finish_job,
    get_incident,
    list_recheck_due,
    queue_depth,
    reap_stale_jobs,
    record_event,
)

_POLL_INTERVAL_S = 1.0
_SWEEP_EVERY_S = 30.0

#: Recheck probe ka timeout — ye oracle nahi, tripwire hai. Chhota rakha.
_RECHECK_TIMEOUT_S = 6.0


def worker_id() -> str:
    """`hostname:pid` — reaper ke logs mein 'kaun mara tha' yahi batata hai."""
    return f"{socket.gethostname()}:{os.getpid()}"


def _process_one(job: dict[str, Any]) -> str:
    """Ek claimed job ka poora kaam. Returns final status string."""
    incident_id = int(job["incident_id"])
    incident = get_incident(incident_id)
    if incident is None:
        # Job bacha hua hai par incident gayab (manual DELETE?). Dead mark
        # karo — requeue karte toh hamesha ke liye loop ban jaata.
        fail_job(int(job["id"]), f"incident {incident_id} not found", max_attempts=0)
        return "failed"

    state = str(incident["state"])
    if state in ("RECOVERED", "ESCALATED"):
        # Purani job requeue ho gayi thi aur beech mein incident terminal pe
        # pahunch gaya. Kuch MAT karo — job done, silently.
        finish_job(int(job["id"]))
        return "done"

    outcome = run_until_pause_or_terminal(incident_id)
    if outcome in (StepOutcome.PAUSED_APPROVAL, StepOutcome.TERMINAL):
        finish_job(int(job["id"]))
        return "done"
    if outcome is StepOutcome.NO_JOB:
        # AWAITING_APPROVAL pe purani job dobara aa gayi — approve ka wait
        # failure nahi hai. Job done; faisla insaan karega, phir naya job.
        finish_job(int(job["id"]))
        return "done"
    raise RuntimeError(f"unexpected step outcome: {outcome}")  # pragma: no cover


def _run_sweeps(last_sweep: float) -> float:
    """Reaper + 5-min recheck. Returns naya last-sweep timestamp."""
    now = time.monotonic()
    if now - last_sweep < _SWEEP_EVERY_S:
        return last_sweep

    reaped = reap_stale_jobs()
    for row in reaped:
        print(
            f"♻️  reaped job {row['id']} (incident {row['incident_id']}, "
            f"owner {row['locked_by']}) — lock stale tha",
            file=sys.stderr,
        )

    _run_rechecks()

    return now


def _run_rechecks() -> None:
    """RECOVERED incidents, 5–60 min purane, jinhe recheck nahi mila (R10).

    🔴 Ye probe oracle NAHI hai — verdict table ko NAHI chhoota. Sirf ek event
       likhta hai `{recheck_5min_ok}` jisse METRICS ka tripwire compute hota
       hai ('30 s window chhota tha kya?'). Verdict immutable hai."""
    import httpx

    due = list_recheck_due()
    if not due:
        return
    url = settings.shopapp_health_url
    for row in due:
        try:
            resp = httpx.get(url, timeout=_RECHECK_TIMEOUT_S)
            ok = resp.status_code == 200
            detail = f"HTTP {resp.status_code}"
        except Exception as exc:  # noqa: BLE001 - failure yahan DATA hai
            ok = False
            detail = type(exc).__name__
        record_event(
            int(row["incident_id"]),
            "verify",
            {"recheck_5min_ok": ok, "detail": detail},
        )
        print(
            f"🔁 recheck incident {row['incident_id']}: "
            f"{'ok' if ok else 'FAIL'} ({detail})",
            file=sys.stderr,
        )


def run_forever(*, poll_interval_s: float = _POLL_INTERVAL_S) -> None:  # pragma: no cover
    """Main loop. `python -m oncall.queue.worker` isi ko chalata hai."""
    wid = worker_id()
    print(f"worker {wid} shuru — DB: {_db_state()}", file=sys.stderr)
    last_sweep = time.monotonic()

    while True:
        last_sweep = _run_sweeps(last_sweep)
        try:
            job = claim_job(wid)
        except Exception as exc:  # noqa: BLE001 - DB down pe loop na mare
            print(f"claim fail: {type(exc).__name__}: {exc}", file=sys.stderr)
            time.sleep(max(poll_interval_s * 10, 10.0))
            continue

        if job is None:
            time.sleep(poll_interval_s)
            continue

        started = time.monotonic()
        try:
            status = _process_one(job)
        except Exception as exc:  # noqa: BLE001 - job-level fail ≠ process-level
            err = f"{type(exc).__name__}: {str(exc)[:500]}"
            new_status = fail_job(int(job["id"]), err)
            print(
                f"job {job['id']} FAIL ({err}) → {new_status}",
                file=sys.stderr,
            )
        else:
            print(
                f"job {job['id']} (incident {job['incident_id']}) → {status} "
                f"in {time.monotonic() - started:.1f}s",
                file=sys.stderr,
            )


def _db_state() -> str:  # pragma: no cover - boot log only
    ok, reason = ping()
    depth = {}
    try:
        depth = queue_depth()
    except Exception:
        pass
    return f"{'reachable' if ok else 'UNREACHABLE'} ({reason}), queue={depth}"


if __name__ == "__main__":  # pragma: no cover
    run_forever()
