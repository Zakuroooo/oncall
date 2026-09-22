import type { ServiceNode } from "@/lib/types";
import { Badge } from "@/components/ui";

/** Anchored, not a substring match — "not_found (NotFound)" must never match a
 *  rule meant for "running". */
const isUp = (s: string) => /^(running|healthy|ok)\b/i.test(s ?? "");

/** shopapp's /health returns per-subsystem strings starting "ok" or "FAIL". */
function checkTone(value: string | undefined): "ok" | "fail" | "unknown" {
  if (!value) return "unknown";
  if (/^ok/i.test(value)) return "ok";
  if (/^fail/i.test(value)) return "fail";
  return "unknown";
}

/**
 * The dependency map.
 *
 * 🔴 This is the view that makes the project's hardest fault legible.
 *
 *    Under F5 the container that LOOKS broken is shopapp; the container that
 *    IS broken is shopcache. Four anonymous status cards show you two red
 *    boxes and no relationship between them. Drawing the dependency edge — and
 *    colouring it from shopapp's own /health sub-checks — shows which way the
 *    failure flows, which is exactly the localisation question the agent has
 *    to answer.
 */
export function Topology({ services }: { services: ServiceNode[] }) {
  const by = Object.fromEntries(services.map((s) => [s.service, s]));
  const checks = by["shopapp"]?.checks ?? {};

  const extras = services.filter(
    (s) => !["shopapp", "shopdb", "shopcache"].includes(s.service),
  );

  return (
    <div className="grid gap-4 p-5 lg:grid-cols-[1fr_150px_1fr] lg:items-center">
      <div className="flex flex-col gap-3">
        <Node node={by["shopapp"]} role="front door · deep health" checks={checks} />
        {extras.map((s) => (
          <Node
            key={s.service}
            node={s}
            role={s.service === "oncall-db" ? "control plane" : "infrastructure"}
          />
        ))}
      </div>

      <div className="flex flex-row gap-6 lg:flex-col lg:gap-8">
        <Edge label="db" tone={checkTone(checks.db)} />
        <Edge label="cache" tone={checkTone(checks.cache)} />
      </div>

      <div className="flex flex-col gap-3">
        <Node node={by["shopdb"]} role="postgres" />
        <Node node={by["shopcache"]} role="redis" />
      </div>
    </div>
  );
}

function Node({
  node, role, checks,
}: { node?: ServiceNode; role: string; checks?: Record<string, string> }) {
  if (!node) return null;
  const up = isUp(node.state);
  const unknown = node.state === "unknown";

  return (
    <div
      className={`rounded-xl border p-3.5 ${
        unknown ? "border-ink-500 bg-ink-700 opacity-70"
          : up ? "border-signal-green/35 bg-ink-700"
               : "border-signal-red/45 bg-gradient-to-b from-signal-red/10 to-ink-700"
      }`}
    >
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span className="text-[13.5px] font-semibold">{node.service}</span>
        <Badge tone={up ? "good" : "bad"}>{node.state}</Badge>
      </div>
      <div className="text-[10.5px] uppercase tracking-[0.06em] text-mist-500">{role}</div>
      {node.detail && (
        <div className="mt-1.5 font-mono text-[11px] break-words text-mist-500">{node.detail}</div>
      )}
      {checks && Object.keys(checks).length > 0 && (
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {Object.entries(checks).map(([key, value]) => {
            const tone = checkTone(value);
            return (
              <span
                key={key}
                title={value}
                className={`rounded border px-1.5 py-0.5 font-mono text-[10.5px] ${
                  tone === "ok"   ? "border-signal-green/30 bg-signal-green/10 text-signal-green"
                : tone === "fail" ? "border-signal-red/35 bg-signal-red/10 text-signal-red"
                                  : "border-ink-500 bg-ink-600 text-mist-500"
                }`}
              >
                {key}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Edge({ label, tone }: { label: string; tone: "ok" | "fail" | "unknown" }) {
  return (
    <div className="flex w-full flex-col gap-1">
      <span
        className={`text-center font-mono text-[10px] ${
          tone === "fail" ? "text-signal-red" : tone === "ok" ? "text-mist-500" : "text-mist-500"
        }`}
      >
        {label} {tone === "ok" ? "✓" : tone === "fail" ? "✕" : ""}
      </span>
      <span
        className={`h-0.5 w-full rounded ${
          tone === "ok"   ? "bg-gradient-to-r from-ink-500 to-signal-green"
        : tone === "fail" ? "bg-gradient-to-r from-ink-500 to-signal-red"
                          : "bg-ink-500"
        }`}
        aria-hidden="true"
      />
    </div>
  );
}
