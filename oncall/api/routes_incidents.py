"""Incident list + detail. UI ka main read path.

Detail mein sab kuch ek response mein: incident row, poora timeline, verdicts,
retrievals, token usage. UI pehle list se id uthata hai, phir yahan aata hai —
aur SSE live updates ke liye `routes_stream.py` use karta hai.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from oncall.db.queries import (
    get_incident,
    get_timeline,
    list_incidents,
    list_retrievals,
    list_verdicts,
    token_usage,
)

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


@router.get("")
def list_all(
    limit: int = Query(default=50, ge=1, le=200),
    open_only: bool = False,
) -> list[dict[str, Any]]:
    """Naye pehle. `open_only=true` partial index (`incidents_open_idx`) use karta hai."""
    return list_incidents(limit=limit, open_only=open_only)


@router.get("/{incident_id}")
def detail(incident_id: int) -> dict[str, Any]:
    """Poora trace. UI isi se render karti hai; SSE sirf LIVE hissa hai."""
    incident = get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"incident {incident_id} not found")
    return {
        "incident": incident,
        "timeline": get_timeline(incident_id),
        "verdicts": list_verdicts(incident_id),
        "retrievals": list_retrievals(incident_id),
        "tokens": token_usage(incident_id),
    }
