import { NextResponse } from "next/server";

/**
 * Reading credentials from either a JSON fetch or a native form POST.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * 🔴 Why the routes accept two content types
 * ────────────────────────────────────────────────────────────────────────────
 * The happy path is `fetch()` with JSON, from the hydrated React form.
 *
 * The path that matters is the other one. When React does not hydrate — a
 * blocked chunk, a browser extension, a runtime error — the browser submits
 * the form natively, form-encoded. If the server only speaks JSON, that
 * submission fails, and the user is left with a login form that silently does
 * nothing.
 *
 * Handling both means login works with JavaScript entirely disabled. That is
 * worth having on its own, and here it is also the thing that closes a
 * security hole: the reason to prefer a native POST over a native GET is that
 * GET puts the password in the URL.
 */
export type Credentials = {
  email: string;
  password: string;
  name: string;
  /** True when this came from a native form POST, so we answer with a redirect
   *  instead of JSON — there is no client-side code to read JSON in that case. */
  isFormPost: boolean;
};

export async function readCredentials(request: Request): Promise<Credentials | null> {
  const contentType = request.headers.get("content-type") ?? "";

  if (contentType.includes("application/json")) {
    try {
      const body = (await request.json()) as Record<string, unknown>;
      return {
        email: String(body.email ?? ""),
        password: String(body.password ?? ""),
        name: String(body.name ?? ""),
        isFormPost: false,
      };
    } catch {
      return null;
    }
  }

  if (
    contentType.includes("application/x-www-form-urlencoded") ||
    contentType.includes("multipart/form-data")
  ) {
    try {
      const form = await request.formData();
      return {
        email: String(form.get("email") ?? ""),
        password: String(form.get("password") ?? ""),
        name: String(form.get("name") ?? ""),
        isFormPost: true,
      };
    } catch {
      return null;
    }
  }

  return null;
}

/**
 * Answer in whichever dialect the caller spoke.
 *
 * 🔴 The error message goes into the redirect URL; the credentials never do.
 *    That distinction is the entire point of this file — a URL is written to
 *    browser history and to every server log that sees the request, so the
 *    only thing allowed in one is something we would be happy to read aloud.
 *
 * 303 (See Other), not 302: it tells the browser to follow up with a GET, so a
 * refresh on the destination does not re-submit the login.
 */
export function respond(
  creds: Credentials,
  outcome: { ok: true } | { ok: false; error: string; status: number },
  mode: "login" | "signup",
  origin: string,
): NextResponse {
  if (outcome.ok) {
    return creds.isFormPost
      ? NextResponse.redirect(new URL("/dashboard", origin), 303)
      : NextResponse.json({ ok: true });
  }

  if (creds.isFormPost) {
    const url = new URL(`/${mode}`, origin);
    url.searchParams.set("error", outcome.error);
    return NextResponse.redirect(url, 303);
  }
  return NextResponse.json({ error: outcome.error }, { status: outcome.status });
}
