/** Shared shapes. These mirror the control plane's column names exactly, so a
 *  row from Postgres and a row from `seed.ts` are the same object to every
 *  component — which is what lets the demo path and the real path share code
 *  instead of drifting into two different UIs. */

export type IncidentState =
  | "DETECTED" | "COLLECTING" | "DIAGNOSING" | "RETRIEVING" | "PROPOSING"
  | "AWAITING_APPROVAL" | "EXECUTING" | "VERIFYING" | "RECOVERED" | "ESCALATED";

export type Incident = {
  id: number;
  service: string;
  fault_class: string;
  state: IncidentState;
  attempt: number;
  detected_at: string | null;
  closed_at: string | null;
  rag_enabled: boolean;
  seed: number | null;
};

export type TimelineEvent = {
  id: number;
  at: string;
  kind: string;
  payload: Record<string, unknown>;
};

export type Verdict = {
  attempt: number;
  sustained_recovered: boolean;
  naive_recovered: boolean;
  window_s: number | null;
  load_rps: number | null;
};

export type ServiceNode = {
  service: string;
  state: string;
  detail: string;
  checks: Record<string, string>;
};

export type FaultClass = {
  id: string;
  title: string;
  blurb: string;
  symptom: string;
  question: string;
  restartFixes: boolean;
  loadDependent: boolean;
  touches: string[];
  correctShape: string;
};

export type AutofixRow = {
  fault_class: string; rag_enabled: boolean; n: number;
  rate_pct: number | null; ci_low_pct: number | null; ci_high_pct: number | null;
  underpowered: boolean;
};
export type DisagreementRow = {
  fault_class: string; verdicts: number; disagreements: number;
  sustained_pass: number; naive_pass: number;
};
export type AblationRow = {
  fault_class: string; with_rag_pct: number | null; n_with_rag: number;
  no_rag_pct: number | null; n_no_rag: number; delta_pp: number | null;
};
export type HitRateRow = { fault_class: string; n: number; hit_at_1_pct: number; hit_at_3_pct: number };
export type EscalationRow = {
  fault_class: string; voluntary: number; exhausted: number;
  human_rejected: number; broker_rejected: number; infra: number; n: number;
};
export type MttrRow = { fault_class: string; n: number; mean_s: number; p50_s: number; p95_s: number };
export type TokenRow = { fault_class: string; n: number; avg_tokens: number; max_tokens: number };
export type RejectionRow = { fault_class: string; proposals: number; rejected: number };

export type StatsBundle = {
  autofix: AutofixRow[];
  disagreements: DisagreementRow[];
  ablation_delta: AblationRow[];
  retrieval_hits: HitRateRow[];
  escalations: EscalationRow[];
  mttr: MttrRow[];
  tokens: TokenRow[];
  rejection: RejectionRow[];
};

/**
 * Where a screen's numbers came from.
 *
 * 🔴 Every data function returns this alongside its rows, and every screen
 *    renders it. It is not decoration — it is the difference between a demo
 *    and a lie.
 */
export type DataSource = "live" | "demo" | "recorded";

export type Sourced<T> = { source: DataSource; data: T; note?: string };
