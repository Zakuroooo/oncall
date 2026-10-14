"""Hybrid retrieval — pgvector cosine + Postgres full-text, RRF se fuse.

────────────────────────────────────────────────────────────────────────────
Query text KYA hai (aur kya nahi)
────────────────────────────────────────────────────────────────────────────
Query = **diagnosis ka summary** (+ evidence lines ka pehla hissa). RAW LOGS
nahi. Kyun: logs mein timestamps, PIDs, hex addresses hote hain jo semantic
space mein noise hain. Diagnosis natural language hai — vector search uske liye
bana hai; aur error ke exact tokens (jo keyword search chahta hai) diagnosis ki
evidence array mein already aate hain (`diagnose.py` ka `evidence` field).

────────────────────────────────────────────────────────────────────────────
🔒 Is file ke do "nahi" — dono security/metric rules hain
────────────────────────────────────────────────────────────────────────────
1. `SELECT` mein sirf `RUNBOOK_COLUMNS` (`db/queries.py`) — answer-key wala
   column aur embedding column is list mein NAHI hain, jaan-boojh kar. Star
   select answer key ko bina naam liye le aata.
2. Fusion ka output DB mein likha jaata hai (`retrievals` table) — rank/score
   sab arithmetic hai, LLM ki opinion nahi. Isliye N4 gate G1 paas karta hai.
"""

from __future__ import annotations

from typing import Any

from oncall.config import load_settings
from oncall.db.pool import get_conn
from oncall.db.queries import RUNBOOK_COLUMNS, insert_retrievals, record_event
from oncall.retrieval.embed import embed
from oncall.retrieval.fuse import fused

#: Har method apni list mein itne candidates deta hai. top_k=3 FINAL output ka
#: number hai (`ONCALL_TOP_K`); candidate pool usse bada hona chahiye taaki
#: fusion ke paas choice rahe.
_CANDIDATES_PER_METHOD = 10

#: Propose prompt mein runbook body ka truncation (~450 tokens). Poora body
#: bhejna 8K TPM tod deta (`TOKEN_BUDGET.md` §3 Call 2 table).
BODY_CHARS_FOR_PROMPT = 1800


def _vector_literal(vec: list[float]) -> str:
    """pgvector ka text format: `'[0.1,0.2,...]'`. Param ki tarah jaata hai,
    string-join SQL mein nahi — `%s::vector` cast SQL ke ANDAR hai."""
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


