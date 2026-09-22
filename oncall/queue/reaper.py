"""Reaper — mare hue worker ke anaath jobs wapas queue mein.

Worker loop isko har 30s chalata hai (`worker.py`), lekin standalone bhi
chala sakte ho:
    .venv/bin/python -m oncall.queue.reaper

────────────────────────────────────────────────────────────────────────────
Problem (R11) — "resumability" ka doosra aadha
────────────────────────────────────────────────────────────────────────────
Worker ne job claim ki (`status='running'`), phir OOM / `docker kill` / laptop
band. Postgres ka row-lock connection ke saath chala gaya — lekin `status`
hamesha ke liye `running` reh gaya. Koi use claim nahi karega, koi error nahi.
Incident **bina kisi log ke hamesha ke liye atak jaata** — sabse bura bug-class.

Isliye lock ki ASLI clock `locked_at` column hai. Reaper usko padhta hai:
jo job `JOB_STALE_AFTER_S` (600s) se zyada running hai wo anaath maani jaati
hai. Threshold queries.py mein hai, uske tark ke saath — yahan sirf loop.
"""

from __future__ import annotations

from oncall.db.queries import JOB_STALE_AFTER_S, reap_stale_jobs


def reap_once(*, stale_after_s: int = JOB_STALE_AFTER_S) -> list[dict]:
    """Ek sweep. Returns reaped rows — **khaali list normal hai** (har sweep
    kuch nahi milegi). Non-empty = koi worker crash hua tha; LOG karna zaruri."""
    return reap_stale_jobs(stale_after_s=stale_after_s)


def run_forever() -> None:  # pragma: no cover - manual ops tool
    """Standalone mode: har 30s ek sweep."""
    import time

    print(f"reaper shuru (stale > {JOB_STALE_AFTER_S}s)", flush=True)
    while True:
        for row in reap_once():
            print(
                f"♻️  reaped job {row['id']} (incident {row['incident_id']}, "
                f"owner {row['locked_by']})",
                flush=True,
            )
        time.sleep(30)


if __name__ == "__main__":
    run_forever()
