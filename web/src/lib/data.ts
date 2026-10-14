/**
 * The data layer — real first, demo only as a labelled fallback.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * 🔥 The one rule this file exists to enforce
 * ────────────────────────────────────────────────────────────────────────────
 * Every function here answers in the same shape: `{ source, data }`, where
 * `source` is "live" or "demo". Every screen renders that flag. So the app is
 * never guessing which world it is in, and neither is the person reading it.
 *
 * The decision is made per query, not once at boot:
 *
 *     control plane reachable AND has rows  →  live
 *     otherwise                             →  demo, and say so
 *
 * "AND has rows" matters. A freshly migrated database is reachable and
 * completely empty. Treating that as "live" would show a brand-new visitor an
 * empty dashboard with no explanation, which reads as a broken product rather
 * than an unstarted one.
 *
 * 🔴 What this file cannot do: write. It has no INSERT, no UPDATE, no path to
 *    Docker. Reading is the entire contract of the web tier.
 */
import { tryQuery } from "@/lib/db";
import { REPLAY, SNAPSHOT, RECORDED_NOTE } from "@/lib/replay";
import {
  DEMO_INCIDENTS, DEMO_TIMELINE, DEMO_VERDICTS, DEMO_SERVICES, DEMO_STATS,
} from "@/lib/seed";
import type {
  Incident, TimelineEvent, Verdict, ServiceNode, StatsBundle, Sourced,
} from "@/lib/types";

const live = <T>(data: T): Sourced<T> => ({ source: "live", data });
const demo = <T>(data: T, note: string): Sourced<T> => ({ source: "demo", data, note });
const recorded = <T>(data: unknown): Sourced<T> => ({ source: "recorded", data: data as T, note: RECORDED_NOTE });

const OFFLINE_NOTE =
  "The control plane is not running, so these are recorded sample runs. Start it locally and this page switches to your own incidents.";
const EMPTY_NOTE =
  "The control plane is running but has not handled an incident yet. Inject a fault and this page switches to your own data.";

/* ── Incidents ─────────────────────────────────────────────────────────── */

export async function getIncidents(limit = 100): Promise<Sourced<Incident[]>> {
  if (REPLAY) return recorded<Incident[]>(SNAPSHOT.incidents.slice(0, limit));
  const result = await tryQuery<Incident>(
    `SELECT id, service, fault_class, state, attempt,
            detected_at, closed_at, rag_enabled, seed
       FROM incidents
      ORDER BY detected_at DESC
      LIMIT $1`,
    [limit],
  );
  if (!result.ok) return demo(DEMO_INCIDENTS, OFFLINE_NOTE);
  if (result.rows.length === 0) return demo(DEMO_INCIDENTS, EMPTY_NOTE);
  return live(result.rows);
}

export async function getIncident(id: number): Promise<Sourced<Incident> | null> {
  if (REPLAY) {
    const hit = (SNAPSHOT.incidents as Incident[]).find((i) => i.id === id);
    return hit ? recorded<Incident>(hit) : null;
  }
  const result = await tryQuery<Incident>(
    `SELECT id, service, fault_class, state, attempt,
            detected_at, closed_at, rag_enabled, seed
       FROM incidents WHERE id = $1`,
    [id],
  );
  if (result.ok && result.rows.length > 0) return live(result.rows[0]);

  const fallback = DEMO_INCIDENTS.find((i) => i.id === id);
  if (!fallback) return null;
  return demo(fallback, result.ok ? EMPTY_NOTE : OFFLINE_NOTE);
}

export async function getTimeline(incidentId: number): Promise<TimelineEvent[]> {
  if (REPLAY) return (SNAPSHOT.timeline[String(incidentId)] ?? []) as TimelineEvent[];
  const result = await tryQuery<TimelineEvent>(
    `SELECT id, at, kind, payload
       FROM events WHERE incident_id = $1
      ORDER BY at, id LIMIT 500`,
    [incidentId],
  );
  if (result.ok && result.rows.length > 0) return result.rows;
  return DEMO_TIMELINE[incidentId] ?? [];
}

