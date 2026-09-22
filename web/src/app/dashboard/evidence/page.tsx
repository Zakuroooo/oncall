import { getStats } from "@/lib/data";
import { Panel, SourceFlag } from "@/components/ui";
import type { AutofixRow } from "@/lib/types";

export const metadata = { title: "Evidence — OnCall" };
export const dynamic = "force-dynamic";

export default async function Evidence() {
  const stats = await getStats();
  const s = stats.data;

  return (
    <div className="mx-auto max-w-6xl px-6 py-7">
      <h1 className="text-[22px] font-semibold tracking-tight">Evidence</h1>
      <p className="mt-1.5 max-w-3xl text-[13.5px] leading-relaxed text-mist-300">
        Per fault class only. There is deliberately no aggregate auto-fix row on this page — an average
        over five unequal fault classes is a number you can make prettier by changing the test mix.
      </p>

      <div className="my-5"><SourceFlag source={stats.source} note={stats.note} /></div>

      <Metric
        id="N1" name="Auto-fix rate" def="per fault class × RAG arm · Wilson 95% interval"
        headers={["fault", "rag", "n", "rate with 95% interval"]}
        rows={s.autofix.map((r) => [
          r.fault_class, r.rag_enabled ? "on" : "off", String(r.n), <CiCell key="ci" row={r} />,
        ])}
      />

      <Metric
        id="N1b" name="Naive vs sustained disagreements" def="false positives the sustained oracle caught"
        headers={["fault", "verdicts", "naive said fixed", "sustained said fixed", "disagreements"]}
        rows={s.disagreements.map((r) => [
          r.fault_class, String(r.verdicts), String(r.naive_pass), String(r.sustained_pass),
          <span key="d" className={Number(r.disagreements) > 0 ? "font-semibold text-signal-red" : ""}>
            {r.disagreements}
          </span>,
        ])}
      />

      <Metric
        id="N3" name="Retrieval ablation" def="same seeds, RAG on vs off"
        headers={["fault", "with rag", "n", "without rag", "n", "delta"]}
        rows={s.ablation_delta.map((r) => [
          r.fault_class, pct(r.with_rag_pct), String(r.n_with_rag), pct(r.no_rag_pct), String(r.n_no_rag),
          // 🔴 A missing arm renders as "no paired arm", never as 0. Missing and
          //    zero mean opposite things, and printing 0 would claim RAG did nothing.
          r.delta_pp == null
            ? <span key="d" className="text-mist-500">no paired arm</span>
            : <span key="d" className={r.delta_pp > 0 ? "text-signal-green" : r.delta_pp < 0 ? "text-signal-red" : "text-mist-500"}>
                {r.delta_pp > 0 ? "+" : ""}{r.delta_pp} pp
              </span>,
        ])}
      />

      <Metric
        id="N4" name="Retrieval hit rate" def="per (incident, attempt) — random baseline ≈ 2-4%"
        headers={["fault", "attempts", "hit@1", "hit@3"]}
        rows={s.retrieval_hits.map((r) => [
          r.fault_class, String(r.n), pct(r.hit_at_1_pct), pct(r.hit_at_3_pct),
        ])}
      />

      <Metric
        id="N7" name="Escalations by cause" def="a single escalation number would hide the interesting split"
        headers={["fault", "voluntary", "exhausted", "you rejected", "broker blocked", "infra", "total"]}
        rows={s.escalations.map((r) => [
          r.fault_class, String(r.voluntary), String(r.exhausted), String(r.human_rejected),
          String(r.broker_rejected), String(r.infra), String(r.n),
        ])}
      />

      <Metric
        id="N2" name="Time to recovery" def="detected → closed"
        headers={["fault", "n", "mean", "p50", "p95"]}
        rows={s.mttr.map((r) => [
          r.fault_class, String(r.n), `${r.mean_s}s`, `${r.p50_s}s`, `${r.p95_s}s`,
        ])}
      />

      <Metric
        id="N6" name="Tokens per incident" def="what one incident costs"
        headers={["fault", "n", "avg", "max"]}
        rows={s.tokens.map((r) => [r.fault_class, String(r.n), String(r.avg_tokens), String(r.max_tokens)])}
      />

      <Metric
        id="N5" name="Human rejection rate" def="your judgement, not the oracle's"
        headers={["fault", "proposals", "rejected", "rate"]}
        rows={s.rejection.map((r) => [
          r.fault_class, String(r.proposals), String(r.rejected),
          r.proposals ? `${Math.round((100 * r.rejected) / r.proposals)}%` : "—",
        ])}
      />
    </div>
  );
}

