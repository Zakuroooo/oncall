"""Stats endpoints — PRD §5 Screen 6 ka JSON roop.

🔴 Jo yahan NAHI hai, wahi is file ki sabse badi feature: koi aggregate
   auto-fix row. Har rate per fault class + rag-arm aata hai, Wilson interval
   ke saath (`wilson.describe`). Bare percentage kabhi akela nahi jaata — G3
   gate. UI pe aggregate row exist hi nahi karti (`METRICS.md` §7).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from oncall.metrics import queries as m
from oncall.metrics.wilson import describe

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("")
def stats() -> dict[str, Any]:
    """Saare N1–N7 jo abhi measure ho sakte hain, ek response mein.

    Har block `[]` aa sakta hai (koi incident nahi) — khaali table jhoothi
    table se behtar (`METRICS.md` ka apna rule)."""
    return {
        "autofix": _with_ci(m.autofix_by_class()),
        "mttr": m.mttr_by_class(),
        "escalations": m.escalations_by_class(),
        "rejection": m.rejection_by_class(),
        "tokens": m.tokens_by_class(),
        "retrieval_hits": m.hit_rate_by_class(),
        "ablation_delta": m.ablation_delta_by_class(),
        "disagreements": m.disagreements_by_class(),
    }


def _with_ci(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """N1 rows mein Wilson interval + underpowered badge jodo."""
    out = []
    for r in rows:
        n = int(r.get("n") or 0)
        recovered = int(r.get("recovered") or 0)
        out.append({**r, **describe(recovered, n)})
    return out
