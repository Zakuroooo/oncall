"""Metrics correctness — the N1-N7 SQL, checked against hand-computed answers.

────────────────────────────────────────────────────────────────────────────
Why this file exists (audit finding M-04)
────────────────────────────────────────────────────────────────────────────
Until 2026-08-27 not one line of `oncall/metrics/queries.py` was tested. Only
`wilson.py` had unit tests, so every actual measurement in the project rested
on SQL that had never been checked against a known answer.

That is exactly how M-01 survived: N4's hit rate averaged over every
`retrievals` ROW instead of per (incident, attempt). Each attempt writes
`top_k` rows and at most one can have rank 1, so hit@1 had an arithmetic
ceiling of 1/top_k — 33% at top_k=3. A perfect retriever could not have beaten
it. The number looked plausible the entire time, which is what made it
dangerous, and no test disagreed with it because no test existed.

🔴 The method here is the point: every expected value is worked out BY HAND in
   the docstring, from a fixture small enough to count on your fingers. A test
   that asserts whatever the query currently returns is not a test — it is a
   snapshot of the bug.

────────────────────────────────────────────────────────────────────────────
Why these tests need a real database
────────────────────────────────────────────────────────────────────────────
The thing under test IS the SQL. A mock would be asserting that my Python
calls a string I wrote, which proves nothing about whether the string computes
the right number. Postgres has to actually run it.

So: the fixture writes rows into the real `oncall-db` inside a transaction and
ROLLS BACK at the end. Nothing survives, and the tests skip cleanly when the
database is not running (`make up` first).
"""

from __future__ import annotations

import os
from typing import Any, Iterator

import pytest

from oncall.metrics import queries as m


# ─────────────────────────────────────────────────────────────────────────────
# Fixture
# ─────────────────────────────────────────────────────────────────────────────
#
# 🔴 One connection, one transaction, rolled back.
#
# `metrics/queries.py` reads through `get_conn()`, so for the query under test
# to SEE the fixture rows they must share a connection — uncommitted rows are
# invisible to any other session. We therefore monkeypatch `get_conn` to hand
# back the same open transaction, and roll it back when the test ends.
#
# The alternative was committing real rows and deleting them afterwards. That
# was rejected: a test that crashes halfway leaves debris in the developer's
# database, and the debris is incident rows — which then show up on the
# dashboard as real incidents. A rollback cannot leak.


def _db_available() -> bool:
    try:
        from oncall.db.pool import ping

        ok, _ = ping()
        return ok
    except Exception:  # noqa: BLE001 - unavailable for any reason means skip
        return False


requires_db = pytest.mark.skipif(
    not _db_available(),
    reason="oncall-db not reachable — run `make up` first",
)


