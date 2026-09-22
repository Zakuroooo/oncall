"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Wordmark } from "@/components/Brand";
import type { SessionUser } from "@/lib/auth";

const NAV = [
  { href: "/dashboard", label: "Overview", icon: "◈" },
  { href: "/dashboard/incidents", label: "Incidents", icon: "≡" },
  { href: "/dashboard/evidence", label: "Evidence", icon: "▤" },
];

export function DashboardShell({
  user, children,
}: { user: SessionUser; children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.refresh();
    router.push("/");
  }

  return (
    <div className="grid min-h-screen grid-cols-1 lg:grid-cols-[218px_1fr]">
      <aside className="flex flex-col gap-6 border-b border-ink-500 bg-ink-800 p-4 lg:sticky lg:top-0 lg:h-screen lg:border-b-0 lg:border-r">
        <Wordmark href="/dashboard" sub="incident console" />

        <nav className="flex flex-row gap-1 lg:flex-col" aria-label="Console">
          {NAV.map((item) => {
            // Exact match for the index so /dashboard/incidents does not also
            // light up "Overview"; prefix match for the rest so a detail page
            // keeps its parent highlighted.
            const active =
              item.href === "/dashboard"
                ? pathname === item.href
                : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] transition-colors ${
                  active
                    ? "bg-signal-teal/12 font-semibold text-signal-teal"
                    : "text-mist-300 hover:bg-ink-700 hover:text-mist-100"
                }`}
              >
                <span aria-hidden="true" className="w-4 text-center text-[12px] opacity-80">{item.icon}</span>
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="mt-auto hidden lg:block">
          <div className="rounded-lg border border-ink-500 bg-ink-700 p-3">
            <div className="text-[10px] uppercase tracking-[0.07em] text-mist-500">Verdict contract</div>
            <div className="mt-1 font-mono text-[11px] whitespace-nowrap text-signal-teal">3 probes · 30s · 20 rps</div>
            <div className="mt-0.5 text-[11px] text-mist-500">zero tokens · zero LLM</div>
          </div>

          <div className="mt-3 border-t border-ink-500 pt-3">
            <div className="truncate text-[12.5px] font-medium">{user.displayName || user.email}</div>
            <div className="truncate text-[11px] text-mist-500">{user.email} · {user.role}</div>
            <button onClick={logout} className="mt-2 text-[12px] text-mist-500 hover:text-signal-red">
              Sign out
            </button>
          </div>
        </div>

        {/* Compact identity strip for narrow screens, where the rail is a bar. */}
        <div className="ml-auto flex items-center gap-3 lg:hidden">
          <span className="truncate text-[12px] text-mist-500">{user.email}</span>
          <button onClick={logout} className="text-[12px] text-mist-500 hover:text-signal-red">Sign out</button>
        </div>
      </aside>

      <main className="min-w-0">{children}</main>
    </div>
  );
}
