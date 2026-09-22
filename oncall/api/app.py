"""API app factory — the door to the whole HTTP surface.

🔴 A factory function (`create_app()`), not a module-level singleton — the same
   rule that sits behind `pool.py` (D49): importing a module must not have side
   effects. Tests build their own app, the worker builds its own, the dev
   server builds its own.

Route file names come from CODEMAP §3a-i. Each router owns its own area; this
file only wires them together, plus the two things that are genuinely global:
the same-origin guard and the static mount.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from oncall.api.security import same_origin_guard
from oncall.config import DOTENV_LOADED, REPO_ROOT
from oncall.db.migrate import verify as verify_schema
from oncall.db.pool import ping
from oncall.db.queries import queue_depth


def create_app() -> FastAPI:
    """Build the app, attach routers, mount static."""
    # Schema is VERIFIED at startup (read-only, D50).
    # 🔴 On failure the process does NOT START — that is the "catch it in 4
    #    seconds, not after 40 minutes of debugging" decision (see the header
    #    of `migrate.py`). A half-migrated schema that boots will kill the
    #    first incident halfway through, and that error looks exactly like a
    #    real fault.
    state = verify_schema()
    if not state.ok:
        raise RuntimeError(
            f"schema incomplete — the API will not start.\n  {state.summary()}\n"
            "  Fix: make nuke && make up  (or python -m oncall.db.migrate --write)"
        )

    # Imports live INSIDE the factory so module import stays side-effect-free.
    from oncall.api.routes_approve import router as approve_router
    from oncall.api.routes_faults import router as faults_router
    from oncall.api.routes_incidents import router as incidents_router
    from oncall.api.routes_stats import router as stats_router
    from oncall.api.routes_stream import router as stream_router

    app = FastAPI(title="OnCall control plane", lifespan=None)

    # 🔴 Registered FIRST so it wraps every route below it, including static.
    #    See `security.py` for why loopback binding alone was not enough.
    app.middleware("http")(same_origin_guard)

    @app.get("/health", include_in_schema=False)
    def health() -> JSONResponse:
        ok, reason = ping()
        body: dict[str, Any] = {
            "status": "ok" if ok else "unhealthy",
            "db": reason,
            "dotenv_loaded": DOTENV_LOADED,
            "queue": _safe_queue_depth(),
        }
        return JSONResponse(status_code=200 if ok else 503, content=body)

    @app.get("/api/services")
    async def services() -> list[dict[str, Any]]:
        """The dashboard's topology cards. See `_cached_snapshot` for why this
        is async and cached rather than a plain sync handler."""
        return await _cached_snapshot()

    app.include_router(incidents_router)
    app.include_router(stream_router)
    app.include_router(approve_router)
    app.include_router(faults_router)
    app.include_router(stats_router)

    static_dir = Path(REPO_ROOT) / "static"
    if static_dir.is_dir():
        # Mounted LAST — /api routes match first. `html=True` makes `/` serve
        # index.html directly.
        app.mount("/", _NoCacheStatic(directory=str(static_dir), html=True), name="static")

    return app


class _NoCacheStatic(StaticFiles):
    """StaticFiles that tells the browser never to reuse a cached copy.

    Audit finding F-16. There is no build step and therefore no content hash in
    any filename, so `app.js` is a stable URL whose contents change. The
    browser's default heuristic caching will happily serve yesterday's file,
    and the symptom of that — a fix that "did not work" — is indistinguishable
    from the fix actually not working. Hours disappear into that.

    Correctness over bytes: this is a single-operator localhost tool, so the
    extra requests cost nothing worth counting.
    """

    def is_not_modified(self, response_headers: Any, request_headers: Any) -> bool:
        return False

    async def get_response(self, path: str, scope: Any) -> Any:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response


def _safe_queue_depth() -> dict[str, int]:
    try:
        return queue_depth()
    except Exception:  # noqa: BLE001 - the health endpoint must show everything, not crash
        return {"queued": -1, "running": -1, "done": -1, "failed": -1}


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/services — the dashboard's topology cards
# ─────────────────────────────────────────────────────────────────────────────
# It lives here (rather than in its own file) because it is a read-only
# snapshot that changes neither incidents nor approvals. Using the Docker SDK
# here is SAFE: status inspection only, no lifecycle calls.

_SERVICES = ("shopapp", "shopdb", "shopcache")

_CONTAINER_NAMES = {
    # handlers._CONTAINERS is authoritative for the three victims; oncall-db is
    # deliberately absent from that map (the agent must not be able to touch
    # it) but the dashboard SHOULD show it — it is this project's own brain.
    "oncall-db": "oncall-db",
}

# ─────────────────────────────────────────────────────────────────────────────
# 🔴 Why this endpoint is cached — audit finding F-07
# ─────────────────────────────────────────────────────────────────────────────
# One call does up to five blocking Docker SDK round-trips plus an HTTP probe
# of shopapp. The dashboard polls it every few seconds, and every open tab
# polls independently.
#
# The bad case is not hypothetical, and it is not rare — it is the demo. Under
# F4 the shopapp probe is exactly the thing that hangs, so the endpoint is at
# its slowest precisely when the operator is refreshing hardest. A sync handler
# would occupy a threadpool worker for the whole probe, and the requests would
# stack up behind each other.
#
# Two changes: run the blocking work in a thread so the event loop stays free,
# and collapse concurrent callers onto one in-flight snapshot with a short TTL.
# The lock is what makes the second part work — without it, ten simultaneous
# requests all miss the cache and all launch their own Docker sweep.
_SNAPSHOT_TTL_S = 2.0
_snapshot_lock = asyncio.Lock()
_snapshot_cache: tuple[float, list[dict[str, Any]]] | None = None


async def _cached_snapshot() -> list[dict[str, Any]]:
    """`service_snapshot()` behind a TTL cache and a thread."""
    global _snapshot_cache

    now = time.monotonic()
    cached = _snapshot_cache
    if cached is not None and now - cached[0] < _SNAPSHOT_TTL_S:
        return cached[1]

    async with _snapshot_lock:
        # Re-check inside the lock: whoever we queued behind has probably
        # already refreshed it, and doing the sweep twice would defeat the point.
        cached = _snapshot_cache
        now = time.monotonic()
        if cached is not None and now - cached[0] < _SNAPSHOT_TTL_S:
            return cached[1]

        rows = await asyncio.to_thread(service_snapshot)
        _snapshot_cache = (time.monotonic(), rows)
        return rows


def service_snapshot() -> list[dict[str, Any]]:
    """`(name, container_state, health_detail)` for all four services at once.

    🔴 shopapp's health is DEEP (/health — db+cache+disk); the other three
       report container status only. Under F5 shopdb/shopcache can look green
       while shopapp is red — that is the localisation puzzle the UI must show.
    """
    import docker

    from oncall.actions.handlers import _CONTAINERS

    out: list[dict[str, Any]] = []
    names = {**_CONTAINERS, **_CONTAINER_NAMES}

    client = None
    try:
        client = docker.from_env()
    except Exception as exc:  # noqa: BLE001 - "daemon down" is a readable card too
        out.append(
            {"service": "docker-daemon", "state": f"unreachable ({type(exc).__name__})", "detail": ""}
        )

    for svc in (*_SERVICES, "oncall-db"):
        row: dict[str, Any] = {"service": svc, "state": "unknown", "detail": ""}
        if client is not None:
            try:
                c = client.containers.get(names.get(svc, f"oncall-{svc}"))
                c.reload()
                row["state"] = str(c.attrs.get("State", {}).get("Status", "unknown"))
            except Exception as exc:  # noqa: BLE001
                row["state"] = f"not_found ({type(exc).__name__})"
        if svc == "shopapp":
            row["detail"], row["checks"] = _shopapp_health()
        out.append(row)
    return out


#: 🔴 2 s, not 4 s. This probe runs inside the dashboard's polling path, and
#: under F4 it is the call that hangs. A shorter timeout means "shopapp is not
#: answering" reaches the screen sooner, which is what the operator needs.
_PROBE_TIMEOUT_S = 2.0


def _shopapp_health() -> tuple[str, dict[str, Any]]:
    """A naive probe for the UI card: `(human_readable_line, per_check_dict)`.

    🔴 This is NOT THE ORACLE (one shot, no load): under F4 it returns 200
       while the oracle says failed. That difference IS the project's result
       (`TEACH_PHASE1.md` §5).

    It returns the raw checks dict alongside the display string so the UI can
    colour db / cache / disk individually. The alternative — having the UI
    re-parse the display string — makes the string a load-bearing format, and
    then the display starts lying the day someone reformats it.
    """
    import httpx

    from oncall.config import settings

    try:
        resp = httpx.get(settings.shopapp_health_url, timeout=_PROBE_TIMEOUT_S)
        checks = _extract_checks(resp)
        if resp.status_code == 200:
            line = "ok · " + ", ".join(f"{k}:{str(v)[:24]}" for k, v in sorted(checks.items()))
        else:
            line = f"HTTP {resp.status_code}"
        return line, checks
    except Exception as exc:  # noqa: BLE001
        # No response at all — connection refused (F1), timeout (F3/F4).
        # The exception type IS the diagnostic here, so it goes on the card.
        return type(exc).__name__, {}


def _extract_checks(resp: Any) -> dict[str, Any]:
    """Pull the per-subsystem checks out of a shopapp /health response.

    🔴 The body has TWO shapes, and missing the second one silently blanks the
       UI exactly when it matters most:

         200 → {"status": "ok", "checks": {...}}
         503 → {"detail": {"status": "unhealthy", "checks": {...}}}

    The 503 shape is FastAPI's doing: `raise HTTPException(status_code=503,
    detail=body)` nests the whole body under `detail`. So the unhealthy case —
    the only case anyone is looking at the screen for — is the one where a
    naive `resp.json()["checks"]` returns nothing.
    """
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 - a body is not guaranteed to be JSON at all
        return {}
    if not isinstance(body, dict):
        return {}
    if isinstance(body.get("detail"), dict):
        body = body["detail"]
    checks = body.get("checks")
    return dict(checks) if isinstance(checks, dict) else {}
