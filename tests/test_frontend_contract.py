"""The operator console's API calls must exist on the server (audit G-03).

────────────────────────────────────────────────────────────────────────────
Why this file exists
────────────────────────────────────────────────────────────────────────────
`make verify` was `lint + docs-lint + test`, and none of the three ever looked
at `static/`. The frontend sat entirely outside the quality gate — which is a
plausible root cause for the density of Group F findings in `docs/AUDIT.md`.

This closes the specific gap that costs the most: a UI calling an endpoint the
API does not serve. That failure is invisible until someone clicks the button,
it produces a 404 that looks like a backend outage, and nothing in the build
notices. F-10 was its mirror image — an endpoint with no caller, dead for weeks.

🔴 Deliberately NOT a browser test. There is no Node dependency here, no
   headless Chrome, no `node_modules`: this parses two files as text and
   compares two sets. It runs in milliseconds and it cannot flake, which is
   what makes it something people will keep in the gate rather than disable.
   (Audit stream A3, option (a) — see `docs/AUDIT.md` §8 Q3.)

What it cannot catch: whether a route WORKS, whether the response shape matches
what the UI reads, or anything about rendering. Those need the manual script in
`docs/AUDIT.md` §5. This checks one property, completely.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
API_DIR = ROOT / "oncall" / "api"

#: Paths the frontend fetches, as they appear in `app.js`. Template literals
#: (`${id}`) and query strings are normalised to a comparable shape.
_FETCH_RE = re.compile(r"""["'`](/api/[^"'`\s]*)["'`]""")

#: FastAPI decorators: @router.get("") / @app.get("/api/services") etc.
_ROUTE_RE = re.compile(r"""@(?:router|app)\.(get|post|put|patch|delete)\(\s*["']([^"']*)["']""")
_PREFIX_RE = re.compile(r"""APIRouter\(\s*prefix\s*=\s*["']([^"']*)["']""")


def _normalise(path: str) -> str:
    """Reduce a path to a comparable shape.

    `/api/incidents/${id}/stream` and `/api/incidents/{incident_id}/stream`
    both become `/api/incidents/*/stream`, so a client template literal and a
    server path parameter can be compared directly.
    """
    path = path.split("?", 1)[0].rstrip("/")
    path = re.sub(r"\$\{[^}]*\}", "*", path)   # JS template literal
    path = re.sub(r"\{[^}]*\}", "*", path)     # FastAPI path parameter
    return path or "/"


#: Comments, so a doc comment that QUOTES an endpoint is not read as a call.
_JS_COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def _strip_comments(js: str) -> str:
    """Remove JS comments before scanning for endpoints.

    🔴 This is the FOURTH time this project has hit the same trap, and the
       previous three are written up in `tests/test_queries.py`: a `POOL_MAX`
       grep, a `SELECT *` grep, and an `execute(f"...")` grep each failed on
       the file's own documentation quoting the pattern it was banning.

       It happened again here. The comment in `app.js` explaining why the
       approve/reject endpoints were made literal contains the old
       `/api/incidents/${id}/${action}` string — and the scanner read that
       prose as a real call, then failed the build over an endpoint nobody
       invokes.

       The project's rule from those three: *a rule that gets quoted in its own
       documentation must be enforced by something that understands structure,
       not by matching raw text.* A full JS parser would be the thorough
       version; stripping comments is the 20-line version that handles the
       actual failure, and this file's whole premise is being cheap enough to
       stay in the gate.

    ⚠️ Known limit: a `//` sequence inside a string literal (a URL like
       "https://x") would be treated as a comment start. No endpoint in this
       file's scan is written that way, and if one ever is, this test fails
       loudly rather than passing wrongly.
    """
    return _JS_COMMENT_RE.sub("", js)


def _client_paths() -> set[str]:
    js = _strip_comments((STATIC / "app.js").read_text(encoding="utf-8"))
    return {_normalise(m.group(1)) for m in _FETCH_RE.finditer(js)}


def _server_paths() -> set[str]:
    found: set[str] = set()
    for path in sorted(API_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        prefix_match = _PREFIX_RE.search(text)
        prefix = prefix_match.group(1) if prefix_match else ""
        for _method, route in _ROUTE_RE.findall(text):
            found.add(_normalise(prefix + route))
    return found


@pytest.mark.skipif(not (STATIC / "app.js").is_file(), reason="no static console")
def test_every_frontend_call_has_a_route() -> None:
    """Every `/api/...` the console fetches must be served by `oncall/api/`."""
    client = _client_paths()
    server = _server_paths()

    assert client, "parsed zero API calls from app.js — the regex has drifted"
    assert server, "parsed zero routes from oncall/api/ — the regex has drifted"

    missing = sorted(client - server)
    assert not missing, (
        "static/app.js calls endpoints the API does not serve:\n  "
        + "\n  ".join(missing)
        + "\n\nServer knows:\n  "
        + "\n  ".join(sorted(server))
    )


@pytest.mark.skipif(not (STATIC / "app.js").is_file(), reason="no static console")
def test_no_orphan_endpoints() -> None:
    """Warn on routes nothing calls — F-10 was exactly this.

    `POST /api/faults/{id}/heal` existed with no caller in the UI for weeks:
    you could break one fault but only heal all of them.

    🔴 An allow-list, not a blanket skip. `/health` is called by Docker and by
       `make health`, and the stream endpoint is opened by EventSource rather
       than fetch, so neither appears in a fetch() scan. Anything else with no
       caller is either a missing button or dead code, and both are worth a
       failing test rather than a shrug.
    """
    expected_uncalled = {
        "/health",                          # Docker healthcheck + make health
        "/api/incidents/*/stream",          # opened via EventSource, not fetch
    }
    orphans = sorted(_server_paths() - _client_paths() - expected_uncalled)
    assert not orphans, (
        "these routes have no caller in static/app.js — a missing button, or dead code:\n  "
        + "\n  ".join(orphans)
    )
