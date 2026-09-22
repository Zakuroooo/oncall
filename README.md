# OnCall

A self-healing infrastructure agent where **every remediation is verified by an executed health check under
load — never by the language model.**

The agent detects a broken container, reads its logs, retrieves the matching runbook, proposes one action from
a fixed allowlist, waits for human approval, executes it through a validating broker, and then runs a health
check three times across a 30-second window while generating load to decide whether the fix actually held.

The language model proposes. It never decides.

---

## ⚠️ Security notice — read this first

> **This is a local development tool. It has no authentication. Do not expose it to a network.**

Three reasons, in increasing order of severity:

1. There is no login. Anyone who can reach `127.0.0.1:8000` can approve a remediation.
2. `POST /api/faults/{id}/inject` breaks things **on purpose**. That is the point of the tool.
3. The agent holds a **Docker socket**. A Docker socket is root-equivalent on the host: a container started
   through it can mount `/` and read any file, including SSH keys and `.env`.

Consequently:

| Never | Why |
|---|---|
| Bind to `0.0.0.0` | Exposes the tool to your local network |
| Port-forward, tunnel, or `ngrok` it | Exposes a root-equivalent socket to the internet |
| Deploy it to a cloud VM | The same, with a permanent address |
| Remove the `127.0.0.1:` prefixes from `docker-compose.yml` | Easiest mistake to make, largest blast radius |

The listen address is **hardcoded** to `127.0.0.1` and is deliberately not configurable by an environment
variable. That is not an oversight — a config option that can expose this tool should not exist.

---

## Status

**All phases implemented and the central claim is now measured (2026-08-24):** on a live F4 injection with only a restart applied, the naive check reports success and the sustained oracle correctly reports failure — both directions verified by `tests/test_oracle.py` against the running stack. 152 tests pass; the security invariants are grep-enforced via
`make verify`. The end-to-end path was smoke-tested against the live Docker stack: fault inject → log
collect → state machine → approval gate → broker execution → oracle verdict. The two LLM call sites need a
valid `GROQ_API_KEY` in `.env`; retrieval additionally needs the local embed service (~2.5 GB of ingest deps).

Every number below is still marked either ✅ verified (with a date) or ⚠️ unmeasured. The N-metrics
(N1–N7) become real numbers only when evaluation runs happen; unmeasured numbers stay labelled as predictions.

| Phase | What | Status |
|---|---|---|
| 1 | Docker stack + deep `/health` endpoint | ✅ built & live-tested |
| 2 | Health-check oracle (3 probes / 30 s / under load) | ✅ built (falsifier test: `tests/test_oracle.py`) |
| 3 | Fault injector + action broker + adversarial tests | ✅ built · 37 broker checks |
| 4 | Job queue, state machine, SSE streaming | ✅ built & live-tested |
| 5 | LLM call 1 — diagnosis | ✅ built (needs valid Groq key to run) |
| 6 | Runbook corpus, embeddings, hybrid retrieval | ✅ built (ingest needs `make install-ingest`) |
| 7 | LLM call 2 — action proposal, approval gate, UI | ✅ built & served |
| 8 | Evaluation harness, per-class stats, retrieval ablation | ✅ built (`scripts/run_ablation.py`, `/api/stats`) |
| 9 | Demo, documentation freeze | ⚠️ pending |

---

## Why the health check is the interesting part

Most agent demos declare success when the remediation command exits `0`, or when a single HTTP request returns
`200`, or — increasingly — when a second language model is asked "did that work?". All three are the same
mistake: the success criterion is cheaper than the thing being measured.

This project uses an **executed, sustained** criterion instead:

- The probe hits a **deep** `/health` endpoint that actually touches Postgres and Redis. Not a hardcoded `200`.
- It probes **three times across a 30-second window**. All three must pass.
- A load generator hits the service at ~20 requests/second for the whole window.

The difference is measurable. Consider connection-pool exhaustion (`F4` below) and a plain restart as the
remediation. A restart briefly empties the pool, so the service answers immediately — and then fails again
under load a few seconds later.

| Verdict source | Reported success on F4 restart |
|---|---|
| Naive check (1 probe, no load) | ⚠️ ~100% |
| Sustained check (3 probes, 30 s, ~20 rps) | ⚠️ ~0% |

⚠️ Both figures are predictions until Phase 2 measures them; the measured values will replace them here with a
date. The claim being tested is narrow and falsifiable: *a single probe is measurably wrong.*

⚠️ `3` probes and `30` seconds are chosen values, not derived ones. They are not defended as optimal — only as
strictly better than one probe.

---

## Architecture

```
chaos injector          →  breaks one container on purpose
        ↓
health detector         →  notices, opens an incident
        ↓
log collector           →  container logs, filtered to ERROR/FATAL/CRITICAL
        ↓
🤖 diagnosis (LLM #1)    →  plain-text description of what is wrong
        ↓
hybrid retrieval        →  pgvector cosine + Postgres full-text, fused with RRF
        ↓
🤖 proposal (LLM #2)     →  one action from a 9-item allowlist, as validated JSON
        ↓
🔒 human approval        →  a database row, not a blocked thread
        ↓
validating broker       →  5 validation layers, then a typed handler
        ↓
🔥 health oracle         →  3 probes / 30 s / under load  ← decides success
        ↓
RECOVERED  ·  retry (max 3, re-observing first)  ·  ESCALATED with full trace
```

