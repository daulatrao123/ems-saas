"use client";
import { useState } from "react";
import { COMMAND_LABEL, CommandRow, EventRow, fmtDateTime, statusTone } from "./types";
import { btn, input, label, tone } from "./DashboardHeader";

type Line = { ts: string; level: "INFO" | "WARN" | "ERROR"; event: string; details: string; key: string };

// Merges the two existing read-only sources: pi_commands lifecycle (status/result/error) and pi_events (Pi-reported).
// Levels are derived only from real terminal statuses / event types; nothing is invented.
function toLines(commands: CommandRow[], events: EventRow[]): Line[] {
  const out: Line[] = [];
  for (const c of commands) {
    const name = COMMAND_LABEL[c.command] || c.command; const slot = c.slot ? ` · Slot ${c.slot}` : "";
    const level: Line["level"] = c.status === "failed" || c.status === "expired" ? "ERROR" : c.status === "completed" || c.status === "acked" ? "INFO" : "WARN";
    const ts = c.completed_at || c.executing_at || c.delivered_at || c.created_at || "";
    out.push({ key: `c-${c.id}`, ts, level, event: `COMMAND_${c.status.toUpperCase()}`, details: `${name}${slot} · seq ${c.sequence_no}${c.result ? ` · ${c.result}` : ""}${c.error ? ` · ${c.error}` : ""}` });
  }
  for (const e of events) {
    const lv = e.level || ""; const level: Line["level"] = /FAIL|ERROR|REJECT|CRITICAL/.test(lv) ? "ERROR" : /WARN|DRIFT|OFFLINE|ROLLBACK/.test(lv) ? "WARN" : "INFO";
    out.push({ key: `e-${e.id}`, ts: e.ts, level, event: lv || "PI_EVENT", details: e.msg });
  }
  return out.sort((a, b) => (b.ts || "").localeCompare(a.ts || ""));
}

export function OperationalLogs({ commands, events }: { commands: CommandRow[]; events: EventRow[] }) {
  const [limit, setLimit] = useState(20);
  const [level, setLevel] = useState("ALL");
  const lines = toLines(commands, events);
  const filtered = level === "ALL" ? lines : lines.filter((line) => line.level === level);
  const lvTone = { INFO: "text-emerald-400", WARN: "text-amber-300", ERROR: "text-red-400" };
  return (
    <section data-testid="operational-logs" className="border-t border-[#293443] pt-4">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><div data-testid="operational-logs-heading" className={label}>Recorded history · newest first</div><span data-testid="operational-logs-count" className="block mt-1 font-mono text-[11px] text-gray-500">{Math.min(limit, filtered.length)} / {filtered.length} matching · {lines.length} loaded (bounded: 25 commands, 50 Pi events)</span></div>
        <label data-testid="logs-level-label" className="flex items-center gap-2 text-xs text-gray-400">Level<select data-testid="logs-level-filter" aria-label="History severity" value={level} onChange={(event) => { setLevel(event.target.value); setLimit(20); }} className={input}><option data-testid="logs-level-option-all" value="ALL">All events</option><option data-testid="logs-level-option-error" value="ERROR">Errors</option><option data-testid="logs-level-option-warn" value="WARN">Warnings</option><option data-testid="logs-level-option-info" value="INFO">Information</option></select></label>
      </div>
      {filtered.length === 0 ? <div data-testid="logs-empty" className="mt-4 text-sm text-gray-500">{lines.length === 0 ? "No events recorded for this device." : "No loaded events match this level."}</div> : (
        <div className="mt-3 w-full font-mono text-[11px]">
          <div aria-hidden="true" className="hidden lg:grid lg:grid-cols-[160px_56px_224px_minmax(0,1fr)] gap-3 py-1 text-[10px] uppercase text-gray-500"><span>Time</span><span>Level</span><span>Event</span><span>Details</span></div>
          <ol>
            {filtered.slice(0, limit).map((l) => (
              <li key={l.key} data-testid={`log-row-${l.key}`} className="grid grid-cols-[auto_minmax(0,1fr)] lg:grid-cols-[160px_56px_224px_minmax(0,1fr)] gap-x-3 gap-y-1 border-t border-[#1e2a3a] py-2 [overflow-wrap:anywhere]">
                <span data-testid={`log-time-${l.key}`} className="col-span-2 lg:col-span-1 text-gray-400">{fmtDateTime(l.ts)}</span>
                <span data-testid={`log-level-${l.key}`} className={`font-bold ${lvTone[l.level]}`}>{l.level}</span>
                <span data-testid={`log-event-${l.key}`} className={l.event.startsWith("COMMAND_") ? statusTone(l.event.replace("COMMAND_", "").toLowerCase()) : "text-gray-200"}>{l.event}</span>
                <span data-testid={`log-details-${l.key}`} className="col-span-2 lg:col-span-1 text-gray-300">{l.details}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
      {filtered.length > limit && <button data-testid="logs-more" onClick={() => setLimit(limit + 20)} className={`${btn} ${tone.gray} mt-3`}>SHOW MORE LOADED HISTORY</button>}
    </section>
  );
}
