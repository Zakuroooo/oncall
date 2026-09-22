"""SSE stream — the live incident timeline.

────────────────────────────────────────────────────────────────────────────
What SSE is, and why not WebSocket (D13)
────────────────────────────────────────────────────────────────────────────
Server-Sent Events = one HTTP connection that stays open while the server keeps
writing text into it. The browser has `EventSource` built in, it reconnects on
its own, and the whole thing is plain HTTP. WebSocket is two-way; we only need
one way (server → browser), so WebSocket's extra machinery buys us nothing.

How to debug it without a browser:
    curl -N http://127.0.0.1:8000/api/incidents/42/stream

🔴 This endpoint never touches the pool directly — every poll goes through
   `asyncio.to_thread` (see `pool.py`'s docstring: otherwise one open browser
   tab eats the whole CONTROL_POOL_MAX=4). A connection is borrowed for the
   duration of a query, not for the duration of the stream.

────────────────────────────────────────────────────────────────────────────
🔴 The resume cursor — audit finding F-01
────────────────────────────────────────────────────────────────────────────
Every frame carries `id: <events.id>`. When `EventSource` reconnects after a
network blip it replays that value back to us in the `Last-Event-ID` request
header. We MUST start the cursor there.

Until 2026-08-27 this file hardcoded `cursor = 0` and never read the header, so
every reconnect replayed the entire timeline from the beginning. The browser
appends events (append-only is a deliberate choice in `app.js`), so the visible
result was a timeline that doubled itself on every blip.

`events.id` is a bigserial — monotonic — which is exactly what makes "everything
after this id" a precise, resumable question.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from oncall.db.queries import get_incident, get_timeline_since

router = APIRouter(prefix="/api/incidents", tags=["stream"])

_POLL_S = 1.0
_HEARTBEAT_EVERY_S = 15.0

#: How long the browser waits before reconnecting, in milliseconds. Sent once
#: as an SSE `retry:` field. The browser default is 3 s; 2 s keeps a live demo
#: feeling responsive without hammering the DB.
_RETRY_MS = 2000


def _sse(event_name: str, payload: Any, *, event_id: int | None = None) -> str:
    """Build one SSE frame. `id:` becomes the client's Last-Event-ID cursor."""
    parts = []
    if event_id is not None:
        parts.append(f"id: {event_id}")
    parts.append(f"event: {event_name}")
    parts.append(f"data: {json.dumps(payload, default=str)}")
    return "\n".join(parts) + "\n\n"


def _resume_cursor(request: Request) -> int:
    """Read the resume point out of the `Last-Event-ID` header.

    Returns 0 (replay everything) when the header is missing or unparseable —
    a fresh page load has no cursor, and a garbage header should degrade to a
    full replay rather than a 500.
    """
    raw = request.headers.get("last-event-id")
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


async def _gen(incident_id: int, cursor: int) -> AsyncIterator[str]:
    # Tell the browser how long to wait before reconnecting.
    yield f"retry: {_RETRY_MS}\n\n"

    # Initial snapshot, so a page refresh immediately has full context.
    incident = await asyncio.to_thread(get_incident, incident_id)
    if incident is None:
        yield _sse("error", {"detail": f"incident {incident_id} not found"})
        return
    yield _sse("incident", incident)

    last_emit = asyncio.get_event_loop().time()

    while True:
        rows = await asyncio.to_thread(get_timeline_since, incident_id, cursor)
        for row in rows:
            cursor = max(cursor, int(row["id"]))
            yield _sse("event", row, event_id=int(row["id"]))

        # Re-read the incident every tick. Cheap, and it means the terminal
        # check below decides on the CURRENT state rather than a stale one.
        fresh = await asyncio.to_thread(get_incident, incident_id)
        if fresh is not None and str(fresh["state"]) != str(incident.get("state", "")):
            incident = fresh
            yield _sse("incident", incident)

        state = str(incident.get("state", ""))
        if state in ("RECOVERED", "ESCALATED") and not rows:
            # Drained AND terminal: nothing more will ever arrive on this
            # incident, so close cleanly instead of polling forever.
            yield _sse("done", {"state": state})
            return

        loop_time = asyncio.get_event_loop().time()
        if loop_time - last_emit > _HEARTBEAT_EVERY_S:
            # 🔴 The heartbeat matters: a silent connection gets killed by
            #    proxies and load balancers at 30-60 s. A comment frame does
            #    not reach the client's UI at all.
            last_emit = loop_time
            yield ": heartbeat\n\n"
        await asyncio.sleep(_POLL_S)


@router.get("/{incident_id}/stream")
async def stream(incident_id: int, request: Request) -> StreamingResponse:
    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # stop nginx-style buffering
    }
    return StreamingResponse(
        _gen(incident_id, _resume_cursor(request)),
        media_type="text/event-stream",
        headers=headers,
    )
