import { REPLAY } from "@/lib/replay";
import { NextResponse } from "next/server";
import { query } from "@/lib/db";
import {
  verifyPassword, createSession, normaliseEmail, hashPassword,
} from "@/lib/auth";
import { rateLimit, clientIp, LOGIN_LIMIT, tooManyRequests } from "@/lib/ratelimit";
import { readCredentials, respond } from "@/lib/formrequest";

/**
 * A hash of a value nobody will ever submit.
 *
 * 🔴 Used to burn the same CPU time when the email does not exist as when it
 *    does. Without it, "unknown email" returns in a millisecond and "known
 *    email, wrong password" takes ~100 ms — so anyone can enumerate which of
 *    your users exist just by timing the responses. Computed once at module
 *    load, not per request.
 */
const DUMMY_HASH_PROMISE = hashPassword("this-password-belongs-to-nobody");

export async function POST(request: Request) {
  if (REPLAY) return new Response("Accounts are disabled on the public replay.", { status: 404 });
  // Accepts JSON (hydrated form) and form-encoded (native submit). See
  // lib/formrequest.ts for why both matter.
  const creds = await readCredentials(request);
  if (!creds) return NextResponse.json({ error: "Malformed request." }, { status: 400 });

  const origin = new URL(request.url).origin;
  const email = normaliseEmail(creds.email);
  const password = creds.password;

  const fail = (error: string, status: number) =>
    respond(creds, { ok: false, error, status }, "login", origin);

  if (!email || !password) return fail("Enter your email and password.", 400);

  // 🔴 Two buckets, both must pass, and both are checked BEFORE the scrypt
  //    verify. Checking after would mean a rate-limited request still costs us
  //    ~16 MB and real CPU — the throttle would be protecting the account
  //    while leaving the machine wide open.
  //
  //    Per-IP alone misses a distributed attempt on one account; per-email
  //    alone misses one source sweeping many accounts. Neither is redundant.
  const ipGate = rateLimit(`login:ip:${clientIp(request)}`, LOGIN_LIMIT.limit, LOGIN_LIMIT.windowSeconds);
  if (!ipGate.allowed) {
    return creds.isFormPost
      ? fail("Too many attempts. Try again later.", 429)
      : tooManyRequests(ipGate.retryAfterSeconds);
  }

  const emailGate = rateLimit(`login:email:${email}`, LOGIN_LIMIT.limit, LOGIN_LIMIT.windowSeconds);
  if (!emailGate.allowed) {
    return creds.isFormPost
      ? fail("Too many attempts. Try again later.", 429)
      : tooManyRequests(emailGate.retryAfterSeconds);
  }

  try {
    const rows = await query<{ id: number; password_hash: string }>(
      `SELECT id, password_hash FROM web_users WHERE email = $1`,
      [email],
    );

    if (rows.length === 0) {
      await verifyPassword(password, await DUMMY_HASH_PROMISE);
      // 🔴 Same message for "no such user" and "wrong password", deliberately.
      //    Distinguishing them is a free account-enumeration oracle.
      return fail("Email or password is incorrect.", 401);
    }

    const ok = await verifyPassword(password, rows[0].password_hash);
    if (!ok) return fail("Email or password is incorrect.", 401);

    await query(`UPDATE web_users SET last_login_at = now() WHERE id = $1`, [rows[0].id]);
    await createSession(rows[0].id, request.headers.get("user-agent"));
    return respond(creds, { ok: true }, "login", origin);
  } catch (err) {
    console.error("[login]", err instanceof Error ? err.message : err);
    return fail("Could not sign in. Is the OnCall database running?", 503);
  }
}
