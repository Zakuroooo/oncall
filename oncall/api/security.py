"""Same-origin guard — the control plane's only request-level defence.

────────────────────────────────────────────────────────────────────────────
🔴 Why this file exists at all — audit findings S-01 / S-02
────────────────────────────────────────────────────────────────────────────
`THREAT_MODEL.md` §7's answer to "this process can drive a root-equivalent
Docker socket" was: bind to 127.0.0.1 and nothing else can reach it.

That answer is incomplete, and the gap is specific. Binding to loopback stops
a *remote* attacker opening a socket to us. It does not stop **the user's own
browser** being told to do it by some unrelated page. Any website the operator
visits can run:

    fetch("http://127.0.0.1:8000/api/faults/F1/inject?write=true",
          {method: "POST", mode: "no-cors"});

`mode: "no-cors"` means the attacker cannot READ our reply — but the request is
still SENT, and we still act on it. There is no auth, no cookie, and no body to
mismatch, so nothing stopped it. Same vector reaches `/approve`, which is the
human safety gate the entire design leans on.

Two headers close it, and both are set by the browser itself — a page cannot
forge either one:

  `Origin`  Present on every cross-origin request (and every POST). If it is
            there and it is not us, this is a cross-site request. Reject.

  `Host`    Closes DNS rebinding: an attacker points `evil.com` at 127.0.0.1,
            so the browser now believes it is same-origin and sends no Origin
            at all — but the `Host` header still reads `evil.com`. Reject
            anything that is not a loopback name.

🔴 This is NOT authentication. It stops a web page from driving the control
   plane; it does nothing about a local process, which can talk to the socket
   directly and skip browsers entirely. That residual risk is real and stays
   accepted — the mitigation for it is the action allowlist and the human
   approval gate, not this file.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse

from oncall.config import LISTEN_HOST, LISTEN_PORT

#: Hostnames we accept in the `Host` header. Anything else is a rebinding
#: attempt, or a deployment this project explicitly does not support.
_ALLOWED_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})

#: Exact origins allowed to make requests. Built from the same constants the
#: server binds to, so the two can never drift apart.
_ALLOWED_ORIGINS = frozenset(
    f"http://{h}:{LISTEN_PORT}" for h in ("127.0.0.1", "localhost", LISTEN_HOST)
)


def _hostname_of(host_header: str) -> str:
    """Strip the `:port` off a Host header. IPv6 literals keep their brackets."""
    host = host_header.strip()
    if host.startswith("["):  # [::1]:8000
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if ":" in host else host


def _reject(reason: str) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": f"blocked: {reason}"})


async def same_origin_guard(
    request: Request,
    call_next: Callable[[Request], Awaitable[object]],
) -> object:
    """Reject cross-site and rebound requests before any route runs.

    Deliberately runs on EVERY method, not just the mutating ones. A GET here
    leaks the incident timeline, the model's reasoning, and the container
    inventory — that is worth protecting too, and a rule with an exception is
    a rule someone will find the exception to.
    """
    host = _hostname_of(request.headers.get("host", ""))
    if host not in _ALLOWED_HOSTNAMES:
        # Empty Host lands here too, which is correct: HTTP/1.1 requires it.
        return _reject(f"unexpected Host header {host!r}")

    origin = request.headers.get("origin")
    if origin is not None and origin not in _ALLOWED_ORIGINS:
        # Present-and-wrong is an attack. Absent is a normal same-origin GET,
        # or curl — both fine, and both already covered by the Host check.
        return _reject(f"cross-origin request from {origin!r}")

    return await call_next(request)
