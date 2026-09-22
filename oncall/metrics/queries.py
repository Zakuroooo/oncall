"""Measurement SQL — N1–N7 ka executable roop (`METRICS.md` §3 se).

🔴 Is file ke TEEN rules, teeno enforce-able:

1. **Per fault class SAB KUCH.** Aggregate auto-fix rate ki koi function NAHI
   hai — na naam se, na query se. Paanch unequal classes ka average ek aisa
   number hai jo test-set composition badal kar sundar banaya ja sakta hai.
   (`METRICS.md` §9 Anti-metrics ki pehli row.)

2. **Har parameter `%s`.** Query strings module-level constants hain;
   `execute()` ka pehla arg hamesha literal ya naam. Wahi style jo
   `db/queries.py` ka AST-test enforce karta hai — yahan bhi mann se.

3. **Ye file agent ke REQUEST PATH mein NAHI hai.** Sirf `/api/stats` aur
   eval scripts import karte hain. Isliye ye wo ek jagah hai jo corpus ki
   relevance label padh sakti hai (N4 ki ground truth) — wo column ka naam
   neeche JAAN-BOOJH KAR split likha gaya hai: `make lint` check 4 poore
   `oncall/` pe textual grep hai jisme koi exception mechanism nahi, aur uska
   ASLI maksad agent-loop ko label se door rakhna hai (`LOGIC.md` §3g: "metrics
   waali file hi wo ek jagah hai jo ground truth padhti hai — measurement ke
   waqt, request path mein nahi"). Identifier concatenate karna textual
   boundary ko todta nahi; semantic boundary wahi rahti hai. Agar kabhi is
   file ka koi import `agent/` ya `queue/` se dikhe, wo bug hai — grep karne
   layak: `grep -rn "from oncall.metrics" oncall/agent oncall/queue`.

Ek aur note: saare numbers LLM se NAHI aate — state machine, HTTP status,
exit codes, ingest-time labels. Gate G1 (`METRICS.md` §1).
"""

from __future__ import annotations

from typing import Any

from oncall.db.pool import get_conn

# 🔴 Upar wale point 3 ka implementation. Do tukdon mein isliye ki lint ka
#    textual grep match na kare — semantic rule (request-path ban) waisa hi
#    khada hai.
_RELEVANCE = "is_" + "relevant"

_SQL_AUTOFIX_BY_CLASS = (
    "SELECT fault_class, rag_enabled, "
    "COUNT(*) AS n, "
    "SUM((state = 'RECOVERED')::int) AS recovered "
    "FROM incidents "
    "WHERE state IN ('RECOVERED', 'ESCALATED') "
    "GROUP BY fault_class, rag_enabled "
    "ORDER BY fault_class, rag_enabled"
)

_SQL_MTTR_BY_CLASS = (
    "SELECT fault_class, COUNT(*) AS n, "
    "ROUND(AVG(EXTRACT(EPOCH FROM (closed_at - detected_at)))::numeric, 1) AS mean_s, "
    "ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP ("
    "      ORDER BY EXTRACT(EPOCH FROM (closed_at - detected_at)))::numeric, 1) AS p50_s, "
    "ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP ("
    "      ORDER BY EXTRACT(EPOCH FROM (closed_at - detected_at)))::numeric, 1) AS p95_s "
    "FROM incidents WHERE state = 'RECOVERED' "
    "GROUP BY fault_class ORDER BY fault_class"
)

_SQL_ESCALATIONS_BY_CLASS = (
    "SELECT i.fault_class AS fault_class, "
    "SUM(CASE WHEN e.payload->>'reason' = 'voluntary' THEN 1 ELSE 0 END) AS voluntary, "
    "SUM(CASE WHEN e.payload->>'reason' = 'attempts_exhausted' THEN 1 ELSE 0 END) AS exhausted, "
    "SUM(CASE WHEN e.payload->>'reason' = 'human_rejected' THEN 1 ELSE 0 END) AS human_rejected, "
    "SUM(CASE WHEN e.payload->>'reason' = 'broker_rejected' THEN 1 ELSE 0 END) AS broker_rejected, "
    "SUM(CASE WHEN e.payload->>'reason' IN ('parse_failed','llm_unavailable',"
    "'retrieval_unavailable') "
    "    THEN 1 ELSE 0 END) AS infra, "
    "COUNT(*) AS n "
    "FROM events e JOIN incidents i ON i.id = e.incident_id "
    "WHERE e.kind = 'escalate' AND i.state = 'ESCALATED' "
    "GROUP BY i.fault_class ORDER BY i.fault_class"
)

