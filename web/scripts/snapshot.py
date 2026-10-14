"""Snapshot real control-plane data into web/src/lib/snapshot.json for the public replay.

Run from the repo root with the stack and control plane up:
    .venv/bin/python web/scripts/snapshot.py

Only terminal incidents (RECOVERED / ESCALATED) are exported: an incident still
in flight would freeze on a public page as "open forever". Nothing is edited —
rows go out exactly as the control plane wrote them.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import urllib.request

import psycopg
from psycopg.rows import dict_row


import os

DB = os.environ.get("ONCALL_DB_URL", "postgresql://oncall:oncall@127.0.0.1:5433/oncall")
API = "http://127.0.0.1:8000"
OUT = pathlib.Path(__file__).resolve().parents[1] / "src" / "lib" / "snapshot.json"


def _json(v):
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    raise TypeError(type(v))


def _get(path: str):
    req = urllib.request.Request(API + path, headers={"Origin": API})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


with psycopg.connect(DB, row_factory=dict_row) as conn:
    incidents = conn.execute(
        """SELECT id, service, fault_class, state, attempt, detected_at, closed_at, rag_enabled, seed
             FROM incidents WHERE state IN ('RECOVERED','ESCALATED') ORDER BY detected_at DESC"""
    ).fetchall()
    ids = [i["id"] for i in incidents]
    events = conn.execute(
        "SELECT incident_id, id, at, kind, payload FROM events WHERE incident_id = ANY(%s) ORDER BY at, id", (ids,)
    ).fetchall()
    verdicts = conn.execute(
        """SELECT incident_id, attempt, sustained_recovered, naive_recovered, window_s, load_rps
             FROM verdicts WHERE incident_id = ANY(%s) ORDER BY incident_id, attempt""", (ids,)
    ).fetchall()

timeline: dict[str, list] = {}
for e in events:
    timeline.setdefault(str(e.pop("incident_id")), []).append(e)
by_inc: dict[str, list] = {}
for v in verdicts:
    by_inc.setdefault(str(v.pop("incident_id")), []).append(v)

snap = {
    "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    "incidents": incidents,
    "timeline": timeline,
    "verdicts": by_inc,
    "services": _get("/api/services"),
    "stats": _get("/api/stats"),
}
OUT.write_text(json.dumps(snap, default=_json, indent=1))
print(f"wrote {OUT} — {len(incidents)} incidents, {len(events)} events, {len(verdicts)} verdicts")