const pct = (v: number | null) => (v == null ? "—" : `${v}%`);

/**
 * One metric block.
 *
 * 🔴 `headers` and `rows` are passed together and their lengths are asserted
 *    in development. The previous console had a table whose header list and
 *    cell list drifted apart, so "95% CI" was a permanently empty column and
 *    the warning badge sat under a blank heading (`docs/AUDIT.md` F-04).
 */
function Metric({
  id, name, def, headers, rows,
}: {
  id: string; name: string; def: string;
  headers: string[]; rows: React.ReactNode[][];
}) {
  if (process.env.NODE_ENV !== "production") {
    const bad = rows.find((r) => r.length !== headers.length);
    if (bad) throw new Error(`Metric ${id}: ${headers.length} headers but a row has ${bad.length} cells.`);
  }

  return (
    <Panel className="mb-4">
      <div className="flex items-baseline gap-2.5 border-b border-ink-500 bg-ink-700 px-4 py-2.5">
        <span className="font-mono text-[11px] font-bold text-signal-teal">{id}</span>
        <span className="text-[13px] font-semibold">{name}</span>
        <span className="ml-auto text-right text-[11.5px] text-mist-500">{def}</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr>
              {headers.map((h, i) => (
                <th key={i} className="border-b border-ink-500 px-4 py-2 text-left text-[10.5px] font-semibold uppercase tracking-[0.06em] whitespace-nowrap text-mist-500">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={headers.length} className="px-4 py-6 text-center text-[13px] text-mist-500">
                  no data yet
                </td>
              </tr>
            ) : rows.map((cells, i) => (
              <tr key={i}>
                {cells.map((c, j) => (
                  <td key={j} className="border-b border-ink-500 px-4 py-2 font-mono text-[12.5px] whitespace-nowrap last:border-b-0">
                    {c}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

/**
 * A rate drawn together with its confidence interval.
 *
 * 🔴 One cell, not two columns. `docs/METRICS.md` §1's G3 gate says no number
 *    claims anything without an interval — and splitting them across columns is
 *    exactly how they end up separated.
 */
function CiCell({ row }: { row: AutofixRow }) {
  if (row.rate_pct == null) return <span className="text-mist-500">no data</span>;
  const lo = Number(row.ci_low_pct), hi = Number(row.ci_high_pct), v = Number(row.rate_pct);
  return (
    <span className="flex min-w-[210px] items-center gap-2.5">
      <span className="w-[46px] shrink-0">{v}%</span>
      <span className="relative h-[18px] min-w-[100px] flex-1">
        <span className="absolute left-0 right-0 top-2 h-0.5 rounded bg-ink-600" />
        <span
          className="absolute top-[7px] h-1 rounded border border-signal-blue bg-signal-blue/20"
          style={{ left: `${lo}%`, width: `${Math.max(hi - lo, 1)}%` }}
        />
        <span
          className="absolute top-1 -ml-[5px] size-2.5 rounded-full border-2 border-ink-800 bg-signal-blue"
          style={{ left: `${v}%` }}
        />
      </span>
      <span className="shrink-0 text-mist-500">{lo}–{hi}</span>
      {row.underpowered && (
        <span className="shrink-0 text-[10.5px] text-signal-amber" title="too few runs to conclude anything">
          ⚠ underpowered
        </span>
      )}
    </span>
  );
}