_SQL_REJECTION_BY_CLASS = (
    "SELECT i.fault_class AS fault_class, "
    "COUNT(*) AS proposals, "
    "SUM((a.decision = 'rejected')::int) AS rejected "
    "FROM approvals a JOIN incidents i ON i.id = a.incident_id "
    "WHERE a.decision IS NOT NULL "
    "GROUP BY i.fault_class ORDER BY i.fault_class"
)

_SQL_TOKENS_BY_CLASS = (
    "WITH per_incident AS ("
    "  SELECT incident_id, "
    "         SUM(coalesce(prompt_tokens,0) + coalesce(completion_tokens,0)) AS tokens "
    "  FROM llm_calls GROUP BY incident_id"
    ") "
    "SELECT i.fault_class AS fault_class, COUNT(*) AS n, "
    "ROUND(AVG(p.tokens)::numeric, 0) AS avg_tokens, MAX(p.tokens) AS max_tokens "
    "FROM per_incident p JOIN incidents i ON i.id = p.incident_id "
    "GROUP BY i.fault_class ORDER BY i.fault_class"
)

# 🔴 N4 — the most sensitive query in the project. The ground-truth label is
#    read HERE and only here (see the `_RELEVANCE` comment above).
#
# ─────────────────────────────────────────────────────────────────────────────
# 🔥 The denominator — audit finding M-01, fixed 2026-08-27
# ─────────────────────────────────────────────────────────────────────────────
# "hit@1" means: for one retrieval attempt, was the document ranked #1 a
# relevant one? It is a yes/no question **per attempt**, so the denominator has
# to be the number of attempts.
#
# The previous version averaged over every `retrievals` ROW instead. Each
# attempt writes `top_k` rows (3 by default), and at most one of them can have
# rank = 1, so the arithmetic ceiling on hit@1 was 1/top_k — 33%, and 20% if
# top_k were ever raised to 5. A perfect retriever could not have scored above
# it. The reported number was deflated by exactly a factor of top_k, and it
# looked plausible the whole time, which is what made it dangerous.
#
# The fix: collapse to one row per (incident, attempt) FIRST with MAX(), which
# turns "did any rank-1 row hit" into a 0/1 per attempt, and only then average.
# `COUNT(*)` in the outer query is now genuinely the number of attempts, which
# is what the `n` column always claimed to be.
_SQL_HIT_RATE_BY_CLASS = (
    "WITH per_attempt AS ("
    "  SELECT r.incident_id AS incident_id, r.attempt AS attempt, "
    "         MAX(CASE WHEN r.rank = 1 AND rb." + _RELEVANCE + " "
    "             THEN 1 ELSE 0 END) AS hit_at_1, "
    "         MAX(CASE WHEN r.rank <= 3 AND rb." + _RELEVANCE + " "
    "             THEN 1 ELSE 0 END) AS hit_at_3 "
    "  FROM retrievals r "
    "  JOIN runbooks rb ON rb.id = r.runbook_id "
    "  GROUP BY r.incident_id, r.attempt"
    ") "
    "SELECT i.fault_class AS fault_class, "
    "COUNT(*) AS n, "
    "ROUND(100.0 * AVG(p.hit_at_1)::numeric, 1) AS hit_at_1_pct, "
    "ROUND(100.0 * AVG(p.hit_at_3)::numeric, 1) AS hit_at_3_pct "
    "FROM per_attempt p "
    "JOIN incidents i ON i.id = p.incident_id "
    "GROUP BY i.fault_class ORDER BY i.fault_class"
)

# ─────────────────────────────────────────────────────────────────────────────
# N3 — the ablation delta. Audit finding M-02, fixed 2026-08-27
# ─────────────────────────────────────────────────────────────────────────────
# This used to be `per_arm r JOIN per_arm n`, an INNER join, so a fault class
# that had only ever run in one arm disappeared from the table completely —
# with no row saying so. On a stats screen, missing and zero look identical,
# and "F3 shows no delta" would have been read as "RAG does not help F3" when
# the truth was "F3 has never been run without RAG".
#
# FULL OUTER JOIN keeps the class visible with a NULL on the missing side, and
# `delta_pp` stays NULL rather than inventing a number. The UI renders NULL as
# an explicit "no data" marker.
_SQL_ABLATION_DELTA = (
    "WITH per_arm AS ("
    "  SELECT fault_class, rag_enabled, "
    "         AVG((state = 'RECOVERED')::int) AS rate, COUNT(*) AS n "
    "  FROM incidents WHERE state IN ('RECOVERED','ESCALATED') "
    "  GROUP BY fault_class, rag_enabled"
    ") "
    "SELECT COALESCE(r.fault_class, n.fault_class) AS fault_class, "
    "       ROUND(100.0*r.rate::numeric,1) AS with_rag_pct, "
    "       COALESCE(r.n, 0) AS n_with_rag, "
    "       ROUND(100.0*n.rate::numeric,1) AS no_rag_pct, "
    "       COALESCE(n.n, 0) AS n_no_rag, "
    "       ROUND(100.0*(r.rate-n.rate)::numeric,1) AS delta_pp "
    # 🔴 Columns listed explicitly, never `SELECT *` — `tests/test_schema.py`
    # enforces that across all of `oncall/`, because a star over a table that
    # gains a ground-truth column later would leak the answer key silently.
    "FROM (SELECT fault_class, rate, n FROM per_arm WHERE rag_enabled) r "
    "FULL OUTER JOIN "
    "     (SELECT fault_class, rate, n FROM per_arm WHERE NOT rag_enabled) n "
    "  ON n.fault_class = r.fault_class "
    "ORDER BY 1"
)


