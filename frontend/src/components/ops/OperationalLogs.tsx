"use client";
import { useState } from "react";
import { COMMAND_LABEL, CommandRow, EventRow, fmtDateTime, statusTone } from "./types";
import { btn, label, panel, tone } from "./DashboardHeader";

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
  const lines = toLines(commands, events);
  const lvTone = { INFO: "text-emerald-400", WARN: "text-amber-300", ERROR: "text-red-400" };
  return (
    <section data-testid="operational-logs" className={`${panel} p-4`}>
      <div className="flex items-center justify-between"><div className={label}>Operational Logs · newest first</div><span className="font-mono text-[10px] text-gray-500">{Math.min(limit, lines.length)} / {lines.length} loaded (bounded: 25 commands, 50 Pi events)</span></div>
      {lines.length === 0 ? <div className="mt-3 text-sm text-gray-500">No events recorded for this device.</div> : (
        <table className="mt-3 w-full font-mono text-[11px]">
          <thead className="text-left text-[10px] uppercase tracking-wide text-gray-500"><tr><th className="py-1 pr-3 w-40">Time</th><th className="py-1 pr-3 w-14">Level</th><th className="py-1 pr-3 w-56">Event</th><th className="py-1">Details</th></tr></thead>
          <tbody>
            {lines.slice(0, limit).map((l) => (
              <tr key={l.key} data-testid="log-row" className="border-t border-[#1e2a3a] align-top">
                <td className="py-1.5 pr-3 text-gray-400 whitespace-nowrap">{fmtDateTime(l.ts)}</td>
                <td className={`py-1.5 pr-3 font-bold ${lvTone[l.level]}`}>{l.level}</td>
                <td className={`py-1.5 pr-3 ${l.event.startsWith("COMMAND_") ? statusTone(l.event.replace("COMMAND_", "").toLowerCase()) : "text-gray-200"}`}>{l.event}</td>
                <td className="py-1.5 text-gray-300 break-words">{l.details}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {lines.length > limit && <button data-testid="logs-more" onClick={() => setLimit(limit + 20)} className={`${btn} ${tone.gray} mt-3`}>SHOW MORE</button>}
    </section>
  );
}
