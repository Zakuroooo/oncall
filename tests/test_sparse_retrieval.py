"""Regression test for the keyword half of hybrid retrieval.

Until 2026-10-15 `sparse()` AND-ed every stem of a multi-sentence diagnosis, so it
matched nothing and every incident logged `keyword_candidates: 0`. This test uses
a realistic diagnosis-length query and requires the keyword search to find the
max-connections runbook. Read-only against the real corpus; skips without the DB.
"""

from __future__ import annotations

import pytest

from oncall.retrieval.search import sparse


def _corpus_available() -> bool:
    try:
        from oncall.db.pool import get_conn

        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM runbooks")
            return int(cur.fetchone()["n"]) > 0
    except Exception:  # noqa: BLE001 - unavailable for any reason means skip
        return False


requires_corpus = pytest.mark.skipif(not _corpus_available(), reason="runbook corpus not loaded — run `make up`")

DIAGNOSIS = (
    "The service fails because PostgreSQL rejects new connections: FATAL sorry, too many "
    "clients already. The connection pool is exhausted under load."
)


@requires_corpus
def test_long_diagnosis_finds_keyword_candidates() -> None:
    rows = sparse(DIAGNOSIS)
    assert rows, "keyword search returned nothing for a diagnosis-length query"
    assert any(str(r["id"]).startswith("rb-postgres-max-connections") for r in rows[:5])


@requires_corpus
def test_empty_query_returns_nothing_instead_of_raising() -> None:
    assert sparse("the and of") == []
