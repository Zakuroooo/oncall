"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Wordmark } from "@/components/Brand";
import { btnStyles } from "@/components/ui";

type Mode = "login" | "signup";

export function AuthForm({ mode, initialError }: { mode: Mode; initialError?: string }) {
  const router = useRouter();
  // `initialError` is how the no-JS path reports failure: the API route
  // redirects back with ?error=... (a message only — never the credentials).
  const [error, setError] = useState<string | null>(initialError ?? null);
  const [busy, setBusy] = useState(false);

  const isSignup = mode === "signup";

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setBusy(true);

    const form = new FormData(event.currentTarget);
    const payload = {
      email: String(form.get("email") ?? ""),
      password: String(form.get("password") ?? ""),
      ...(isSignup ? { name: String(form.get("name") ?? "") } : {}),
    };

    try {
      const resp = await fetch(`/api/auth/${mode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        setError(data.error ?? "Something went wrong.");
        setBusy(false);
        return;
      }
      // 🔴 refresh() before push(): the layout that guards /dashboard is a
      //    server component, and without a refresh Next can serve it from the
      //    router cache — rendering the signed-out shell for a user who just
      //    signed in.
      router.refresh();
      router.push("/dashboard");
    } catch {
      setError("Could not reach the server.");
      setBusy(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center px-6 py-16">
      <div className="aurora absolute inset-0 -z-10" aria-hidden="true" />

      <div className="w-full max-w-sm">
        <div className="mb-8 flex justify-center">
          <Wordmark sub="self-healing infrastructure" />
        </div>

        <div className="panel p-7">
          <h1 className="text-[21px] font-semibold tracking-tight">
            {isSignup ? "Create your account" : "Sign in"}
          </h1>
          <p className="mt-1.5 text-[13px] text-mist-300">
            {isSignup
              ? "Read-only access to the console. Actions still require a control plane running on your own machine."
              : "Welcome back."}
          </p>

          {/* 🔴 method="post" and action are NOT decoration — they are the
              failure-safe path, and this form has already failed once without
              them.

              If React does not hydrate (a blocked dev chunk, a browser
              extension, a runtime error), onSubmit never attaches and the
              browser performs a NATIVE submission. A <form> with no method
              submits GET — which appends every field to the URL. That is
              exactly how a real password ended up in the address bar, in
              browser history, and nine times over in the dev server log.

              With these two attributes the same failure POSTs form-encoded to
              the API route, which handles it and redirects. So login works
              with JavaScript completely disabled, and no failure mode can put
              a password in a URL. */}
          <form
            onSubmit={onSubmit}
            method="post"
            action={`/api/auth/${mode}`}
            className="mt-6 space-y-4"
            noValidate
          >
            {isSignup && (
              <Field label="Name" name="name" type="text" autoComplete="name" placeholder="Pranay Sarkar" />
            )}
            <Field
              label="Email" name="email" type="email" required
              autoComplete="email" placeholder="you@example.com"
            />
            <Field
              label="Password" name="password" type="password" required
              autoComplete={isSignup ? "new-password" : "current-password"}
              placeholder={isSignup ? "at least 10 characters" : ""}
              hint={isSignup ? "Length matters more than symbols. A passphrase is fine." : undefined}
            />

            {error && (
              <p role="alert" className="rounded-lg border border-signal-red/40 bg-signal-red/10 px-3 py-2 text-[12.5px] text-signal-red">
                {error}
              </p>
            )}

            <button type="submit" disabled={busy} className={`${btnStyles.primary} w-full`}>
              {busy ? "Working…" : isSignup ? "Create account" : "Sign in"}
            </button>
          </form>

          <p className="mt-5 text-center text-[12.5px] text-mist-500">
            {isSignup ? "Already have an account? " : "No account yet? "}
            <Link href={isSignup ? "/login" : "/signup"} className="text-signal-teal hover:underline">
              {isSignup ? "Sign in" : "Create one"}
            </Link>
          </p>
        </div>

        <p className="mt-5 text-center text-[12px] leading-relaxed text-mist-500">
          Signing in gives you a <strong className="text-mist-300">view</strong> of a control plane,
          never control of one. The tier that can restart containers only listens on localhost.
        </p>

        <p className="mt-4 text-center">
          <Link href="/" className="text-[12.5px] text-mist-500 hover:text-signal-teal">← Back to the site</Link>
        </p>
      </div>
    </div>
  );
}

function Field({
  label, name, type, required, autoComplete, placeholder, hint,
}: {
  label: string; name: string; type: string; required?: boolean;
  autoComplete?: string; placeholder?: string; hint?: string;
}) {
  return (
    <div>
      <label htmlFor={name} className="mb-1.5 block text-[12.5px] font-medium text-mist-300">
        {label}
      </label>
      <input
        id={name}
        name={name}
        type={type}
        required={required}
        autoComplete={autoComplete}
        placeholder={placeholder}
        className="w-full rounded-lg border border-ink-500 bg-ink-950 px-3 py-2.5 text-[14px] text-mist-100 placeholder:text-mist-500/70 focus:border-signal-teal focus:outline-none"
      />
      {hint && <p className="mt-1.5 text-[11.5px] text-mist-500">{hint}</p>}
    </div>
  );
}
