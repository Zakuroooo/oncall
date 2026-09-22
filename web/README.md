# `web/` — the public tier

Next.js 16 · React 19 · Tailwind 4 · TypeScript. One runtime dependency: `pg`.

---

## 🔴 The trust boundary — read this first

OnCall runs as **two tiers**, and the split is the security design, not a deployment convenience.

```
┌─ WEB TIER (this directory) ──────────┐   ┌─ CONTROL PLANE (../oncall) ──────┐
│  Next.js · port 3000                 │   │  FastAPI · 127.0.0.1:8000        │
│                                      │   │                                  │
│  • landing page, signup, login       │   │  • the agent loop                │
│  • read-only incident/metric views   │   │  • the action broker             │
│                                      │   │  • the health oracle             │
│  Reads Postgres. Calls the control   │   │                                  │
│  plane over HTTP. Nothing else.      │   │  Holds the DOCKER SOCKET.        │
│                                      │   │                                  │
│  ✅ Safe to expose                    │   │  ❌ NEVER exposed                │
└──────────────────────────────────────┘   └──────────────────────────────────┘
```

**Why this exists.** `CLAUDE.md` §3 hard-codes the control plane's listen address to `127.0.0.1`
because the Docker socket is root-equivalent — anything that can reach that API can, in effect, run as
root on the host. Adding accounts to *that* would be adding a login screen to a root shell.

So accounts were added to a tier that cannot act. **Signing in gives you a view of a control plane,
never control of one.** Actions — inject, approve, reject — stay on the loopback console at
`127.0.0.1:8000`, reachable only by someone already sitting at the machine.

### What this tier is structurally incapable of

- No Docker socket, no `docker` client, no shell.
- No `INSERT`/`UPDATE`/`DELETE` against any control-plane table. `lib/data.ts` contains only `SELECT`.
- Its only writes are its own two tables, `web_users` and `web_sessions`.

The `role` column (`viewer` / `operator`) decides whether action affordances are *rendered*. It does not
grant the ability — the network does, and the network says loopback only.

---

## 🔴 Demo data is labelled, always

The rule that stops this from being a portfolio fake:

```
control plane reachable AND has rows  →  live
otherwise                             →  demo, and the screen says so
```

Every function in `lib/data.ts` returns `{ source, data }`, and every screen renders that flag. `"AND has
rows"` matters: a freshly migrated database is reachable and empty, and showing a new visitor a blank
dashboard reads as a broken product rather than an unstarted one.

**A bug worth keeping in mind, found by running the real thing:** the overview draws from *three*
sources — incidents from Postgres, topology and metrics from the control plane's HTTP API. In the most
ordinary setup they disagree, because the database keeps yesterday's incidents after the API is shut
down. The first version showed one "Live" flag for the whole page, which made a page of mostly-sample
data announce itself as entirely real — the exact false claim the flag was added to prevent. Now every
block, and every stat card, carries its own provenance.

---

## Auth, and why there is no auth library

`NextAuth`/`Auth.js` was rejected on purpose. Two reasons: this project's first rule is that every line
has to be defensible in an interview, and *"the library handles it"* is not an answer to *"how do you
store a password?"*; and every dependency in an auth path is supply-chain surface. `lib/auth.ts` imports
`node:crypto` and nothing else.

| Decision | Why |
|---|---|
| **scrypt**, not bcrypt/argon2 | In Node's standard library — no native module, no compiler at install. Memory-hard: forcing every guess to allocate ~16 MB is what makes a GPU farm useless. |
| Per-user random **salt**, stored with the hash | Without it, two people with the same password get the same hash and one rainbow table cracks both. |
| Parameters stored **in** the hash string | `scrypt$N$r$p$salt$hash`. When 2^14 stops being enough, new hashes get a higher N and old ones still verify. |
| **`timingSafeEqual`**, not `===` | `===` returns early on the first differing byte, so response time leaks the correct value one byte at a time. |
| Sessions store **`sha256(token)`**, not the token | A session token in a database is a live credential. Anyone reading the table — a backup, a log, an injection — could paste it into a cookie. The hash cannot be replayed. |
| Cookie is **`httpOnly` + `SameSite=lax`** | httpOnly means an XSS bug cannot steal the session; SameSite is the CSRF defence for every mutating route. |
| Login returns **one message** for both failures | "No such user" vs "wrong password" is a free account-enumeration oracle. A dummy hash is verified on the unknown-email path so the timing matches too. |
| `getSessionUser()` **fails closed** | An unreachable database returns `null`, never "sure, they're authenticated". |
| The auth gate is a **layout**, not middleware | Every route under `/dashboard` is protected the moment its file exists. Per-page checks fail on the page somebody forgets. |

**What we give up:** OAuth providers, magic links, email verification, and password reset. Real costs,
written down rather than hidden. Reset in particular needs an email sender, and this project takes zero
paid services.

---

## Running it

```bash
make up            # containers
make web-schema    # web_users + web_sessions (idempotent)
make web-install   # once
make web           # http://127.0.0.1:3000
```

For live topology and metrics, also run the control plane:

```bash
make serve         # http://127.0.0.1:8000
```

| Env var | Default | What |
|---|---|---|
| `ONCALL_DB_URL` | `postgresql://oncall:oncall@127.0.0.1:5433/oncall` | Read incidents, read/write accounts |
| `ONCALL_CONTROL_URL` | `http://127.0.0.1:8000` | Topology and metrics; absent → demo |

---

## Layout

```
src/
  lib/
    db.ts        pg pool; tryQuery (may fail) vs query (must not)
    auth.ts      scrypt, sessions, validation
    data.ts      🔴 the real-or-demo switch
    seed.ts      demo rows — same shapes as the real tables
    types.ts     shared shapes, mirroring the DB column names
  components/    Topology, Stepper, Timeline, Verdicts, IncidentList, ui
  app/
    page.tsx           landing
    login/ signup/     auth screens
    api/auth/*         signup · login · logout
    dashboard/
      layout.tsx       🔴 the auth gate
      page.tsx         overview
      incidents/       list + [id] detail
      evidence/        N1–N7
db/web_schema.sql
```