The pipeline has 16 steps. **Two** of them call a language model. The longest single step — the 30-second
verification — costs zero tokens.

---

## Fault classes

Five injected faults, each with a known correct remediation recorded as ground truth. The ground truth is
**never** visible to the agent — it exists only so results can be scored.

| ID | Fault | Fixed by a restart alone? |
|---|---|---|
| F1 | Process killed (`SIGKILL`) | ✅ yes — deliberate positive control |
| F2 | Cache directory filled to 100% | ❌ no — a new process writes to the same full disk |
| F3 | `ACCESS EXCLUSIVE` lock held on a table | ❌ no — the lock lives in another database session |
| F4 | Connection pool exhausted (`max_connections=5`) | ❌ no — **and this is the interesting one** |
| F5 | Dependency container stopped | ❌ no — the service that *looks* broken is the wrong one |

F4 is the case the whole project is built around: it is the fault where a naive success check and a real one
disagree. `ALTER SYSTEM` persists the low limit to `postgresql.auto.conf`, so a restart does not remove it —
it only empties the pool. An idle pool needs one connection, so a single health probe returns `200`, and it
will still return `200` ten minutes later. The fault is **load-dependent, not time-dependent**: put ~20 rps
through it and the sixth connection is refused with `FATAL: sorry, too many clients already`.

---

## The action boundary

The agent cannot run shell commands. It selects one of **nine** actions, and the selection is constrained at
five layers before anything executes:

1. The action name is a `Literal` in a Pydantic schema — the model cannot emit a tenth value.
2. The schema rejects unknown fields (`extra="forbid"`).
3. File paths are checked against a 2-entry allowlist; config keys against a 3-entry allowlist.
4. Numeric parameters are range-checked.
5. Container names are checked against a 3-entry scope.

Only then is a typed handler invoked. No command string is ever assembled from model output, and no shell is
spawned. This is verifiable from the outside:

```bash
grep -rn "shell=True\|subprocess\|os.system\|popen" oncall/
```

Expected output: nothing.

⚠️ One deliberate exception exists and is documented in the source: `ALTER SYSTEM SET` in PostgreSQL does not
accept bind parameters, so that one statement interpolates. It is safe because the key comes from a 3-item
enum and the value is a range-checked integer — but it is an exception, and it is called out rather than hidden.

---

## How results are reported

- **Per fault class.** Every rate is reported for one fault class at a time.
- **With Wilson score confidence intervals.** A rate is never shown as a bare percentage.
- **With an `underpowered` badge** when the interval is wider than 25 percentage points. At n=20, most of
  them are.
- **There is no aggregate success rate.** Not in the API, not in the UI. An average across five fault classes
  of different difficulty is a number you can inflate by choosing easier faults, so the query was never
  written and the UI row does not exist.

Escalations are counted by type — "gave up after three attempts", "human rejected", "invalid model output",
"no permitted action applies" — because those are four different bugs.

---

## Quick start

