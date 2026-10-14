/**
 * Authentication — accounts, password hashing, sessions.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * 🔴 Why there is no auth library here
 * ────────────────────────────────────────────────────────────────────────────
 * The obvious alternative was NextAuth / Auth.js. It was rejected on purpose,
 * for two reasons:
 *
 *   1. This project's first rule is that every line has to be defensible in an
 *      interview. "NextAuth handles it" is not an answer to "how do you store
 *      a password?". Roughly 200 lines of explicit code is worth more here
 *      than a dependency that hides the interesting part.
 *
 *   2. Every dependency in an auth path is supply-chain surface. This file
 *      imports `node:crypto` and nothing else.
 *
 * What we give up: OAuth providers, magic links, and the account-recovery
 * flows a real product needs. That is a real cost and it is written down in
 * `docs/DECISIONS.md`, not hidden.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * Why scrypt and not bcrypt/argon2
 * ────────────────────────────────────────────────────────────────────────────
 * scrypt is in Node's standard library. bcrypt and argon2 are native modules
 * that need a compiler at install time.
 *
 * The property that matters is that scrypt is **memory-hard**: it deliberately
 * needs a large block of RAM to compute. Plain SHA-256 is fast and parallel,
 * so a GPU tries billions of guesses per second against a stolen hash. Forcing
 * every guess to allocate 64 MB is what makes a GPU farm useless — you run out
 * of memory bandwidth long before you run out of guesses.
 */
import {
  randomBytes,
  scrypt as scryptCallback,
  timingSafeEqual,
  createHash,
  type ScryptOptions,
} from "node:crypto";
import { cookies } from "next/headers";
import { query } from "@/lib/db";
import { REPLAY } from "@/lib/replay";

/**
 * Promise wrapper around `crypto.scrypt`.
 *
 * Hand-written rather than `promisify()` because promisify collapses Node's
 * overloaded signature and loses the options argument — and the options are
 * the entire point here, since they carry N, r, p and maxmem.
 */
function scrypt(
  password: string,
  salt: Buffer,
  keylen: number,
  options: ScryptOptions,
): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    scryptCallback(password, salt, keylen, options, (err, derivedKey) => {
      if (err) reject(err);
      else resolve(derivedKey);
    });
  });
}

/** OWASP's floor for scrypt. N is the cost, and it is a power of two. */
const SCRYPT_N = 16384; // 2^14 → ~16 MB per hash with r=8
const SCRYPT_r = 8;
const SCRYPT_p = 1;
const KEY_LEN = 32;

export const SESSION_COOKIE = "oncall_session";
const SESSION_DAYS = 7;

/* ── Passwords ─────────────────────────────────────────────────────────── */

/**
 * Hash a password for storage.
 *
 * 🔴 The salt is random PER USER and stored alongside the hash. Without it,
 *    two people who pick the same password get the same hash, and one leaked
 *    rainbow table cracks both at once.
 *
 * The parameters are stored in the string too. When 2^14 stops being enough,
 * new hashes get a higher N and old ones still verify with the N they were
 * made with — the format carries its own instructions.
 */
export async function hashPassword(password: string): Promise<string> {
  const salt = randomBytes(16);
  const derived = await scrypt(password, salt, KEY_LEN, {
    N: SCRYPT_N,
    r: SCRYPT_r,
    p: SCRYPT_p,
    // Node refuses scrypt above a default 32 MB budget; N=16384,r=8 needs more.
    maxmem: 64 * 1024 * 1024,
  });
  return [
    "scrypt", SCRYPT_N, SCRYPT_r, SCRYPT_p,
    salt.toString("base64"), derived.toString("base64"),
  ].join("$");
}

/**
 * Check a password against a stored hash.
 *
 * 🔴 `timingSafeEqual`, not `===`.
 *
 * A normal string comparison returns as soon as it finds a differing byte, so
 * a wrong guess that shares the first byte takes measurably longer than one
 * that does not. Over enough requests an attacker reads the correct value out
 * of the timing, one byte at a time. `timingSafeEqual` always compares every
 * byte, so the duration carries no information.
 */
