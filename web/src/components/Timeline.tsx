import type { TimelineEvent } from "@/lib/types";
import { Badge, fmtTime } from "@/components/ui";

/**
 * The timeline.
 *
 * 🔴 React escapes every interpolated value automatically, which removes an
 *    entire bug class the previous vanilla-JS console had to fight by hand:
 *    these payload fields come out of a JSONB column that was written from
 *    model output, and several of them (confidence, rrf score, line counts)
 *    only LOOK reliably numeric.
 *
 *    The one thing to never do here is dangerouslySetInnerHTML. There is no
 *    reason to, and the fields that would tempt you are precisely the
 *    model-authored ones.
 */
export function Timeline({ events }: { events: TimelineEvent[] }) {
  if (events.length === 0) {
    return <div className="px-5 py-8 text-center text-[13px] text-mist-500">No events recorded.</div>;
  }
  return (
    <ol className="p-4">
      {events.map((ev) => (
        <li key={ev.id} className="relative grid grid-cols-[58px_20px_minmax(0,1fr)] gap-2.5 py-1">
          <span className="pt-1 text-right font-mono text-[11px] text-mist-500">{fmtTime(ev.at)}</span>
          <span className="relative flex justify-center">
            <span className="absolute inset-y-0 w-0.5 bg-ink-500" aria-hidden="true" />
            <span className={`relative mt-1.5 size-2.5 rounded-full ring-4 ring-ink-800 ${dotColour(ev)}`} aria-hidden="true" />
          </span>
          <span className="min-w-0 py-0.5 text-[13px] leading-relaxed">{render(ev)}</span>
        </li>
      ))}
    </ol>
  );
}

function dotColour(ev: TimelineEvent): string {
  const p = ev.payload ?? {};
  if (ev.kind === "escalate" || (ev.kind === "execute" && p.phase === "rejected")) return "bg-signal-red";
  if (ev.kind === "verify") return p.sustained_recovered === true ? "bg-signal-green" : "bg-signal-red";
  if (ev.kind === "retrieve") return "bg-signal-violet";
  if (ev.kind === "approval" || ev.kind === "propose") return "bg-signal-amber";
  return "bg-signal-blue";
}

const Mono = ({ children }: { children: React.ReactNode }) => (
  <code className="rounded bg-ink-600 px-1.5 py-0.5 font-mono text-[12px] text-signal-teal">{children}</code>
);
const Dim = ({ children }: { children: React.ReactNode }) => (
  <span className="text-mist-500">{children}</span>
);

function render(ev: TimelineEvent): React.ReactNode {
  const p = ev.payload as Record<string, never>;

  switch (ev.kind) {
    case "state_change":
      return <>state → <strong>{String(p.to)}</strong> <Dim>{String(p.why ?? "")}</Dim></>;

    case "logs":
      return (
        <>
          collected logs — kept <strong>{String(p.kept_line_count)}</strong> of {String(p.raw_line_count)} lines
          {p.injection_suspected ? <> <Badge tone="bad">injection suspected</Badge></> : null}
        </>
      );

    case "diagnose":
      return (
        <>
          diagnosis: <strong>{String(p.summary)}</strong>{" "}
          <Dim>{String(p.suspected_service)} · confidence {String(p.confidence)}</Dim>
        </>
      );

    case "retrieve":
      return (
        <>
          retrieved <strong>{String(p.kept)}</strong> runbooks — top <Mono>{String(p.top_runbook_id)}</Mono>{" "}
          <Dim>rrf {String(p.top_rrf_score)}</Dim>
        </>
      );

    case "propose":
      return (
        <>
          proposal: <Mono>{String(p.action)}</Mono> <Dim>{JSON.stringify(p.params)}</Dim>
        </>
      );

    case "approval":
      return <>human decision: <strong>{String(p.decision)}</strong></>;

    case "execute":
      if (p.phase === "rejected") return <>broker rejected at layer {String(p.layer)} — {String(p.reason)}</>;
      if (p.phase === "result") return <>action finished: exit {String(p.exit_code)} — {String(p.detail)}</>;
      return <>executing <Mono>{String(p.action)}</Mono>…</>;

    case "verify":
      if (p.recheck_5min_ok !== undefined) {
        return <>5-minute recheck: {p.recheck_5min_ok ? "still healthy" : <strong>FAILED — the 30s window was too short</strong>}</>;
      }
      return (
        <>
          oracle: sustained <strong>{String(p.sustained_recovered)}</strong>, naive {String(p.naive_recovered)}
          {p.disagreed ? <strong> — they disagreed</strong> : null}
        </>
      );

    case "escalate":
      return <span className="text-signal-red">escalated ({String(p.reason)}) — {String(p.detail ?? "")}</span>;

    default:
      // 🔴 Unknown kinds are visible, not blank. A silently dropped row would
      //    let the backend add an event type the UI never shows again.
      return <Dim>unrecognised event: {ev.kind}</Dim>;
  }
}
