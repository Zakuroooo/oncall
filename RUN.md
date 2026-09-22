# Running OnCall locally

> Verified end to end on **2026-08-27** on this machine: F1 injected → diagnosed → approved →
> executed → oracle verdict **RECOVERED**. Every command below was actually run.

---

## What I need from you

**Almost nothing.** I checked, and your machine is already set up:

| | Status |
|---|---|
| Groq API key | ✅ live — test call returned `{"status":"ok"}`, model `openai/gpt-oss-120b` |
| Runbook corpus | ✅ 205 runbooks, all 205 embedded |
| shopdb seed data | ✅ 5,003 orders |
| `sentence-transformers` (embeddings) | ✅ installed |
| Docker / colima | ✅ running, 4 containers healthy |
| Web tier accounts table | ✅ created |

**One decision is yours, and only one:**

### 🔴 `git init` — I have not run it

This is the first commit of the project and the point of no return for history, so it is your call, not
mine. It is the last S1 item in `docs/AUDIT.md` (**G-01**).

Right now `make lint` check 6 reports *"git repo nahi hai"*, and every `.gitignore` protection
`CLAUDE.md` §3 claims — `docs/` never committed, `.env` never committed, `web/.env.local` never
committed — is **enforcing nothing**, because there is no git to honour it.

When you want it:

```bash
git init && git add -A && git status --short | head -30
```

Then **check the staging list before committing** — nothing from `docs/`, no `.env`, no
`node_modules/`, no `CLAUDE.md`, no `AGENTS.md`. Verify directly:

```bash
git check-ignore -v docs/ .env CLAUDE.md AGENTS.md web/.env.local web/node_modules
```

Every one of those must print a matching rule. If any does not, stop and tell me.

*(Optional, low stakes: you never answered whether chat replies should stay Hinglish — `docs/AUDIT.md`
§8 Q1. Code is English now, `docs/` is Hinglish. Chat is the only undecided surface.)*

---

## Seeing it — the short version

Two servers, two URLs. **They are different things on purpose.**

```bash
make up          # 1. containers
make serve       # 2. control plane  → http://127.0.0.1:8000
make web         # 3. web tier       → http://localhost:3000
```

| URL | What | Can it act? |
|---|---|---|
| **http://127.0.0.1:8000** | Operator console — inject faults, approve, reject | **Yes.** Holds the Docker socket. Never expose it. |
| **http://localhost:3000** | Landing page, signup/login, read-only dashboard | No. Reads only. Safe to expose. |

**To break something and watch it get fixed, use `:8000`.** `:3000` is the product surface — the
landing page, accounts, and a read-only view of the same incidents.

---

## Full walkthrough, from cold

### 1. Containers

```bash
make up
```

Four containers: `shopapp`, `shopdb`, `shopcache` (the victim stack) and `oncall-db` (the brain).
Wait for healthy:

```bash
make ps
```

If Docker itself is not running, start it first — on this machine that is `colima start`.

### 2. Check the schema

```bash
make migrate
```

Read-only. It should say the schema is complete. `oncall-db` applies `stack/oncalldb/init.sql` on first
boot, so normally there is nothing to do. If it complains: `make nuke && make up` (⚠️ deletes shopdb data).

### 3. Web tier tables — once

```bash
make web-schema
```

Creates `web_users` and `web_sessions`. Idempotent; safe to re-run.

### 4. Start the control plane

```bash
make serve
```

Starts **three** processes and prints their pids:

```
API    → pid …  (log: /tmp/oncall-api.log)
worker → pid …  (log: /tmp/oncall-worker.log)
embed  → pid …  (log: /tmp/oncall-embed.log)
embed  : ready
```

> 🔴 **The embed service is new here.** Until 2026-08-27 `make serve` started only the API and the
> worker — the embed service had no make target at all, so the normal way to start OnCall left it down
> and *every RAG incident escalated with `retrieval_unavailable`.* Found by running it, not by reading it.

Wait for `embed : ready`. The first start loads a ~90 MB model and can take 30-60 seconds; after that it
is cached.

Sanity check:

```bash
make health
```

### 5. Start the web tier

```bash
make web-install     # once, ~380 packages
make web             # http://localhost:3000
```

Open **http://localhost:3000**, click **Create account**, and you are in. Password must be 10+ characters.

### 6. Break something and watch it

Open **http://127.0.0.1:8000** → **Chaos lab** → **Inject F1**.

Or from the shell:

```bash
make inject-write F=F1
```

You will be taken to the incident. It runs itself to **Awaiting you** in a few seconds, shows you the
diagnosis, the proposed action, the exact parameters, and the log lines it based that on. Click
**Approve & execute**.

Then the oracle runs: a naive one-shot probe, then ~20 rps held for 30 seconds with three more probes.
Both results are recorded and shown side by side.

**Start with F1** — it is the one a restart genuinely fixes, so you see a clean success first.
**Then try F4** — that is the one the whole project exists for.

Reset whenever you like:

```bash
make heal-all
```

### 7. Stopping

```bash
make stop     # API + worker + embed  (containers stay up)
make down     # containers too        (data kept)
```

---

## What a real run looked like

Verified on 2026-08-27, incident #7, F1:

```
DETECTED → COLLECTING → DIAGNOSING → RETRIEVING → PROPOSING → AWAITING_APPROVAL
   (5 seconds, two LLM calls, 3 runbooks retrieved)

diagnosis : "shopapp container exited with code 137 … health endpoint unreachable"
proposal  : restart_service { "service": "shopapp" }
            ← the correct action for F1

[approved by hand]

EXECUTING → VERIFYING → RECOVERED   (30 second oracle window)

VERDICT   naive = False    sustained = True    30s @ 20 rps
```

🔴 **Look at that verdict.** The naive probe said *not recovered* and the sustained probe said
*recovered* — the naive one fired before the container had finished booting.

That is the **opposite** disagreement from the F4 story, and it is why `N1b` counts only
*naive-true / sustained-false*. Counting disagreements in both directions would let ordinary startup
timing inflate the headline number. There is a test asserting exactly this
(`tests/test_metrics.py::test_n1b_counts_only_naive_true_sustained_false`).

**And an honest one:** on F4 (incident #6) the agent got it wrong. It read the 500s from `/orders`,
diagnosed "unhandled exception in the ASGI application", decided none of its nine allowed actions could
fix a code bug, and **escalated voluntarily** rather than guessing. Retrieval also missed —
`keyword_candidates: 0`, and the top runbook was `rb-pg-logging-config`, not the max-connections one.

F4 is the hard one. A wrong-but-honest escalation is the failure mode you want; `docs/RETRO.md` §1
pre-registered that this could happen.

---

## Checking it yourself

```bash
make verify      # lint (6 security invariants) + docs-lint + 161 tests
make code-lint   # source-is-English burn-down (currently 5,178 lines / 52 files)
cd web && npx tsc --noEmit && npx eslint .
```

---

## When something is wrong

| Symptom | Cause | Fix |
|---|---|---|
| `make serve` → "schema adhoora" | Schema incomplete | `make nuke && make up` |
| Incidents escalate with `retrieval_unavailable` | Embed service down | `make embed`, wait for ready |
| `embed : ⚠️ not ready yet` | Model still loading | Wait, then `tail /tmp/oncall-embed.log` |
| Dashboard says "sample data" | Control plane not running | `make serve` |
| Signup → "Is the OnCall database running?" | `oncall-db` down, or `make web-schema` never run | Both |
| Login → "Too many attempts" | Rate limit: 5 per 15 min | Wait, or restart `make web` (the limiter is in-process) |
| `docker` errors | Docker not running | `colima start` |

Logs: `/tmp/oncall-api.log`, `/tmp/oncall-worker.log`, `/tmp/oncall-embed.log`.

---

## Where to read next

| | |
|---|---|
| How the whole thing works | `docs/HOW_IT_WORKS.md` |
| What is broken / was fixed | `docs/AUDIT.md` |
| Why each decision was made | `docs/DECISIONS.md` |
| The web tier's trust boundary | `web/README.md` |
