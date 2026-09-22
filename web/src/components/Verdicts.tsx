import type { Verdict } from "@/lib/types";
import { Badge } from "@/components/ui";

/**
 * The verdict block — the most important thing this console renders.
 *
 * 🔴 Both probes are shown side by side, ALWAYS, even when they agree.
 *
 *    Showing only the sustained result would hide the control group, and the
 *    control group is the entire argument. "Sustained checking beats naive
 *    checking" is a comparison, and a comparison needs both numbers visible or
 *    it is just an assertion.
 */
export function Verdicts({ verdicts }: { verdicts: Verdict[] }) {
  return (
    <div>
      {verdicts.map((v) => {
        const disagreed = v.naive_recovered && !v.sustained_recovered;
        return (
          <div key={v.attempt} className="border-b border-ink-500 p-4 last:border-b-0">
            <div className="mb-3.5 flex flex-wrap items-center gap-2.5">
              <Badge tone={v.sustained_recovered ? "good" : "bad"}>
                {v.sustained_recovered ? "verdict: recovered" : "verdict: not recovered"}
              </Badge>
              <span className="font-mono text-[12px] text-mist-500">attempt {v.attempt}</span>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <Probe
                kind="naive probe (control)"
                passed={v.naive_recovered}
                detail="1 probe · idle · no load"
              />
              <Probe
                kind="sustained probe (oracle)"
                passed={v.sustained_recovered}
                detail={`${v.window_s ?? 30}s window · ~${v.load_rps ?? 20} rps`}
              />
            </div>

            {disagreed && (
              <p className="mt-3 rounded-lg border border-signal-amber bg-signal-amber/10 px-3.5 py-2.5 text-[12.5px] leading-relaxed text-mist-300">
                <strong className="text-signal-amber">The two probes disagreed.</strong>{" "}
                A one-shot check on an idle service said this was fixed; holding ~{v.load_rps ?? 20} rps
                for {v.window_s ?? 30}s showed it was not. That is one false positive a conventional
                self-healing agent would have shipped as a success.
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

function Probe({ kind, passed, detail }: { kind: string; passed: boolean; detail: string }) {
  return (
    <div className={`rounded-lg border bg-ink-700 p-3.5 ${passed ? "border-signal-green/40" : "border-signal-red/40"}`}>
      <div className="text-[10.5px] uppercase tracking-[0.07em] text-mist-500">{kind}</div>
      <div className={`mt-1.5 text-[17px] font-semibold ${passed ? "text-signal-green" : "text-signal-red"}`}>
        {passed ? "recovered" : "not recovered"}
      </div>
      <div className="mt-1 font-mono text-[11.5px] text-mist-500">{detail}</div>
    </div>
  );
}