⚠️ These commands describe the intended setup and are not yet verified against a running system — see
[Status](#status).

**Requirements:** Docker Desktop with Compose v2, Python 3.11+, ~6 GB free disk, ~5 GB free RAM, and a free
Groq API key.

```bash
git clone <repo-url> oncall
cd oncall
cp .env.example .env      # then fill in GROQ_API_KEY
```

```bash
docker compose up -d
docker compose ps         # expect four containers running
```

```bash
/opt/homebrew/bin/python3.11 -m venv .venv && source .venv/bin/activate   # 3.11 only (see requirements.txt)
pip install -r requirements.txt
make migrate            # schema VERIFY (read-only); apply is `make migrate-write`
```

If you run Colima instead of Docker Desktop, the Python SDK does not understand Docker contexts — add your
socket to `.env`: `DOCKER_HOST=unix:///$HOME/.colima/default/docker.sock`.

Ingest the runbook corpus. **The script writes nothing without `--write`** — run it once without the flag and
read the output first:

```bash
python scripts/ingest_runbooks.py
python scripts/ingest_runbooks.py --write
```

Start the API and the worker in two terminals:

```bash
uvicorn oncall.main:app --host 127.0.0.1 --port 8000
```

```bash
python -m oncall.queue.worker
```

Open `http://127.0.0.1:8000`, inject a fault, and approve the proposal when the timeline stops at
`AWAITING_APPROVAL`.

The worker is a separate process on purpose: an HTTP request should never wait 30 seconds for a verification
window, and incident state lives in Postgres rather than in memory, so an in-flight incident survives a worker
crash.

---

## Running an incident

⚠️ Planned interface — not yet verified against a running system.

Faults are injected deliberately. There are two paths, and the difference matters:

```bash
# UI path — what the demo uses. Goes through POST /api/faults/{id}/inject.
# Click "Inject" on a fault card at http://127.0.0.1:8000

# Script path — what measurement runs use. Bypasses the HTTP endpoint.
python -c "from chaos.inject import inject; inject('F4')"
```

The timeline then advances on its own:

```
DETECTED → COLLECTING → DIAGNOSING → RETRIEVING → PROPOSING → AWAITING_APPROVAL
```

It stops at `AWAITING_APPROVAL` and stays there. **That is not a hang** — the wait is a row in Postgres, not a
blocked thread, so nothing times out and the worker is free to do other work. Approving flips a state and
enqueues a job; it does not execute anything itself.

```
EXECUTING → VERIFYING (~30 s) → RECOVERED  |  retry (max 3)  |  ESCALATED
```

Between incidents the world is reset, so that results are independent:

```bash
python -m chaos.inject --heal-all --write     # or POST /api/faults/heal-all from the UI
```

Without the reset, a disk filled by one fault leaks into the next fault's run and a per-class success rate
silently becomes a rate for a mixture of two faults.

---

## Stack

| Component | Choice | Note |
|---|---|---|
| API | FastAPI + uvicorn | SSE for live incident timelines |
| Database | PostgreSQL + `pgvector` | Also the job queue, via `FOR UPDATE SKIP LOCKED` |
| Vector search | `pgvector` cosine distance | 384-dim |
| Keyword search | Postgres `tsvector` / `ts_rank` | Fused with vector results using reciprocal rank fusion |
| Embeddings | `all-MiniLM-L6-v2`, run locally | ~90 MB; computed once at ingest and cached |
| Language model | Groq free tier | ✅ 200K tokens/day, 8K tokens/minute (verified 2026-08-21) |
| Container control | Docker SDK for Python | No shell, no `subprocess` |
| Frontend | Plain HTML + vanilla JS | No framework; this is an infrastructure project |

No queue broker, no orchestration framework, no agent framework. The queue is a Postgres table because the
project already requires Postgres, and one fewer moving part in a locally-run tool is worth more than the
features a broker would add.

Everything runs on free tiers, on one laptop, with no GPU. There is no training and no fine-tuning.

---

## Repository layout

⚠️ Planned layout — files appear as their phase lands (see [Status](#status)).

| Path | What lives there |
|---|---|
| `docker-compose.yml` | Four containers, every port bound to `127.0.0.1` |
| `oncall/config.py` | Settings. The listen host is hardcoded here and is deliberately not an env var |
| `oncall/main.py` | Entry point that starts the API and the worker |
| `oncall/api/` | FastAPI app: incident endpoints, SSE stream, approval endpoint, stats |
| `oncall/db/` | Connection pool, idempotent migrations, and every SQL statement in the project |
| `oncall/queue/` | Worker loop and the stale-lock reaper. Claiming happens in `oncall/db/queries.py` via `FOR UPDATE SKIP LOCKED` |
| `oncall/agent/` | The ten states and fifteen transitions stored as data, the loop that walks them, and the two files that call a language model: `diagnose.py`, `propose.py` |
| `oncall/llm/` | Provider router with a fallback chain, and the Pydantic schema the model must fill |
| `oncall/retrieval/` | Dense search, sparse search, and the RRF fusion that combines them |
| `oncall/actions/` | The allowlist, the five validation layers, and the typed handlers |
| `oncall/oracle/` | The health probe, the load generator, and the sustained verdict |
| `oncall/metrics/` | Per-class queries and the Wilson interval. No aggregate query exists |
| `chaos/faults.py`, `chaos/inject.py` | The five fault classes and the injector, with `--heal` and `--heal-all` |
| `scripts/seed_shopdb.py` | Synthetic orders for the monitored app. Dry-run unless `--write` |
| `scripts/ingest_runbooks.py` | Corpus fetch, chunk, embed. Dry-run unless `--write` |
| `scripts/run_ablation.py` | The RAG on/off measurement, with seed and prompt SHA pinned |
| `tests/` | Adversarial guardrail tests, each file runnable with plain `python3` |
| `static/` | The UI: one HTML file, one JS file, no build step |

The two oracle tests are named after the claim they check:

```
test_naive_says_true_on_f4()
test_sustained_says_false_on_f4()
```

If the second one ever starts passing for the wrong reason, the project's central claim is broken, and the
test name says so out loud.

---

## Corpus and licensing

The runbook corpus is fetched by a script from public vendor documentation. Every stored record carries its
**source URL, licence, and fetch date**. ⚠️ Licence verification for each source is a prerequisite for the
ingest step: a source whose terms do not permit this use is dropped, even though dropping one makes the corpus
smaller.

No content is hand-written into the corpus. Distractor documents have to be real, otherwise retrieval accuracy
is measured against an exam the author both set and sat.

---

## Not goals

- **Not autonomous.** A human approves every action. That is a design choice, not a limitation to be removed.
- **Not production software.** No authentication, no multi-tenancy, no audit trail beyond the incident table.
- **Not a Kubernetes operator.** Plain Docker Compose, four containers, one laptop.
- **No LLM-as-judge anywhere in the verdict path.** A model is never asked whether a fix worked.

---

## Licence

⚠️ To be added before first release. Code is MIT-intended; the ingested corpus retains the licence of its
source, recorded per record.

