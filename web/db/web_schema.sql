-- ═══════════════════════════════════════════════════════════════════════════
-- OnCall web tier — accounts and sessions.
--
-- 🔴 These tables live in `oncall-db` alongside incidents, but they are a
--    SEPARATE TIER with a separate job. Nothing in here grants any ability to
--    touch Docker. See `web/README.md` for the trust boundary; the short
--    version is that logging in gets you a VIEW of the control plane, never
--    control of it.
--
-- Idempotent: safe to run repeatedly (same rule as stack/oncalldb/init.sql).
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS web_users (
    id             bigserial   PRIMARY KEY,

    -- Stored lower-cased by the application, with a UNIQUE index, so
    -- "Pranay@x.com" and "pranay@x.com" cannot become two accounts.
    -- (citext would do this in the database, but it needs an extension and
    -- this project keeps its extension list to pgvector alone.)
    email          text        NOT NULL UNIQUE,
    display_name   text        NOT NULL,

    -- scrypt output, encoded as "scrypt$N$r$p$salt$hash" — see lib/auth.ts.
    -- 🔴 Never the password. Never a reversible encoding of it.
    password_hash  text        NOT NULL,

    -- viewer   → can read incidents, verdicts and metrics
    -- operator → may additionally drive a LOCAL control plane from the UI
    --
    -- 🔴 This role does NOT grant the ability by itself. The control plane
    --    only ever listens on 127.0.0.1, so an operator can only act on a
    --    control plane running on their own machine. The role decides whether
    --    we render the buttons; the network decides whether they can work.
    role           text        NOT NULL DEFAULT 'viewer'
                               CHECK (role IN ('viewer', 'operator')),

    created_at     timestamptz NOT NULL DEFAULT now(),
    last_login_at  timestamptz
);

CREATE TABLE IF NOT EXISTS web_sessions (
    -- 🔥 The PRIMARY KEY is a HASH of the session token, not the token.
    --
    -- Same argument as password_hash, and it is the one people skip. A session
    -- token in the database is a live credential: anyone who can read this
    -- table (a backup, a log, an SQL injection, a support engineer) could paste
    -- it into a cookie and be logged in as that user. Storing sha256(token)
    -- means the table holds nothing that can be replayed.
    token_hash  bytea       PRIMARY KEY,

    user_id     bigint      NOT NULL REFERENCES web_users (id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,

    -- Shown on the account screen so a user can spot a session they do not
    -- recognise. Never used for authentication — a header is attacker-controlled.
    user_agent  text
);

-- Logout-everywhere and the session sweeper both filter by user.
CREATE INDEX IF NOT EXISTS web_sessions_user_idx ON web_sessions (user_id);

-- Partial index for the sweeper: only expired rows are ever deleted, so only
-- expired rows need to be findable quickly.
CREATE INDEX IF NOT EXISTS web_sessions_expiry_idx ON web_sessions (expires_at);
