import { getIncidents } from "@/lib/data";
import { Panel, PanelHead, SourceFlag } from "@/components/ui";
import { IncidentList } from "@/components/IncidentList";

export const metadata = { title: "Incidents — OnCall" };
export const dynamic = "force-dynamic";

const FILTERS = {
  all:       { label: "All",       match: () => true },
  open:      { label: "Open",      match: (s: string) => !["RECOVERED", "ESCALATED"].includes(s) },
  recovered: { label: "Recovered", match: (s: string) => s === "RECOVERED" },
  escalated: { label: "Escalated", match: (s: string) => s === "ESCALATED" },
} as const;

type FilterKey = keyof typeof FILTERS;

export default async function IncidentsPage({
  searchParams,
}: { searchParams: Promise<{ filter?: string }> }) {
  const params = await searchParams;
  const key: FilterKey = (params.filter as FilterKey) in FILTERS
    ? (params.filter as FilterKey)
    : "all";

  const incidents = await getIncidents(200);
  const shown = incidents.data.filter((i) => FILTERS[key].match(i.state));

  return (
    <div className="mx-auto max-w-6xl px-6 py-7">
      <h1 className="text-[22px] font-semibold tracking-tight">Incidents</h1>
      <p className="mt-1 text-[13px] text-mist-300">Newest first. Click any row for the full trace.</p>

      <div className="my-5"><SourceFlag source={incidents.source} note={incidents.note} /></div>

      <Panel>
        <PanelHead
          title={`${shown.length} incident${shown.length === 1 ? "" : "s"}`}
          action={
            // 🔴 Filters are links with a query string, not client state.
            //    That makes a filtered view shareable and bookmarkable, and it
            //    survives a refresh — three things useState gives up for free.
            <div className="flex gap-1.5">
              {(Object.keys(FILTERS) as FilterKey[]).map((f) => (
                <a
                  key={f}
                  href={`/dashboard/incidents?filter=${f}`}
                  className={`rounded-full border px-2.5 py-1 text-[12px] transition-colors ${
                    f === key
                      ? "border-signal-teal bg-signal-teal/12 font-semibold text-signal-teal"
                      : "border-ink-500 bg-ink-700 text-mist-300 hover:text-mist-100"
                  }`}
                >
                  {FILTERS[f].label}
                </a>
              ))}
            </div>
          }
        />
        <IncidentList incidents={shown} />
      </Panel>
    </div>
  );
}
