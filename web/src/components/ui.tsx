import Link from "next/link";
import type { ReactNode } from "react";
import type { DataSource } from "@/lib/types";

/* ── Badges ────────────────────────────────────────────────────────────── */

const TONES = {
  good:  "bg-signal-green/12  text-signal-green  border-signal-green/30",
  bad:   "bg-signal-red/12    text-signal-red    border-signal-red/30",
  wait:  "bg-signal-amber/12  text-signal-amber  border-signal-amber/35",
  work:  "bg-signal-blue/12   text-signal-blue   border-signal-blue/30",
  know:  "bg-signal-violet/12 text-signal-violet border-signal-violet/30",
  teal:  "bg-signal-teal/12   text-signal-teal   border-signal-teal/30",
  idle:  "bg-ink-600 text-mist-500 border-ink-500",
} as const;

export type Tone = keyof typeof TONES;

export function Badge({ tone = "idle", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold tracking-wide whitespace-nowrap ${TONES[tone]}`}
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
      {children}
    </span>
  );
}

/* ── Buttons ───────────────────────────────────────────────────────────── */

const BTN =
  "inline-flex items-center justify-center gap-2 rounded-lg text-[13.5px] font-medium transition-colors disabled:opacity-45 disabled:cursor-not-allowed";

export const btnStyles = {
  primary: `${BTN} bg-signal-teal text-ink-950 hover:bg-signal-teal/85 px-4 py-2 font-semibold`,
  ghost:   `${BTN} bg-ink-700 border border-ink-500 text-mist-100 hover:bg-ink-600 hover:border-ink-400 px-4 py-2`,
  quiet:   `${BTN} text-mist-300 hover:text-signal-teal px-2 py-1`,
};

export function ButtonLink({
  href, variant = "primary", children,
}: { href: string; variant?: keyof typeof btnStyles; children: ReactNode }) {
  return <Link href={href} className={btnStyles[variant]}>{children}</Link>;
}

/* ── Panels ────────────────────────────────────────────────────────────── */

export function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`panel overflow-hidden ${className}`}>{children}</section>;
}

export function PanelHead({
  title, note, action, badge,
}: { title: string; note?: string; action?: ReactNode; badge?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-ink-500 bg-ink-700 px-4 py-2.5">
      <h2 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-mist-300">{title}</h2>
      {badge}
      {note && <span className="ml-auto text-right text-[11.5px] text-mist-500">{note}</span>}
      {action && <div className={note ? "" : "ml-auto"}>{action}</div>}
    </div>
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="px-5 py-9 text-center">
      <div className="text-[13px] font-semibold text-mist-300">{title}</div>
      {hint && <div className="mt-1 text-[12.5px] text-mist-500">{hint}</div>}
    </div>
  );
}

/* ── The data-source flag ──────────────────────────────────────────────── */

/**
 * 🔴 Rendered on every screen that shows numbers. Not optional, not a tooltip.
 *
 * The whole reason `lib/data.ts` returns a source with every result is so that
 * this component can exist. A demo that does not announce itself is a claim
 * you did not intend to make.
 */
export function SourceFlag({ source, note }: { source: DataSource; note?: string }) {
  if (source === "recorded") {
    return (
      <div className="flex items-start gap-2 rounded-lg border border-signal-teal/30 bg-signal-teal/8 px-3 py-2 text-[12.5px] text-mist-300">
        <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-signal-teal" aria-hidden="true" />
        <span><strong className="text-signal-teal">Recorded run.</strong> {note}</span>
      </div>
    );
  }
  if (source === "live") {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-signal-green/25 bg-signal-green/8 px-3 py-2 text-[12.5px] text-mist-300">
        <span className="size-1.5 rounded-full bg-signal-green throb" aria-hidden="true" />
        <span><strong className="text-signal-green">Live.</strong> These are incidents your own control plane recorded.</span>
      </div>
    );
  }
  return (
    <div className="flex items-start gap-2 rounded-lg border border-signal-amber/30 bg-signal-amber/8 px-3 py-2 text-[12.5px] text-mist-300">
      <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-signal-amber" aria-hidden="true" />
      <span><strong className="text-signal-amber">Sample data.</strong> {note}</span>
    </div>
  );
}

/**
 * The compact version, for a panel header.
 *
 * 🔴 Why this exists at all — a bug found while testing the real thing.
 *
 *    The overview draws from THREE sources: incidents come from Postgres,
 *    while topology and metrics come from the control plane's HTTP API. Those
 *    can disagree, and in the most ordinary setup they do: the database keeps
 *    yesterday's incidents after the API is shut down.
 *
 *    The first version put one "Live" flag at the top of the page. So a page
 *    with real incidents, demo topology and demo metrics announced itself as
 *    entirely live — the exact false claim the flag was added to prevent.
 *
 *    One flag per source, attached to the panel it actually describes.
 */
export function SourcePill({ source, note }: { source: DataSource; note?: string }) {
  if (source === "recorded") {
    return (
      <span title={note} className="inline-flex items-center gap-1.5 rounded-full border border-signal-teal/35 bg-signal-teal/10 px-2 py-0.5 text-[10.5px] font-semibold tracking-wide text-signal-teal">
        <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
        recorded
      </span>
    );
  }
  if (source === "live") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-signal-green/30 bg-signal-green/10 px-2 py-0.5 text-[10.5px] font-semibold tracking-wide text-signal-green">
        <span className="size-1.5 rounded-full bg-current throb" aria-hidden="true" />
        live
      </span>
    );
  }
  return (
    <span
      title={note}
      className="inline-flex items-center gap-1.5 rounded-full border border-signal-amber/35 bg-signal-amber/10 px-2 py-0.5 text-[10.5px] font-semibold tracking-wide text-signal-amber"
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
      sample data
    </span>
  );
}

/* ── Formatting ────────────────────────────────────────────────────────── */

export function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  // The date appears only when it is not today — an incident from Tuesday must
  // not read identically to one from two minutes ago.
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay ? t : `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${t}`;
}

export function fmtDuration(seconds: number | null): string {
  if (seconds == null || Number.isNaN(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

export const STATE_TONE: Record<string, Tone> = {
  RECOVERED: "good", ESCALATED: "bad", AWAITING_APPROVAL: "wait",
};

export const STATE_LABEL: Record<string, string> = {
  DETECTED: "Detected", COLLECTING: "Collecting", DIAGNOSING: "Diagnosing",
  RETRIEVING: "Retrieving", PROPOSING: "Proposing", AWAITING_APPROVAL: "Awaiting you",
  EXECUTING: "Executing", VERIFYING: "Verifying",
  RECOVERED: "Recovered", ESCALATED: "Escalated",
};

export const FAULT_TITLE: Record<string, string> = {
  F1: "Process crash", F2: "Disk full", F3: "Database lock",
  F4: "Pool exhaustion", F5: "Dependency down",
};
