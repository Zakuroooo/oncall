"""RRF — Reciprocal Rank Fusion. Do ranked lists → ek merged list.

────────────────────────────────────────────────────────────────────────────
Problem jo ye file solve karti hai (D11)
────────────────────────────────────────────────────────────────────────────
Vector search cosine deta hai (0 se 1). Keyword search ts_rank deta hai
(unbounded). `0.82` vs `4.7` — kaun bada? Ye compare karne layak HI NAHI hai.
Scores ko normalise karna ek aur problem hoti (dono ka distribution alag hai).

RRF ka jawaab: **scores bhool jao, sirf rank rakho.**

    score(doc) = Σ over lists  1 / (K + rank_in_that_list)

Doc A: vector #1 + keyword #3  →  1/61 + 1/63 ≈ 0.0323
Doc B: sirf vector #1          →  1/61         ≈ 0.0164
A jeeta. Logic: jo cheez DONO tareeko se upar aayi, uspe zyada bharosa.

🔴 K=60 ek standard constant hai (Cormack et al. 2009), tuned magic nahi.
   Chhota K (jaise 1) top-1 ko haraam-khor bana deta; bada K (1000) ranking
   ko flat. 60 sab lists ke liye tested default hai — isliye constant,
   env var NAHI (config nahi, contract).
"""

from __future__ import annotations

from typing import Iterable

#: Cormack et al. 2009 ka standard k. Upar wale comment ka subject.
K = 60


def rrf(*ranked_lists: Iterable[str]) -> dict[str, float]:
    """Har list mein rank 1-based hai. Same id do list mein → score JUDEGA.

    Returns:
        `{doc_id: score}` — sorted NAHI. Sorting caller karta hai kyunki
        ties ka tie-breaker (stable order) caller ki choice hoti hai.
    """
    scores: dict[str, float] = {}
    for ids in ranked_lists:
        seen_in_this_list: set[str] = set()
        for rank, doc_id in enumerate(ids, start=1):
            if doc_id in seen_in_this_list:
                # Ek hi list mein duplicate id = upstream bug (SQL DISTINCT
                # ki galti). Score dobara jodna result inflate karta — isliye
                # chup-chaap ignore nahi, loud skip.
                raise ValueError(f"ek ranked list mein id dobara aa gaya: {doc_id!r}")
            seen_in_this_list.add(doc_id)
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (K + rank)
    return scores


def fused(
    *ranked_lists: list[str],
) -> list[tuple[str, float, dict[str, int | None]]]:
    """Fusion + per-method ranks — `retrievals` table ke columns yahi hain.

    Returns:
        `[(doc_id, rrf_score, {"vector": rank|None, "keyword": rank|None})]`,
        best pehle. Tie-break: pehli list ka rank, phir insertion order —
        deterministic hona zaruri hai warna same input do output ban jaata hai
        aur eval runs reproducible nahi rehte.
    """
    scores = rrf(*ranked_lists)
    positions: list[dict[str, int]] = []
    for ids in ranked_lists:
        positions.append({doc_id: rank for rank, doc_id in enumerate(ids, start=1)})

    def _sort_key(item: tuple[str, float]) -> tuple[float, ...]:
        doc_id, score = item
        # Primary: RRF score (zyada behtar). Tie-breaker: har list ka rank
        # (chhota behtar) — isliye negative positions.
        tiebreak = tuple(-pos.get(doc_id, 10_000) for pos in positions)
        return (-score, *tiebreak)

    out: list[tuple[str, float, dict[str, int | None]]] = []
    for doc_id, score in sorted(scores.items(), key=_sort_key):
        v_rank = positions[0].get(doc_id) if len(positions) > 0 else None
        k_rank = positions[1].get(doc_id) if len(positions) > 1 else None
        out.append((doc_id, score, {"vector": v_rank, "keyword": k_rank}))
    return out