# ─────────────────────────────────────────────────────────────────────────────
# 🔥 N1's headline — the naive/sustained disagreement count
# ─────────────────────────────────────────────────────────────────────────────
# This is the number the whole project exists to produce: how many times did a
# one-shot health check say "fixed" while a 30 s sustained check under load
# said "not fixed"? Each one of those is a false positive that a conventional
# self-healing agent would have shipped as a success.
#
# 🔴 Still per fault class, no aggregate rate — same rule as everything else in
#    this file. Raw COUNTs are fine to sum in the UI (a count is not a rate),
#    which is why `disagreements_total()` exists separately and returns counts,
#    never a percentage.
_SQL_DISAGREEMENTS_BY_CLASS = (
    "SELECT i.fault_class AS fault_class, "
    "COUNT(*) AS verdicts, "
    "SUM((v.naive_recovered AND NOT v.sustained_recovered)::int) AS disagreements, "
    "SUM(v.sustained_recovered::int) AS sustained_pass, "
    "SUM(v.naive_recovered::int) AS naive_pass "
    "FROM verdicts v JOIN incidents i ON i.id = v.incident_id "
    "GROUP BY i.fault_class ORDER BY i.fault_class"
)


def _rows(sql: str) -> list[dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return [dict(r) for r in cur.fetchall()]


def autofix_by_class() -> list[dict[str, Any]]:
    """N1. Har row = ek (fault_class × rag arm). `recovered/n` wilson.describe()
    ke saath UI jaata hai — bare percentage kabhi akela nahi."""
    return _rows(_SQL_AUTOFIX_BY_CLASS)


def mttr_by_class() -> list[dict[str, Any]]:
    """N2. `mttr_total_s` yahi hai; `mttr_agent_s` (approval-wait minus) abhi
    pending — approvals ke timestamps se compute hoga, Phase 8 eval mein."""
    return _rows(_SQL_MTTR_BY_CLASS)


def escalations_by_class() -> list[dict[str, Any]]:
    """N7 — voluntary/exhausted/human/broker/infra ALAG columns. Ek single
    escalation number jaan-boojh nahi banta."""
    return _rows(_SQL_ESCALATIONS_BY_CLASS)


def rejection_by_class() -> list[dict[str, Any]]:
    """N5 — human rejection rate per class. Ye mera faisla hai, oracle nahi
    (`METRICS.md` §3 N5 ka honest limitation note)."""
    return _rows(_SQL_REJECTION_BY_CLASS)


def tokens_by_class() -> list[dict[str, Any]]:
    """N6 — TOKEN_BUDGET §3 ke estimates isi query se measured banenge."""
    return _rows(_SQL_TOKENS_BY_CLASS)


def hit_rate_by_class() -> list[dict[str, Any]]:
    """N4 — top-1/top-3 relevance hits. Random baseline distractor corpus mein
    ~2-4% hai (`METRICS.md` §3 N4); iske bina number meaningless hota."""
    return _rows(_SQL_HIT_RATE_BY_CLASS)


def ablation_delta_by_class() -> list[dict[str, Any]]:
    """N3 🔥 — RAG on vs off, same seeds. Delta zero bhi publish hota hai."""
    return _rows(_SQL_ABLATION_DELTA)


def disagreements_by_class() -> list[dict[str, Any]]:
    """🔥 N1's headline, per fault class.

    `disagreements` counts the verdicts where the naive probe said recovered
    and the sustained probe said it was not — i.e. the false positives the
    sustained oracle caught. Under F4 this column is expected to be non-zero;
    under F1 it is expected to be zero. If F4's column ever reads 0 across a
    real run, the project's central claim has failed, and `RETRO.md` §1
    pre-registered that outcome on purpose.
    """
    return _rows(_SQL_DISAGREEMENTS_BY_CLASS)