def dense(query_text: str, k: int = _CANDIDATES_PER_METHOD) -> list[dict[str, Any]]:
    """Cosine distance ascending. `<=>` pgvector operator hai."""
    vec = embed(query_text)
    cols = ", ".join(RUNBOOK_COLUMNS)
    sql = (
        f"SELECT {cols}, embedding <=> %s::vector AS distance "
        "FROM runbooks WHERE embedding IS NOT NULL "
        f"ORDER BY embedding <=> %s::vector LIMIT {int(k)}"
    )
    literal = _vector_literal(vec)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (literal, literal))
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def sparse(query_text: str, k: int = _CANDIDATES_PER_METHOD) -> list[dict[str, Any]]:
    """Postgres full-text search over the runbooks, OR-ed across query terms.

    🔴 Why OR and not `plainto_tsquery` as-is: `plainto_tsquery` ANDs every stem.
       The query here is a whole diagnosis (2-3 sentences + evidence), so AND
       demanded that one runbook chunk contain all ~30 stems — no chunk ever did.
       Every incident up to 2026-10-15 logged `keyword_candidates: 0`, which made
       "hybrid" retrieval vector-only in practice. We still let `plainto_tsquery`
       parse and stem the text (it never raises on user input), then swap its
       `&` for `|`, and let `ts_rank` (normalisation 1 = divide by log(doc
       length)) order chunks by how many query terms they share.
    """
    cols = ", ".join(RUNBOOK_COLUMNS)
    sql = (
        f"SELECT {cols}, ts_rank(tsv, q, 1) AS tsr "
        "FROM runbooks, "
        "to_tsquery('english', replace(plainto_tsquery('english', %s)::text, ' & ', ' | ')) AS q "
        "WHERE q::text <> '' AND tsv @@ q "
        f"ORDER BY tsr DESC LIMIT {int(k)}"
    )
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (query_text,))
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def hybrid(
    *,
    incident_id: int,
    attempt: int,
    query_text: str,
) -> list[dict[str, Any]]:
    """Dono searches → fusion → `retrievals` rows + 'retrieve' event.

    Returns:
        Final ranked docs (top_k), har dict mein `rank`, `rrf_score`,
        `vector_rank`, `keyword_rank`, truncated `body`.

    🔴 0 results VALID hai — corpus khaali ho sakta hai ya query kisi cheez se
       match na kare. Exception nahi uthalta; propose ko "(no runbooks found)"
       context milta hai. Ye edge RETRIEVING → PROPOSING edge ka hi reason hai.
    """
    settings = load_settings()

    # Evidence lines query ko exact tokens deti hain — diagnosis summary ko
    # paraphrase power. Dono ek saath = hybrid ki poori wajah.
    v_rows = dense(query_text)
    s_rows = sparse(query_text)

    ranked = fused(
        [str(r["id"]) for r in v_rows],
        [str(r["id"]) for r in s_rows],
    )

    bodies = {str(r["id"]): str(r.get("body") or "") for r in (*v_rows, *s_rows)}
    titles = {str(r["id"]): str(r.get("title") or "") for r in (*v_rows, *s_rows)}
    sources = {str(r["id"]): str(r.get("source") or "") for r in (*v_rows, *s_rows)}

    out: list[dict[str, Any]] = []
    db_rows: list[dict[str, Any]] = []
    for doc_id, score, method_ranks in ranked[: max(1, settings.top_k)]:
        row = {
            "runbook_id": doc_id,
            "title": titles.get(doc_id, ""),
            "source": sources.get(doc_id, ""),
            "body": bodies.get(doc_id, "")[:BODY_CHARS_FOR_PROMPT],
            "rank": len(out) + 1,
            "rrf_score": round(score, 6),
            "vector_rank": method_ranks.get("vector"),
            "keyword_rank": method_ranks.get("keyword"),
        }
        out.append(row)
        db_rows.append(row)

    inserted = insert_retrievals(incident_id, attempt, db_rows)
    record_event(
        incident_id,
        "retrieve",
        {
            "query_chars": len(query_text),
            "vector_candidates": len(v_rows),
            "keyword_candidates": len(s_rows),
            "kept": inserted,
            "top_runbook_id": out[0]["runbook_id"] if out else None,
            "top_rrf_score": out[0]["rrf_score"] if out else None,
        },
    )
    return out


def docs_for_prompt(incident_id: int, attempt: int) -> list[dict[str, Any]]:
    """`retrievals` ke ranks + `runbooks` ke bodies wapas jodo — propose ka input.

    🔴 Ye function isliye hai ki PROPOSING step (jo naya job mein chal sakta
       hai) fusion ka output DB se reconstruct kar sake. Rank order `retrievals`
       table se aata hai, body/title `RUNBOOK_COLUMNS` se — answer-key column
       yahan bhi nahi aati.
    """
    from oncall.db.queries import get_retrievals, get_runbook

    out: list[dict[str, Any]] = []
    for r in get_retrievals(incident_id, attempt):
        rb = get_runbook(str(r["runbook_id"]))
        if rb is None:
            # Retrieval ke baad runbook delete ho gaya (corpus re-ingest)? Row
            # skip hoti hai, crash nahi — citation missing dikhega, kaam chalega.
            continue
        out.append(
            {
                "runbook_id": str(r["runbook_id"]),
                "title": str(rb.get("title") or ""),
                "source": str(rb.get("source") or ""),
                "body": str(rb.get("body") or "")[:BODY_CHARS_FOR_PROMPT],
                "rank": int(r["rank"]),
                "rrf_score": float(r["rrf_score"]),
                "vector_rank": r["vector_rank"],
                "keyword_rank": r["keyword_rank"],
            }
        )
    return out
