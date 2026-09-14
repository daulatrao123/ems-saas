"use client";
import { label, panel } from "../DashboardHeader";
import { EventRow, fmtTime } from "../types";
import { allocationEventKind, eventTone } from "./types";

// Today's Pi-reported allocation events (energy_allocation_* from the existing pi-events feed), newest first.
// Verbatim messages: this is WHY the plant switched. No state is inferred here.
export function AllocationTimeline({ events, operatingDate }: { events: EventRow[]; operatingDate: string }) {
  const today = events.filter((e) => (e.ts || "").slice(0, 10) === operatingDate);
  return (
    <section data-testid="energy-allocation-timeline" className={`${panel} p-4`}>
      <div className="flex flex-wrap items-center justify-between gap-2"><div data-testid="energy-allocation-timeline-title" className={label}>Auto Allocation Timeline · {operatingDate}</div><span data-testid="energy-allocation-provenance" className="font-mono text-[10px] text-gray-500">Pi-reported events · not live state</span></div>
      {today.length === 0 ? (
        <div data-testid="energy-allocation-empty" className="mt-2 font-mono text-[10px] text-gray-500">NO ALLOCATION EVENTS TODAY{events.length ? ` (${events.length} older allocation event(s) in the feed)` : ""}</div>
      ) : (
        <ol className="mt-2 space-y-1 max-h-64 overflow-y-auto">
          {today.map((e) => { const kind = allocationEventKind(e.level); return (
            <li key={e.id} data-testid={`energy-allocation-event-${e.id}`} className="grid grid-cols-[auto_auto_1fr] gap-x-3 items-start font-mono text-[11px]">
              <span className="text-gray-500">{fmtTime(e.ts)}</span>
              <span data-testid={`energy-allocation-kind-${e.id}`} className={`px-1.5 py-0.5 text-[9px] font-bold border ${eventTone(kind)}`}>{kind.replace(/_/g, " ")}</span>
              <span className="text-gray-300 break-words">{e.msg}</span>
            </li>
          ); })}
        </ol>
      )}
    </section>
  );
}