export async function getVerdicts(incidentId: number): Promise<Verdict[]> {
  if (REPLAY) return (SNAPSHOT.verdicts[String(incidentId)] ?? []) as Verdict[];
  const result = await tryQuery<Verdict>(
    `SELECT attempt, sustained_recovered, naive_recovered, window_s, load_rps
       FROM verdicts WHERE incident_id = $1
      ORDER BY attempt`,
    [incidentId],
  );
  if (result.ok && result.rows.length > 0) return result.rows;
  return DEMO_VERDICTS[incidentId] ?? [];
}

/* ── Topology ──────────────────────────────────────────────────────────── */

/**
 * Service health.
 *
 * 🔴 Unlike everything else here, this cannot come from Postgres — container
 *    state lives in the Docker daemon, and only the control plane is allowed
 *    to ask it (`oncall/api/app.py`). So the web tier asks the control plane
 *    over HTTP, and gets nothing when it is not running. That is correct: the
 *    web tier having its own Docker access would be the exact hole the whole
 *    trust boundary exists to prevent.
 */
export async function getServices(): Promise<Sourced<ServiceNode[]>> {
  if (REPLAY) return recorded<ServiceNode[]>((SNAPSHOT.services as ServiceNode[]).map((r) => ({ ...r, checks: r.checks ?? {} })));
  const base = process.env.ONCALL_CONTROL_URL ?? "http://127.0.0.1:8000";
  try {
    const resp = await fetch(`${base}/api/services`, {
      signal: AbortSignal.timeout(1500),
      cache: "no-store",
    });
    if (!resp.ok) return demo(DEMO_SERVICES, OFFLINE_NOTE);
    const rows = (await resp.json()) as ServiceNode[];
    return live(rows.map((r) => ({ ...r, checks: r.checks ?? {} })));
  } catch {
    return demo(DEMO_SERVICES, OFFLINE_NOTE);
  }
}

/* ── Metrics ───────────────────────────────────────────────────────────── */

/**
 * The metrics bundle.
 *
 * The control plane already computes these correctly, including the Wilson
 * intervals and the per-attempt hit-rate denominator that `docs/AUDIT.md` M-01
 * was about. Re-implementing that SQL here would create a second definition of
 * every number, and two definitions of one metric is how a project ends up
 * quoting two different auto-fix rates in two different places.
 *
 * So: ask the control plane, or fall back to demo. Never a third implementation.
 */
export async function getStats(): Promise<Sourced<StatsBundle>> {
  if (REPLAY) return recorded<StatsBundle>(SNAPSHOT.stats);
  const base = process.env.ONCALL_CONTROL_URL ?? "http://127.0.0.1:8000";
  try {
    const resp = await fetch(`${base}/api/stats`, {
      signal: AbortSignal.timeout(2500),
      cache: "no-store",
    });
    if (!resp.ok) return demo(DEMO_STATS, OFFLINE_NOTE);
    const bundle = (await resp.json()) as StatsBundle;
    // An empty bundle means "running, nothing measured yet" — show the demo so
    // the page teaches what the metrics ARE, and label it honestly.
    if (!bundle.autofix || bundle.autofix.length === 0) return demo(DEMO_STATS, EMPTY_NOTE);
    return live(bundle);
  } catch {
    return demo(DEMO_STATS, OFFLINE_NOTE);
  }
}

/* ── Derived summary for the overview ──────────────────────────────────── */

export type Summary = {
  open: number;
  awaiting: number;
  recovered: number;
  escalated: number;
  falsePositivesCaught: number;
};

export function summarise(incidents: Incident[], stats: StatsBundle): Summary {
  const terminal = ["RECOVERED", "ESCALATED"];
  return {
    open: incidents.filter((i) => !terminal.includes(i.state)).length,
    awaiting: incidents.filter((i) => i.state === "AWAITING_APPROVAL").length,
    recovered: incidents.filter((i) => i.state === "RECOVERED").length,
    escalated: incidents.filter((i) => i.state === "ESCALATED").length,
    // 🔴 A SUM of counts, never an average of rates. `docs/METRICS.md` §9 bans
    //    aggregate rates over five unequal fault classes because the test mix
    //    can tune them; a count of caught false positives cannot be tuned that way.
    falsePositivesCaught: (stats.disagreements ?? [])
      .reduce((total, row) => total + Number(row.disagreements || 0), 0),
  };
}
