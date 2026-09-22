import Link from "next/link";
import type { Incident } from "@/lib/types";
import { Badge, Empty, fmtTime, STATE_TONE, STATE_LABEL, FAULT_TITLE } from "@/components/ui";

const COLS = "grid-cols-[54px_minmax(0,1fr)_132px_64px_52px_112px]";

export function IncidentList({ incidents }: { incidents: Incident[] }) {
  if (incidents.length === 0) {
    return <Empty title="Nothing on fire" hint="Inject a fault from your local control plane to open an incident." />;
  }

  return (
    <div>
      <div className={`grid ${COLS} gap-3 border-b border-ink-500 px-4 py-2 text-[10.5px] uppercase tracking-[0.07em] text-mist-500`}>
        <div>id</div><div>fault</div><div>state</div><div>attempt</div><div>rag</div><div>detected</div>
      </div>
      {incidents.map((inc) => (
        // 🔴 A Link, not a div with an onClick. It is focusable, activates on
        //    Enter, opens in a new tab on middle-click, and is announced as a
        //    link — all of which a div has to reimplement badly.
        <Link
          key={inc.id}
          href={`/dashboard/incidents/${inc.id}`}
          className={`grid ${COLS} items-center gap-3 border-b border-ink-500 px-4 py-2.5 last:border-b-0 hover:bg-ink-700 focus-visible:bg-ink-700`}
        >
          <span className="truncate font-mono text-[12.5px] text-mist-500">#{inc.id}</span>
          <span className="min-w-0">
            <span className="block truncate text-[13.5px] font-medium">
              {inc.fault_class} · {FAULT_TITLE[inc.fault_class] ?? "incident"}
            </span>
            <span className="block truncate text-[11.5px] text-mist-500">{inc.service}</span>
          </span>
          <span className="min-w-0">
            <Badge tone={STATE_TONE[inc.state] ?? "work"} title={inc.state}>
              {STATE_LABEL[inc.state] ?? inc.state}
            </Badge>
          </span>
          <span className="truncate font-mono text-[12px] text-mist-300">{inc.attempt}</span>
          <span className="truncate font-mono text-[12px] text-mist-300">{inc.rag_enabled ? "on" : "off"}</span>
          <span className="truncate font-mono text-[12px] text-mist-300">{fmtTime(inc.detected_at)}</span>
        </Link>
      ))}
    </div>
  );
}