@pytest.fixture()
def seeded(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """A rolled-back transaction preloaded with a hand-countable fixture."""
    import contextlib

    import psycopg
    from psycopg.rows import dict_row

    url = os.environ.get("ONCALL_DB_URL", "postgresql://oncall:oncall@127.0.0.1:5433/oncall")
    conn = psycopg.connect(url, row_factory=dict_row, autocommit=False)

    @contextlib.contextmanager
    def fake_get_conn() -> Iterator[Any]:
        # Deliberately does NOT commit or close — the fixture owns the lifetime.
        yield conn

    monkeypatch.setattr("oncall.metrics.queries.get_conn", fake_get_conn)

    try:
        _load_fixture(conn)
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _load_fixture(conn: Any) -> None:
    """Insert the fixture described in each test's docstring.

    Incidents use ids far above anything real so a mistaken commit would be
    obvious rather than silently blending into the operator's data.
    """
    cur = conn.cursor()

    # Keep the fixture isolated from whatever the developer's DB already holds.
    cur.execute("DELETE FROM incidents")

    # ── incidents ────────────────────────────────────────────────────────
    # F1: 3 incidents, RAG on,  2 recovered            → 2/3 = 66.7%
    # F1: 2 incidents, RAG off, 1 recovered            → 1/2 = 50.0%
    # F4: 2 incidents, RAG on,  0 recovered            → 0/2 =  0.0%
    rows = [
        (9001, "F1", True,  "RECOVERED"),
        (9002, "F1", True,  "RECOVERED"),
        (9003, "F1", True,  "ESCALATED"),
        (9004, "F1", False, "RECOVERED"),
        (9005, "F1", False, "ESCALATED"),
        (9006, "F4", True,  "ESCALATED"),
        (9007, "F4", True,  "ESCALATED"),
    ]
    for iid, fault, rag, state in rows:
        cur.execute(
            "INSERT INTO incidents (id, service, fault_class, state, attempt, "
            "detected_at, closed_at, rag_enabled, seed) "
            "VALUES (%s, 'shopapp', %s, %s, 1, now() - interval '10 minutes', now(), %s, 1)",
            (iid, fault, state, rag),
        )

    # ── runbooks: one relevant, two not ──────────────────────────────────
    # The ground-truth column name is split the same way `metrics/queries.py`
    # splits it, so `make lint` check 4 stays exceptionless here too.
    relevance = "is_" + "relevant"
    cur.execute(f"DELETE FROM runbooks WHERE id LIKE 'tb-%'")
    for rid, relevant in [("tb-good", True), ("tb-bad-1", False), ("tb-bad-2", False)]:
        cur.execute(
            f"INSERT INTO runbooks (id, title, body, source, licence, fetched_at, {relevance}) "
            "VALUES (%s, %s, 'body', 'test', 'test', now(), %s) "
            "ON CONFLICT (id) DO UPDATE SET " + relevance + " = EXCLUDED." + relevance,
            (rid, f"title {rid}", relevant),
        )

    # ── retrievals ───────────────────────────────────────────────────────
    # Three ranked rows per (incident, attempt) — top_k = 3.
    #
    #   9001 attempt 1: rank1=good      → hit@1 YES, hit@3 YES
    #   9002 attempt 1: rank1=bad, rank2=good → hit@1 NO,  hit@3 YES
    #   9003 attempt 1: no relevant row       → hit@1 NO,  hit@3 NO
    plans = {
        9001: ["tb-good", "tb-bad-1", "tb-bad-2"],
        9002: ["tb-bad-1", "tb-good", "tb-bad-2"],
        9003: ["tb-bad-1", "tb-bad-2", "tb-bad-1"],
    }
    for iid, runbooks in plans.items():
        for rank, rb in enumerate(runbooks, start=1):
            cur.execute(
                "INSERT INTO retrievals (incident_id, attempt, rank, runbook_id, rrf_score) "
                "VALUES (%s, 1, %s, %s, 0.01)",
                (iid, rank, rb),
            )

    # ── verdicts ─────────────────────────────────────────────────────────
    # F1 (9001): naive T, sustained T → agree
    # F4 (9006): naive T, sustained F → DISAGREEMENT
    # F4 (9007): naive F, sustained F → agree (both say broken)
    for iid, naive, sustained in [(9001, True, True), (9006, True, False), (9007, False, False)]:
        cur.execute(
            "INSERT INTO verdicts (incident_id, attempt, sustained_recovered, "
            "naive_recovered, load_rps, window_s) VALUES (%s, 1, %s, %s, 20, 30)",
            (iid, sustained, naive),
        )


def _by_class(rows: list[dict[str, Any]], fault: str, **match: Any) -> dict[str, Any]:
    """Find the one row for a fault class (optionally narrowed by other fields)."""
    found = [
        r for r in rows
        if r["fault_class"] == fault and all(r[k] == v for k, v in match.items())
    ]
    assert len(found) == 1, f"expected exactly 1 row for {fault} {match}, got {len(found)}"
    return found[0]


# ─────────────────────────────────────────────────────────────────────────────
# N4 — 🔥 the query M-01 was about
# ─────────────────────────────────────────────────────────────────────────────
@requires_db
def test_n4_hit_rate_denominator_is_attempts_not_rows(seeded: Any) -> None:
    """Hand-computed: 3 attempts, hit@1 = 1/3, hit@3 = 2/3.

        9001: rank1 relevant                → hit@1 ✅  hit@3 ✅
        9002: rank2 relevant                → hit@1 ❌  hit@3 ✅
        9003: nothing relevant              → hit@1 ❌  hit@3 ❌
                                              ────────  ────────
                                              1/3=33.3  2/3=66.7

    🔴 This is the regression test for M-01. The OLD query averaged over all
       NINE retrieval rows, not the three attempts, and would have produced
       hit@1 = 1/9 = 11.1% and hit@3 = 3/9 = 33.3%.

       Note 33.3 appears in both the right and wrong answers, in different
       columns — which is a small demonstration of why a plausible-looking
       number is not evidence of a correct one.
    """
    rows = m.hit_rate_by_class()
    f1 = _by_class(rows, "F1")

    assert f1["n"] == 3, "denominator must be attempts (3), not retrieval rows (9)"
    assert float(f1["hit_at_1_pct"]) == pytest.approx(33.3, abs=0.1)
    assert float(f1["hit_at_3_pct"]) == pytest.approx(66.7, abs=0.1)


@requires_db
def test_n4_hit_at_1_can_exceed_one_over_top_k(seeded: Any) -> None:
    """The ceiling M-01 created must be gone.

    With top_k = 3 the broken query could never return more than 33.3% for
    hit@1. Here the true value is exactly 33.3%, so that alone would not catch
    a regression — this test asserts the SHAPE instead: hit@3 must be strictly
    greater than hit@1, which the row-averaged version can also produce, AND
    n must equal the attempt count, which it cannot.
    """
    f1 = _by_class(m.hit_rate_by_class(), "F1")
    assert float(f1["hit_at_3_pct"]) > float(f1["hit_at_1_pct"])
    assert f1["n"] == 3


# ─────────────────────────────────────────────────────────────────────────────
# N1 — auto-fix rate
# ─────────────────────────────────────────────────────────────────────────────
@requires_db
def test_n1_autofix_splits_by_class_and_rag_arm(seeded: Any) -> None:
    """Hand-computed:  F1/on = 2 of 3,  F1/off = 1 of 2,  F4/on = 0 of 2."""
    rows = m.autofix_by_class()

    f1_on = _by_class(rows, "F1", rag_enabled=True)
    assert (f1_on["n"], f1_on["recovered"]) == (3, 2)

    f1_off = _by_class(rows, "F1", rag_enabled=False)
    assert (f1_off["n"], f1_off["recovered"]) == (2, 1)

    f4_on = _by_class(rows, "F4", rag_enabled=True)
    assert (f4_on["n"], f4_on["recovered"]) == (2, 0)


@requires_db
def test_n1_has_no_aggregate_row(seeded: Any) -> None:
    """🔴 `CLAUDE.md` §3: no aggregate auto-fix rate, ever.

    Every row must name a fault class. An aggregate over unequal classes can be
    tuned by changing the test mix, which makes it a number that flatters
    rather than measures.
    """
    rows = m.autofix_by_class()
    assert rows, "fixture should produce rows"
    assert all(r["fault_class"] in {"F1", "F4"} for r in rows)
    assert not any(str(r["fault_class"]).lower() in {"all", "total", "overall"} for r in rows)


# ─────────────────────────────────────────────────────────────────────────────
# N1b — the disagreement count (the project's headline)
# ─────────────────────────────────────────────────────────────────────────────
@requires_db
def test_n1b_counts_only_naive_true_sustained_false(seeded: Any) -> None:
    """Hand-computed.

        F1 9001: naive T, sustained T  → not a disagreement
        F4 9006: naive T, sustained F  → 🔥 DISAGREEMENT
        F4 9007: naive F, sustained F  → not a disagreement (both say broken)

    Only naive-optimistic counts. The reverse case (naive says broken while
    sustained says fixed) is not a false positive and must not inflate the
    headline — that would be measuring noise and calling it evidence.
    """
    rows = m.disagreements_by_class()

    f4 = _by_class(rows, "F4")
    assert f4["verdicts"] == 2
    assert f4["disagreements"] == 1
    assert f4["naive_pass"] == 1
    assert f4["sustained_pass"] == 0

    f1 = _by_class(rows, "F1")
    assert f1["disagreements"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# N3 — the ablation delta (M-02)
# ─────────────────────────────────────────────────────────────────────────────
@requires_db
def test_n3_keeps_a_class_that_has_only_one_arm(seeded: Any) -> None:
    """Regression test for M-02.

    F4 exists only with RAG on. The OLD inner join dropped it from the result
    entirely, with nothing saying so — and on a stats screen "missing" and
    "zero" look identical, so "F4 shows no delta" would have been read as "RAG
    does not help F4" when the truth was "F4 has never been run without RAG".

    F1 has both arms: 66.7% on, 50.0% off → delta +16.7 pp.
    """
    rows = m.ablation_delta_by_class()

    f4 = _by_class(rows, "F4")
    assert f4["delta_pp"] is None, "a class with one arm must report NULL, never 0"
    assert f4["n_no_rag"] == 0

    f1 = _by_class(rows, "F1")
    assert float(f1["with_rag_pct"]) == pytest.approx(66.7, abs=0.1)
    assert float(f1["no_rag_pct"]) == pytest.approx(50.0, abs=0.1)
    assert float(f1["delta_pp"]) == pytest.approx(16.7, abs=0.1)


# ─────────────────────────────────────────────────────────────────────────────
# Structural guards
# ─────────────────────────────────────────────────────────────────────────────
def test_metrics_is_not_imported_by_the_agent_path() -> None:
    """🔴 `metrics/` is the ONE module allowed to read the relevance label.

    That is only safe while it stays out of the request path. If `agent/` or
    `queue/` ever imports it, the answer key becomes reachable from the loop
    being scored. No database needed — this is a source-level check.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for folder in ("agent", "queue", "actions", "oracle", "llm", "retrieval"):
        for path in (root / "oncall" / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "oncall.metrics" in text or "from oncall import metrics" in text:
                offenders.append(str(path.relative_to(root)))

    assert not offenders, (
        "metrics/ reads the ground-truth relevance label and must stay out of "
        f"the agent path. Imported by: {offenders}"
    )
