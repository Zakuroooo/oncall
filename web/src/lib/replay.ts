/**
 * Public replay mode (`ONCALL_REPLAY=1`, set on the hosted deployment).
 *
 * The control plane holds a Docker socket — root on its host — so it is never
 * exposed to the internet (README, "Security notice"). The hosted site instead
 * replays a snapshot of real incidents recorded on a local run
 * (`web/scripts/snapshot.py`). Nothing in it is edited or invented; every
 * screen is labelled "recorded", and sign-in is disabled because there is no
 * account to protect and nothing a visitor can change.
 */
import snapshot from "@/lib/snapshot.json";

export const REPLAY = process.env.ONCALL_REPLAY === "1";
export const SNAPSHOT = snapshot as unknown as {
  recorded_at: string;
  incidents: unknown[];
  timeline: Record<string, unknown[]>;
  verdicts: Record<string, unknown[]>;
  services: unknown[];
  stats: unknown;
  annotations?: Record<string, string>;
};

const recordedOn = new Date(SNAPSHOT.recorded_at).toLocaleDateString("en-GB", {
  day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata",
});
export const RECORDED_NOTE =
  `Recorded from a real run of the control plane on ${recordedOn}. The control plane is never exposed online — it holds a Docker socket.`;

export function annotationFor(id: number): string | undefined {
  return SNAPSHOT.annotations?.[String(id)];
}
