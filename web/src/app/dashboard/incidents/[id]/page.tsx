import Link from "next/link";
import { notFound } from "next/navigation";
import { getIncident, getTimeline, getVerdicts } from "@/lib/data";
import { Panel, PanelHead, SourceFlag, Badge, btnStyles, fmtDuration, fmtTime, STATE_TONE, STATE_LABEL, FAULT_TITLE } from "@/components/ui";
import { Stepper } from "@/components/Stepper";
import { Timeline } from "@/components/Timeline";
import { Verdicts } from "@/components/Verdicts";
import { annotationFor } from "@/lib/replay";

export const dynamic = "force-dynamic";

export default async function IncidentDetail({
  params,
}: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const incidentId = Number(id);
  if (!Number.isInteger(incidentId)) notFound();

  const result = await getIncident(incidentId);
  if (!result) notFound();

  const incident = result.data;
  const [timeline, verdicts] = await Promise.all([
    getTimeline(incidentId), getVerdicts(incidentId),
  ]);

  // 🔴 Only closed incidents get a duration, and that is not just a lint fix.
  //
  //    The first version used `Date.now()` for open incidents. React's purity
  //    rule flagged it, and the rule is right about more than re-renders: this
  //    page is server-rendered, so "open 4m 12s" would be frozen at whatever
  //    moment the HTML was generated and would then quietly age on screen.
  //    A wrong number that looks precise is worse than no number.
  const elapsed = incident.closed_at && incident.detected_at
    ? (new Date(incident.closed_at).getTime() - new Date(incident.detected_at).getTime()) / 1000
    : null;

  return (
    <div className="mx-auto max-w-6xl px-6 py-7">
      <Link href="/dashboard/incidents" className={`${btnStyles.ghost} mb-4`}>← All incidents</Link>
      {annotationFor(incidentId) && (
        <div className="mb-4 rounded-lg border border-signal-amber/30 bg-signal-amber/8 px-3 py-2 text-[12.5px] text-mist-300">
          <strong className="text-signal-amber">Note on this incident.</strong> {annotationFor(incidentId)}
        </div>
      )}

      <div className="mb-4 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-[22px] font-semibold tracking-tight">
            Incident #{incident.id} — {FAULT_TITLE[incident.fault_class] ?? incident.fault_class}
          </h1>
          <div className="mt-2 flex flex-wrap gap-2 font-mono text-[12px] text-mist-500">
            {[
              incident.fault_class,
              `service ${incident.service}`,
              `attempt ${incident.attempt}`,
              `RAG ${incident.rag_enabled ? "on" : "off"}`,
              incident.seed != null ? `seed ${incident.seed}` : null,
              incident.closed_at ? `took ${fmtDuration(elapsed)}` : `open since ${fmtTime(incident.detected_at)}`,
            ].filter(Boolean).map((chip) => (
              <span key={String(chip)} className="rounded border border-ink-500 bg-ink-700 px-2 py-0.5">{chip}</span>
            ))}
          </div>
        </div>
        <Badge tone={STATE_TONE[incident.state] ?? "work"} title={incident.state}>
          {STATE_LABEL[incident.state] ?? incident.state}
        </Badge>
      </div>

      <div className="mb-4"><SourceFlag source={result.source} note={result.note} /></div>

      <Panel className="mb-4">
        <PanelHead
          title="Pipeline"
          note={
            incident.state === "ESCALATED" ? "escalated — see the timeline for which stage stopped it"
            : incident.state === "RECOVERED" ? "closed by the oracle"
            : `currently ${incident.state.toLowerCase()}`
          }
        />
        <Stepper state={incident.state} />
      </Panel>

      {incident.state === "AWAITING_APPROVAL" && (
        <div className="mb-4 rounded-xl border border-signal-amber bg-gradient-to-b from-signal-amber/10 to-ink-800 p-5">
          <div className="mb-2 flex items-center gap-2.5">
            <span className="size-2.5 rounded-full bg-signal-amber throb" aria-hidden="true" />
            <h2 className="text-[15px] font-semibold text-signal-amber">Waiting on a human</h2>
          </div>
          <p className="text-[13.5px] leading-relaxed text-mist-300">
            The agent has proposed an action and stopped. Nothing will be touched until someone decides.
            {/* 🔴 Approving is an ACTION, and actions belong to the control plane.
                This tier reads; it does not act. Saying so here is more honest
                than rendering a button that would not work. */}
            {" "}Approve or reject it from the control plane console at{" "}
            <code className="rounded bg-ink-600 px-1.5 py-0.5 font-mono text-[12.5px] text-signal-teal">
              http://127.0.0.1:8000
            </code>{" "}
            — this view is read-only by design.
          </p>
        </div>
      )}

      {verdicts.length > 0 && (
        <Panel className="mb-4">
          <PanelHead title="Oracle verdict" note="the only writer of truth in this system" />
          <Verdicts verdicts={verdicts} />
        </Panel>
      )}

      <Panel>
        <PanelHead title="Timeline" note={`${timeline.length} events`} />
        <Timeline events={timeline} />
      </Panel>
    </div>
  );
}