export async function verifyPassword(password: string, stored: string): Promise<boolean> {
  const parts = stored.split("$");
  if (parts.length !== 6 || parts[0] !== "scrypt") return false;

  const [, n, r, p, saltB64, hashB64] = parts;
  const salt = Buffer.from(saltB64, "base64");
  const expected = Buffer.from(hashB64, "base64");

  try {
    const derived = await scrypt(password, salt, expected.length, {
      N: Number(n), r: Number(r), p: Number(p),
      maxmem: 256 * 1024 * 1024,
    });
    // Lengths must match before timingSafeEqual — it throws otherwise, and a
    // thrown exception would itself be a timing signal.
    if (derived.length !== expected.length) return false;
    return timingSafeEqual(derived, expected);
  } catch {
    return false;
  }
}

/* ── Sessions ──────────────────────────────────────────────────────────── */

const hashToken = (token: string) => createHash("sha256").update(token).digest();

export type SessionUser = {
  id: number;
  email: string;
  displayName: string;
  role: "viewer" | "operator";
};

/**
 * Issue a session: random token to the browser, its hash to the database.
 *
 * 32 bytes from the OS CSPRNG. Not `Math.random()`, which is a fast
 * deterministic generator seeded from limited state — predictable output is
 * exactly what a session token must never be.
 */
export async function createSession(userId: number, userAgent: string | null): Promise<string> {
  const token = randomBytes(32).toString("base64url");
  const expires = new Date(Date.now() + SESSION_DAYS * 86_400_000);

  await query(
    `INSERT INTO web_sessions (token_hash, user_id, expires_at, user_agent)
     VALUES ($1, $2, $3, $4)`,
    [hashToken(token), userId, expires, userAgent?.slice(0, 300) ?? null],
  );

  const jar = await cookies();
  jar.set(SESSION_COOKIE, token, {
    // httpOnly: JavaScript cannot read it, so an XSS bug cannot steal the session.
    httpOnly: true,
    // sameSite lax: the cookie is not sent on cross-site POSTs, which is the
    // CSRF defence for every mutating route in this app.
    sameSite: "lax",
    // secure in production only — localhost is http, and a secure cookie would
    // simply never be stored during development.
    secure: process.env.NODE_ENV === "production",
    path: "/",
    expires,
  });
  return token;
}

/** Resolve the current user, or null. Safe to call on every request. */
export async function getSessionUser(): Promise<SessionUser | null> {
  // Public replay: no accounts exist and nothing can be changed, so every
  // visitor is the same read-only guest (see lib/replay.ts).
  if (REPLAY) return { id: 0, email: "read-only replay", displayName: "Guest", role: "viewer" };
  const jar = await cookies();
  const token = jar.get(SESSION_COOKIE)?.value;
  if (!token) return null;

  try {
    const rows = await query<{
      id: number; email: string; display_name: string; role: "viewer" | "operator";
    }>(
      `SELECT u.id, u.email, u.display_name, u.role
         FROM web_sessions s
         JOIN web_users u ON u.id = s.user_id
        WHERE s.token_hash = $1
          AND s.expires_at > now()`,
      [hashToken(token)],
    );
    if (rows.length === 0) return null;
    const row = rows[0];
    return { id: row.id, email: row.email, displayName: row.display_name, role: row.role };
  } catch {
    // Database down. Returning null logs the visitor out for the moment rather
    // than showing them a 500 — and critically it FAILS CLOSED: an unreachable
    // database must never be read as "sure, they're authenticated".
    return null;
  }
}

/** Log out: delete the row, clear the cookie. */
export async function destroySession(): Promise<void> {
  const jar = await cookies();
  const token = jar.get(SESSION_COOKIE)?.value;
  if (token) {
    // 🔴 The row goes first. Clearing the cookie alone would leave a token that
    //    still works if anyone captured it — logout has to revoke, not just forget.
    await query(`DELETE FROM web_sessions WHERE token_hash = $1`, [hashToken(token)]).catch(() => {});
  }
  jar.delete(SESSION_COOKIE);
}

/* ── Validation ────────────────────────────────────────────────────────── */

export const normaliseEmail = (email: string) => email.trim().toLowerCase();

// Deliberately loose. Email validity is decided by delivery, not by a regex,
// and every strict pattern on the internet rejects somebody's real address.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function validateCredentials(email: string, password: string, name?: string): string | null {
  if (!EMAIL_RE.test(email)) return "Enter a valid email address.";
  // Length is the property that actually matters; complexity rules push people
  // toward "Passw0rd!" and away from long passphrases.
  if (password.length < 10) return "Password must be at least 10 characters.";
  if (password.length > 200) return "Password must be under 200 characters.";
  if (name !== undefined && name.trim().length < 2) return "Enter your name.";
  return null;
}
