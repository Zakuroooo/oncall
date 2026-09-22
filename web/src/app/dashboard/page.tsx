import Link from "next/link";
import { getIncidents, getServices, getStats, summarise } from "@/lib/data";
import { Panel, PanelHead, SourcePill, btnStyles } from "@/components/ui";
import { Topology } from "@/components/Topology";
import type { DataSource } from "@/lib/types";
import { IncidentList } from "@/components/IncidentList";

export const metadata = { title: "Overview — OnCall" };
// Never cached: an incident console showing a snapshot from a minute ago is
// worse than one that admits it cannot reach the control plane.
export const dynamic = "force-dynamic";

export default async function Overview() {
  const [incidents, services, stats] = await Promise.all([
    getIncidents(), getServices(), getStats(),
  ]);
  const summary = summarise(incidents.data, stats.data);
  const open = incidents.data.filter((i) => !["RECOVERED", "ESCALATED"].includes(i.state));

  // 🔴 Three independent sources on one page, and they routinely disagree —
  //    the database keeps yesterday's incidents after the control plane API is
  //    shut down. So each block states its own provenance rather than the page
  //    making one claim on behalf of all three. See SourcePill for the bug
  //    this replaced.
  const mixed = new Set([incidents.source, services.source, stats.source]).size > 1;

  return (
    <div className="mx-auto max-w-6xl px-6 py-7">
      <div className="mb-5">
        <h1 className="text-[22px] font-semibold tracking-tight">Overview</h1>
        <p className="mt-1 text-[13px] text-mist-300">
          Every verdict here came from a probe under load — never from a model&rsquo;s opinion.
        </p>
      </div>

      {mixed && (
        <p className="mb-5 rounded-lg border border-ink-500 bg-ink-700/60 px-3 py-2 text-[12.5px] leading-relaxed text-mist-300">
          <strong className="text-mist-100">Partly live.</strong> Some blocks below come from your own
          control plane and some are recorded samples — each one says which. Incidents come from the
          database; topology and metrics need the control plane API running at{" "}
          <code className="rounded bg-ink-600 px-1.5 py-0.5 font-mono text-[12px] text-signal-teal">
            127.0.0.1:8000
          </code>.
        </p>
      )}

      {/* 🔴 Even within one row the sources differ: the false-positive count is
          computed from the metrics bundle, the other three from the incident
          rows. So the marker goes on the CARD, not on the row. A row-level
          label would be a smaller version of the same false claim. */}
      <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat value={summary.falsePositivesCaught} label="false positives caught" hero source={stats.source} />
        <Stat value={summary.open} label="open now" tone={summary.open ? "amber" : undefined} source={incidents.source} />
        <Stat value={summary.awaiting} label="awaiting you" tone={summary.awaiting ? "amber" : undefined} source={incidents.source} />
        <Stat value={summary.recovered} label="recovered" tone="green" source={incidents.source} />
      </div>

      <Panel className="mb-4">
        <PanelHead
          title="Live topology"
          badge={<SourcePill source={services.source} note={services.note} />}
          note="shopapp is probed deeply (db · cache · disk); the rest report container state"
        />
        <Topology services={services.data} />
      </Panel>

      <Panel>
        <PanelHead
          title="Active incidents"
          badge={<SourcePill source={incidents.source} note={incidents.note} />}
          action={<Link href="/dashboard/incidents" className={btnStyles.quiet}>View all →</Link>}
        />
        <IncidentList incidents={open.slice(0, 6)} />
      </Panel>
    </div>
  );
}

function Stat({
  value, label, hero, tone, source,
}: {
  value: number; label: string; hero?: boolean;
  tone?: "amber" | "green"; source: DataSource;
}) {
  const valueClass = hero ? "text-signal-teal"
    : tone === "amber" ? "text-signal-amber"
    : tone === "green" ? "text-signal-green"
    : "text-mist-100";
  return (
    <div className={`rounded-xl border p-3.5 ${hero ? "border-signal-teal/50 bg-signal-teal/8" : "border-ink-500 bg-ink-700"}`}>
      <div className="flex items-start justify-between gap-2">
        <div className={`font-mono text-[26px] font-semibold leading-none ${valueClass}`}>{value}</div>
        {source === "demo" && (
          <span
            title="Sample data — this number does not come from your control plane."
            className="mt-0.5 shrink-0 rounded border border-signal-amber/35 bg-signal-amber/10 px-1.5 py-0.5 text-[9.5px] font-semibold uppercase tracking-wide text-signal-amber"
          >
            sample
          </span>
        )}
      </div>
      <div className="mt-2 text-[10.5px] uppercase tracking-[0.07em] text-mist-500">{label}</div>
    </div>
  );
}
