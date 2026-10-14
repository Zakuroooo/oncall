import { REPLAY } from "@/lib/replay";
import { NextResponse } from "next/server";
import { query } from "@/lib/db";
import {
  hashPassword, createSession, normaliseEmail, validateCredentials,
} from "@/lib/auth";
import { rateLimit, clientIp, SIGNUP_LIMIT, tooManyRequests } from "@/lib/ratelimit";
import { readCredentials, respond } from "@/lib/formrequest";

export async function POST(request: Request) {
  if (REPLAY) return new Response("Accounts are disabled on the public replay.", { status: 404 });
  const creds = await readCredentials(request);
  if (!creds) return NextResponse.json({ error: "Malformed request." }, { status: 400 });

  const origin = new URL(request.url).origin;
  const email = normaliseEmail(creds.email);
  const password = creds.password;
  const name = creds.name.trim();

  const fail = (error: string, status: number) =>
    respond(creds, { ok: false, error, status }, "signup", origin);

  const invalid = validateCredentials(email, password, name);
  if (invalid) return fail(invalid, 400);

  // Looser than login — a real person mistyping should not be locked out — but
  // not absent: without it one script can fill the users table.
  const gate = rateLimit(`signup:ip:${clientIp(request)}`, SIGNUP_LIMIT.limit, SIGNUP_LIMIT.windowSeconds);
  if (!gate.allowed) {
    return creds.isFormPost
      ? fail("Too many attempts. Try again later.", 429)
      : tooManyRequests(gate.retryAfterSeconds);
  }

  const passwordHash = await hashPassword(password);

  try {
    // 🔴 The UNIQUE constraint decides, not a SELECT-then-INSERT.
    //    Checking first and inserting second is a race: two simultaneous
    //    signups both see "free" and both insert. `ON CONFLICT DO NOTHING`
    //    makes the database the single arbiter, which it is anyway.
    const rows = await query<{ id: number }>(
      `INSERT INTO web_users (email, display_name, password_hash)
       VALUES ($1, $2, $3)
       ON CONFLICT (email) DO NOTHING
       RETURNING id`,
      [email, name, passwordHash],
    );

    if (rows.length === 0) {
      return fail("An account with that email already exists.", 409);
    }

    await createSession(rows[0].id, request.headers.get("user-agent"));
    return respond(creds, { ok: true }, "signup", origin);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error("[signup]", message);
    // 🔴 The internal message never reaches the client — it can carry table
    //    names, column names, and connection strings.
    return fail("Could not create the account. Is the OnCall database running?", 503);
  }
}
