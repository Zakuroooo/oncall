"""🔒 Approval gate ka HTTP hissa — state badalta hai, EXECUTE NAHI karta.

────────────────────────────────────────────────────────────────────────────
🔴 Ye file project ki safety boundary ka PUBLIC face hai
────────────────────────────────────────────────────────────────────────────
Approve endpoint sirf TEEN kaam karta hai:
  1. `decide_approval()` — DB row ko decision deta (ek hi click jeetta hai,
     doosra `None` milta hai → idempotent, `queries.py` §6 ka contract)
  2. State transition AWAITING_APPROVAL → EXECUTING (ya ESCALATED on reject)
  3. Ek NAYA job enqueue karta

Khud broker nahi chalata, khud Docker nahi chhoota. Execution hamesha worker
ke through hoti hai — isliye approve karne ke baad bhi process crash ho jaaye
toh incident kho nahi jaata: state aur job dono DB mein hain.

Idempotency (TDD §8a): double-click pe doosri request stored result dekhti hai
(200 + "already_decided"), action dobara NAHI chalti.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from oncall.db.queries import (
    advance_state,
    close_incident,
    decide_approval,
    enqueue_job,
    get_approval,
    get_incident,
    record_event,
)
from oncall.agent.states import State

router = APIRouter(prefix="/api/incidents", tags=["approval"])


class DecisionBody(BaseModel):
    reason: str = ""


def _load_incident_or_404(incident_id: int) -> dict[str, Any]:
    incident = get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"incident {incident_id} not found")
    return incident


@router.post("/{incident_id}/approve")
def approve(incident_id: int) -> dict[str, Any]:
    """Proposal manzoor. `AWAITING_APPROVAL → EXECUTING` + naya job."""
    incident = _load_incident_or_404(incident_id)
    if str(incident["state"]) != State.AWAITING_APPROVAL.value:
        raise HTTPException(
            status_code=409,
        detail=f"incident is not in {State.AWAITING_APPROVAL.value} (currently: {incident['state']})",
        )

    attempt = int(incident["attempt"])
    row = decide_approval(
        incident_id=incident_id, attempt=attempt, decision="approved"
    )
    if row is None:
        # Pehla click already jeet chuka — stored result wapas, execute NAHI.
        return {"status": "already_decided", **_current(incident_id)}

    record_event(
        incident_id, "approval",
        {"decision": "approved", "action": row["action"], "attempt": attempt},
    )
    advance_state(
        incident_id,
        frm=State.AWAITING_APPROVAL.value,
        to=State.EXECUTING.value,
        why="approved by human operator",
    )
    enqueue_job(incident_id)
    return {"status": "ok", **_current(incident_id)}


@router.post("/{incident_id}/reject")
def reject(incident_id: int, body: DecisionBody | None = None) -> dict[str, Any]:
    """Insaan ne mana kiya → ESCALATED (`human_rejected`). Retry NAHI hota —
    reject ka matlab agent ki soch par trust khatam (D20). Wo bhi ek data point
    hai: METRICS N5 isi se banta hai."""
    incident = _load_incident_or_404(incident_id)
    if str(incident["state"]) != State.AWAITING_APPROVAL.value:
        raise HTTPException(
            status_code=409,
        detail=f"incident is not in {State.AWAITING_APPROVAL.value} (currently: {incident['state']})",
        )

    attempt = int(incident["attempt"])
    reason = (body.reason if body else "").strip() or "(no reason given)"
    row = decide_approval(
        incident_id=incident_id, attempt=attempt, decision="rejected"
    )
    if row is None:
        return {"status": "already_decided", **_current(incident_id)}

    record_event(
        incident_id, "escalate",
        {"reason": "human_rejected", "detail": reason[:500], "via": "operator"},
    )
    close_incident(
        incident_id,
        frm=State.AWAITING_APPROVAL.value,
        to=State.ESCALATED.value,
        why="human_rejected",
    )
    return {"status": "ok", **_current(incident_id)}


@router.get("/{incident_id}/proposal")
def proposal(incident_id: int) -> dict[str, Any]:
    """Pending approval card ka data — diagnosis + action + runbook citation."""
    incident = _load_incident_or_404(incident_id)
    attempt = int(incident["attempt"])
    approval = get_approval(incident_id, attempt)
    if approval is None:
        raise HTTPException(status_code=404, detail="no proposal was created for this attempt")
    return {
        "approval": approval,
        "incident": incident,
        **_current(incident_id),
    }


def _current(incident_id: int) -> dict[str, Any]:
    fresh = get_incident(incident_id)
    approval = get_approval(incident_id, int(fresh["attempt"])) if fresh else None
    return {
        "incident": fresh,
        "approval": approval,
    }
