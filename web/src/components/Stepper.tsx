import type { IncidentState } from "@/lib/types";

/** The pipeline in order. AWAITING_APPROVAL sits in the middle on purpose —
 *  the human gate is a stage of the machine, not an interruption to it. */
const PIPELINE: [IncidentState, string][] = [
  ["DETECTED", "detect"], ["COLLECTING", "collect"], ["DIAGNOSING", "diagnose"],
  ["RETRIEVING", "retrieve"], ["PROPOSING", "propose"], ["AWAITING_APPROVAL", "approve"],
  ["EXECUTING", "execute"], ["VERIFYING", "verify"], ["RECOVERED", "verdict"],
];

export function Stepper({ state }: { state: IncidentState }) {
  const escalated = state === "ESCALATED";
  const index = PIPELINE.findIndex(([s]) => s === state);

  return (
    <ol className="flex overflow-x-auto p-4" aria-label="Incident pipeline">
      {PIPELINE.map(([name, label], i) => {
        let status: "done" | "now" | "wait" | "fail" | "todo" = "todo";
        if (escalated) {
          // From the state alone we cannot tell which stage stopped it, so
          // mark the run as done and put the failure on the verdict. The
          // timeline below carries the detail.
          status = i === PIPELINE.length - 1 ? "fail" : "done";
        } else if (state === "RECOVERED") status = "done";
        else if (i < index) status = "done";
        else if (i === index) status = name === "AWAITING_APPROVAL" ? "wait" : "now";

        return (
          <li key={name} className="relative min-w-[76px] flex-1 text-center">
            {i > 0 && (
              <span
                aria-hidden="true"
                className={`absolute left-[-50%] top-[9px] h-0.5 w-full ${
                  status === "done" || status === "now" || status === "wait" || status === "fail"
                    ? "bg-signal-green" : "bg-ink-500"
                }`}
              />
            )}
            <span
              aria-hidden="true"
              className={`relative z-10 mx-auto mb-2 flex size-5 items-center justify-center rounded-full border-2 text-[10px] font-bold ${
                status === "done" ? "border-signal-green bg-signal-green text-ink-950"
              : status === "now"  ? "border-signal-blue bg-signal-blue text-ink-950 throb"
              : status === "wait" ? "border-signal-amber bg-signal-amber text-ink-950 throb"
              : status === "fail" ? "border-signal-red bg-signal-red text-ink-950"
                                  : "border-ink-500 bg-ink-600 text-mist-500"
              }`}
            >
              {status === "done" ? "✓" : status === "fail" ? "✕" : ""}
            </span>
            <span
              className={`block text-[10px] uppercase tracking-[0.05em] ${
                status === "now"  ? "font-semibold text-signal-blue"
              : status === "wait" ? "font-semibold text-signal-amber"
              : status === "fail" ? "font-semibold text-signal-red"
              : status === "done" ? "text-mist-300" : "text-mist-500"
              }`}
            >
              {label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
