"""Runbook corpus ingest — vendor docs fetch → chunk → embed → oncall-db.

────────────────────────────────────────────────────────────────────────────
🔴 DRY RUN DEFAULT. `--write` ke bina kuch fetch nahi, kuch likha nahi jaata.
────────────────────────────────────────────────────────────────────────────
    .venv/bin/python scripts/ingest_runbooks.py            # plan dekho
    .venv/bin/python scripts/ingest_runbooks.py --write    # asli ingest

`--write` ke liye DO cheezein chahiye:
  1. oncall-db chal raha ho (`make up`)
  2. embed service port 8010 pe (`make install-ingest` phir uvicorn
     oncall.retrieval.embed_service:app --port 8010)

────────────────────────────────────────────────────────────────────────────
Q1 ka jawaab yahan code ban jaata hai (D17)
────────────────────────────────────────────────────────────────────────────
Corpus KHUD KA LIKHA NAHI hai — wo retrieval hit-rate ko rigged kar deta.
REGISTRY mein asli vendor-doc URLs hain. Sirf ~5 slugs RELEVANT hain, aur wo
5 bhi yahan likhe NAHI jaate — runtime pe `chaos.faults` ke ground-truth
fields se UNION nikalta hai. Yaani "kaunsa doc relevant hai" ka faisla fault
registry ke saath drift-proof hai: naya fault juda → uska runbook slug registry
mein juda → relevance khud-ba-khud sahi.

Baaki ~11 distractors ASLI documentation hain (Postgres/Redis/Docker) — fake
docs rakhna exam apne aap set karke khud dena hai.

⚠️ LICENCES (R1): neeche har entry ka licence string hai. ⚠️ Ye strings abhi
UNVERIFIED hain — Phase 6 ka pehla kaam inko `docs/DATA.md` ki table ke saath
verify karna hai. Jo source verify nahi hua wo DROP hoga, chahe corpus chhota
ho jaaye.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

# ── Registry ─────────────────────────────────────────────────────────────────
# Slug naming: `rb-<topic>`. Relevant slugs EXACTLY wahi hain jo faults.py ke
# fields mein hain — mismatch ho toh neeche `_relevance_check()` loudly marega.

_PGL = "PostgreSQL Licence (postgresql.org, UNVERIFIED-R1)"
_REDIS_L = "CC BY-SA 4.0 (redis.io, UNVERIFIED-R1)"
_DOCKER_L = "Apache-2.0 (docs.docker.com, UNVERIFIED-R1)"

PG = "https://www.postgresql.org/docs/15"
RD = "https://redis.io/docs/latest/commands"
DK = "https://docs.docker.com/reference/cli/docker/container"

REGISTRY: list[dict[str, str]] = [
    # ── RELEVANT candidates (union faults ke fields se match hoti hai) ──
    {"slug": "rb-postgres-max-connections", "title": "Postgres: connections and authentication",
     "url": f"{PG}/runtime-config-connection.html", "source": "postgresql.org", "licence": _PGL},
    {"slug": "rb-postgres-lock-contention", "title": "Postgres: explicit locking",
     "url": f"{PG}/explicit-locking.html", "source": "postgresql.org", "licence": _PGL},
    {"slug": "rb-disk-full-cache-dir", "title": "Postgres: determining disk usage",
     "url": f"{PG}/diskusage.html", "source": "postgresql.org", "licence": _PGL},
    {"slug": "rb-docker-container-exited", "title": "docker container restart (CLI)",
     "url": f"{DK}/restart/", "source": "docs.docker.com", "licence": _DOCKER_L},
    {"slug": "rb-redis-dependency-down", "title": "PING command (Redis)",
     "url": f"{RD}/ping/", "source": "redis.io", "licence": _REDIS_L},
    # ── DISTRACTORS (asli docs, kisi fault ka sahi jawab nahi) ──
    {"slug": "rb-pg-resource-config", "title": "Postgres: resource consumption settings",
     "url": f"{PG}/runtime-config-resource.html", "source": "postgresql.org", "licence": _PGL},
    {"slug": "rb-pg-logging-config", "title": "Postgres: error reporting and logging",
     "url": f"{PG}/runtime-config-logging.html", "source": "postgresql.org", "licence": _PGL},
    {"slug": "rb-pg-mvcc", "title": "Postgres: concurrency control (MVCC)",
     "url": f"{PG}/mvcc.html", "source": "postgresql.org", "licence": _PGL},
    {"slug": "rb-pg-vacuum", "title": "Postgres: VACUUM reference",
     "url": f"{PG}/sql-vacuum.html", "source": "postgresql.org", "licence": _PGL},
    {"slug": "rb-pg-ha", "title": "Postgres: high availability and load balancing",
     "url": f"{PG}/high-availability.html", "source": "postgresql.org", "licence": _PGL},
    {"slug": "rb-pg-backup", "title": "Postgres: backup and restore",
     "url": f"{PG}/backup.html", "source": "postgresql.org", "licence": _PGL},
    {"slug": "rb-redis-flushall", "title": "FLUSHALL command (Redis)",
     "url": f"{RD}/flushall/", "source": "redis.io", "licence": _REDIS_L},
    {"slug": "rb-redis-info", "title": "INFO command (Redis)",
     "url": f"{RD}/info/", "source": "redis.io", "licence": _REDIS_L},
    {"slug": "rb-docker-container-stop", "title": "docker container stop (CLI)",
     "url": f"{DK}/stop/", "source": "docs.docker.com", "licence": _DOCKER_L},
    {"slug": "rb-docker-system-prune", "title": "docker system prune (CLI)",
     "url": "https://docs.docker.com/reference/cli/docker/system/prune/",
     "source": "docs.docker.com", "licence": _DOCKER_L},
    {"slug": "rb-docker-container-inspect", "title": "docker container inspect (CLI)",
     "url": f"{DK}/inspect/", "source": "docs.docker.com", "licence": _DOCKER_L},
]

#: Chunk ka target size (chars). ~450 tokens/chunk — TOKEN_BUDGET §3 Call 2
#: teeno runbooks ka budget isi number pe khada hai.
CHUNK_CHARS = 1200


def _relevance_check() -> None:
    """Registry ↔ faults registry ka cross-check. Import-time pe NAHI — main()
    se, kyunki ye script bina venv bhi `--help` kar sakti hai."""
    from chaos import faults as faults_mod

    expected: set[str] = set()
    for f in faults_mod.FAULTS.values():
        expected.update(f.relevant_runbooks)
    registered = {e["slug"] for e in REGISTRY}
    missing = expected - registered
    if missing:
        raise SystemExit(
            f"❌ Registry mein ye RELEVANT slugs gayab hain: {sorted(missing)}.\n"
            "   Inke bina un faults ke liye retrieval kabhi hit nahi hogi — "
            "corpus adhoora hai."
        )


# ── HTML → text (stdlib; koi new dep nahi) ───────────────────────────────────
class _TextExtractor(HTMLParser):
    """Sabse sasta readable-text extractor. Sirf <script>/<style> chhodta hai,
    baaki sab text data jodta hai. Vendor docs ke fixed layouts ke liye kaafi."""

    _SKIP = {"script", "style", "nav", "footer"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[no-untyped-def]
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self.chunks.append(text)


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    return re.sub(r"\s+", " ", " ".join(p.chunks)).strip()


def chunk_text(text: str, *, max_chars: int = CHUNK_CHARS) -> list[str]:
    """Sentence-boundary pe todta hai. Poora word beech se cut nahi hota —
    half-sentence embedding noise hota hai."""
    out: list[str] = []
    buf: list[str] = []
    size = 0
    for sentence in re.split(r"(?<=[.;])\s+", text):
        s = sentence.strip()
        if not s:
            continue
        if size + len(s) > max_chars and buf:
            out.append(" ".join(buf))
            buf, size = [], 0
        buf.append(s)
        size += len(s) + 1
    if buf:
        out.append(" ".join(buf))
    return [c for c in out if len(c) > 80]  # chhote tukde embedding ke layak nahi


# ── Fetch + build ────────────────────────────────────────────────────────────

def fetch_all() -> tuple[list[dict[str, object]], list[str]]:
    """Har registry entry fetch → text → chunks. Returns (rows, errors).

    Ek fail hua URL poora ingest NAHI maarta — error list mein aata hai aur
    baaki docs ingest hote hain. Corpus thoda chhota hona zinda corpus se
    behtar hai (`README`: licence/source drop policy)."""
    rows: list[dict[str, object]] = []
    errors: list[str] = []
    today = date.today().isoformat()

    for entry in REGISTRY:
        try:
            resp = httpx.get(entry["url"], timeout=30.0, follow_redirects=True)
            resp.raise_for_status()
            text = html_to_text(resp.text)
        except Exception as exc:  # noqa: BLE001 - network errors expected hain
            errors.append(f"{entry['slug']}: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        pieces = chunk_text(text)
        if not pieces:
            errors.append(f"{entry['slug']}: text nahi bana (layout badla?)")
            continue
        for i, piece in enumerate(pieces, start=1):
            rows.append(
                {
                    "id": f"{entry['slug']}#{i}",
                    "slug": entry["slug"],
                    "title": f"{entry['title']} [{i}/{len(pieces)}]",
                    "body": piece,
                    "source": entry["source"],
                    "source_url": entry["url"],
                    "licence": entry["licence"],
                    "fetched_at": today,
                }
            )
    return rows, errors


def write_db(rows: list[dict[str, object]], *, batch_size: int = 32) -> int:
    """Embed + INSERT. `ON CONFLICT DO UPDATE` — dobara ingest embeddings
    refresh karta hai, duplicate nahi banata (idempotent re-runnable)."""
    import psycopg

    from chaos import faults as faults_mod
    from oncall.config import load_settings
    from oncall.retrieval.embed import embed_batch

    expected: set[str] = set()
    for f in faults_mod.FAULTS.values():
        expected.update(f.relevant_runbooks)

    dsn = load_settings().oncall_db_url
    written = 0
    with psycopg.connect(dsn) as conn:
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            texts = [f"{r['title']}\n{r['body']}" for r in batch]
            vectors = embed_batch(texts)
            for row, vec in zip(batch, vectors):
                conn.execute(
                    """
                    INSERT INTO runbooks
                        (id, title, body, source, source_url, licence,
                         fetched_at, is_relevant, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::date, %s, %s::vector)
                    ON CONFLICT (id) DO UPDATE SET
                        title = EXCLUDED.title,
                        body = EXCLUDED.body,
                        source = EXCLUDED.source,
                        source_url = EXCLUDED.source_url,
                        licence = EXCLUDED.licence,
                        fetched_at = EXCLUDED.fetched_at,
                        is_relevant = EXCLUDED.is_relevant,
                        embedding = EXCLUDED.embedding
                    """,
                    (
                        row["id"], row["title"], row["body"], row["source"],
                        row["source_url"], row["licence"], row["fetched_at"],
                        str(row["slug"]) in expected,
                        "[" + ",".join(f"{x:.7f}" for x in vec) + "]",
                    ),
                )
                written += 1
        conn.commit()
    return written


# ── CLI ──────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/ingest_runbooks.py",
        description="Vendor-docs runbook corpus ingest (DRY RUN default).",
        epilog=(
            "Examples:\n"
            "  python scripts/ingest_runbooks.py            # plan print, kuch nahi likhta\n"
            "  python scripts/ingest_runbooks.py --write    # fetch+embed+insert\n"
        ),
    )
    parser.add_argument("--write", action="store_true", help="asli mein karo")
    args = parser.parse_args(argv)

    _relevance_check()
    relevant = sum(
        1 for e in REGISTRY if _is_relevant_slug(e["slug"])
    )

    print(f"registry      : {len(REGISTRY)} documents ({relevant} relevant, "
          f"{len(REGISTRY) - relevant} distractors)")
    print(f"chunk target  : ~{CHUNK_CHARS} chars")
    if not args.write:
        print("\nDRY-RUN plan (kuch fetch nahi hua, kuch likha nahi gaya):")
        for e in REGISTRY:
            tag = "RELEVANT" if _is_relevant_slug(e["slug"]) else "distractor"
            print(f"  [{tag:>8}] {e['slug']:<34} {e['url']}")
        print("\n🔴 Asli ingest: --write (embed service port 8010 pe chalna chahiye)")
        return 0

    print(f"\nfetching {len(REGISTRY)} URLs...")
    rows, errors = fetch_all()
    for err in errors:
        print(f"  ⚠️  skip: {err}")
    if not rows:
        print("❌ ek bhi document fetch nahi hua — network/layout check karo")
        return 1
    print(f"fetched: {len(rows)} chunks from {len(REGISTRY) - len(errors)} docs")

    written = write_db(rows)
    print(f"\n✅ {written} rows written to runbooks (with embeddings + provenance)")
    print(json.dumps({"written": written, "skipped_docs": len(errors)}, indent=2))
    return 0


def _is_relevant_slug(slug: object) -> bool:
    from chaos import faults as faults_mod

    expected: set[str] = set()
    for f in faults_mod.FAULTS.values():
        expected.update(f.relevant_runbooks)
    return str(slug) in expected


if __name__ == "__main__":
    sys.exit(main())
