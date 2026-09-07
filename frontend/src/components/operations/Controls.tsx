"use client";
import { useState } from "react";

export type QueueFn = (deviceId: string, command: string, slot: string, params?: Record<string, unknown>) => Promise<boolean>;

export const ctlBtn = "px-3 py-1.5 text-[10px] font-bold rounded border transition-colors disabled:opacity-50";
export const ctlInput = "rounded border border-gray-700 bg-gray-950 px-2 py-1 text-xs text-gray-100 focus:border-cyan-500 focus:outline-none";
export const tone = {
  cyan: "bg-cyan-500/15 text-cyan-300 border-cyan-500/30 hover:bg-cyan-500/25",
  amber: "bg-amber-500/15 text-amber-300 border-amber-500/30 hover:bg-amber-500/25",
  red: "bg-red-500/15 text-red-300 border-red-500/30 hover:bg-red-500/25",
  gray: "bg-gray-800 text-gray-300 border-gray-700 hover:bg-gray-700",
};

// Per-slot target-days editor -> one set_days command (backend: slot + params.days 1-31).
export function SlotDaysInput({ deviceId, slot, current, queue }: { deviceId: string; slot: string; current: number; queue: QueueFn }) {
  const [days, setDays] = useState(String(current || 1)); const [busy, setBusy] = useState(false);
  const n = Number(days); const valid = Number.isInteger(n) && n >= 1 && n <= 31;
  const submit = async () => { setBusy(true); await queue(deviceId, "set_days", slot, { days: n }); setBusy(false); };
  return (
    <div className="mt-2 flex items-center gap-1" data-testid={`slot-days-${deviceId}-${slot}`}>
      <input data-testid={`slot-days-input-${slot}`} type="number" min={1} max={31} value={days} onChange={(e) => setDays(e.target.value)} className={`${ctlInput} w-16`} aria-label={`Target days for slot ${slot}`} />
      <button data-testid={`slot-days-submit-${slot}`} onClick={submit} disabled={busy || !valid || n === current} className={`${ctlBtn} ${tone.amber}`}>SET DAYS</button>
    </div>
  );
}

// Reset Days / OFF ALL / Restart / Reboot — device-wide, confirmed, no slot/params.
export function SystemControls({ deviceId, queue }: { deviceId: string; queue: QueueFn }) {
  const [busy, setBusy] = useState<string | null>(null);
  const fire = async (command: string, label: string) => {
    if (!window.confirm(`${label} on this Pi? The command is queued and executed on the Pi's next sync.`)) return;
    setBusy(command); await queue(deviceId, command, "", {}); setBusy(null);
  };
  const items: [string, string, string][] = [["reset_days", "RESET DAYS", tone.amber], ["off_all", "OFF ALL", tone.red], ["restart", "RESTART EMS", tone.gray], ["reboot", "REBOOT PI", tone.gray]];
  return (
    <div className="flex flex-wrap gap-2" data-testid={`system-controls-${deviceId}`}>
      {items.map(([cmd, label, t]) => (
        <button key={cmd} data-testid={`cmd-${cmd}-${deviceId}`} onClick={() => fire(cmd, label)} disabled={busy !== null} className={`${ctlBtn} ${t}`}>{busy === cmd ? "QUEUING…" : label}</button>
      ))}
    </div>
  );
}

// Monthly reset day (1-28) -> set_reset_day {day}. Current value is not exposed by /api/admin/dashboard (backend untouched).
export function ResetDayControl({ deviceId, queue }: { deviceId: string; queue: QueueFn }) {
  const [day, setDay] = useState("1"); const [busy, setBusy] = useState(false);
  const n = Number(day); const valid = Number.isInteger(n) && n >= 1 && n <= 28;
  const submit = async () => {
    if (!window.confirm(`Set monthly reset day to ${n}?`)) return;
    setBusy(true); await queue(deviceId, "set_reset_day", "", { day: n }); setBusy(false);
  };
  return (
    <div className="flex items-center gap-2" data-testid={`reset-day-control-${deviceId}`}>
      <span className="text-[10px] uppercase tracking-wide text-gray-500">Monthly reset day</span>
      <input data-testid={`reset-day-input-${deviceId}`} type="number" min={1} max={28} value={day} onChange={(e) => setDay(e.target.value)} className={`${ctlInput} w-16`} />
      <button data-testid={`cmd-set_reset_day-${deviceId}`} onClick={submit} disabled={busy || !valid} className={`${ctlBtn} ${tone.amber}`}>SET RESET DAY</button>
    </div>
  );
}

// LCD message -> lcd_display {line1, line2, duration}. 16x2 preview.
export function LcdControl({ deviceId, queue }: { deviceId: string; queue: QueueFn }) {
  const [l1, setL1] = useState(""); const [l2, setL2] = useState(""); const [dur, setDur] = useState("10"); const [busy, setBusy] = useState(false);
  const submit = async () => { setBusy(true); await queue(deviceId, "lcd_display", "", { line1: l1, line2: l2, duration: Number(dur) || 10 }); setBusy(false); };
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid={`lcd-control-${deviceId}`}>
      <span className="text-[10px] uppercase tracking-wide text-gray-500">LCD</span>
      <input data-testid={`lcd-line1-${deviceId}`} maxLength={16} placeholder="Line 1 (16 chars)" value={l1} onChange={(e) => setL1(e.target.value)} className={`${ctlInput} w-40 font-mono`} />
      <input data-testid={`lcd-line2-${deviceId}`} maxLength={16} placeholder="Line 2 (16 chars)" value={l2} onChange={(e) => setL2(e.target.value)} className={`${ctlInput} w-40 font-mono`} />
      <input data-testid={`lcd-duration-${deviceId}`} type="number" min={1} max={300} value={dur} onChange={(e) => setDur(e.target.value)} className={`${ctlInput} w-16`} title="Seconds" />
      <button data-testid={`cmd-lcd_display-${deviceId}`} onClick={submit} disabled={busy || (!l1 && !l2)} className={`${ctlBtn} ${tone.cyan}`}>SEND TO LCD</button>
      {(l1 || l2) && <pre className="rounded bg-emerald-950/60 border border-emerald-800 px-2 py-1 text-[10px] leading-4 text-emerald-300 font-mono">{l1.padEnd(16)}{"\n"}{l2.padEnd(16)}</pre>}
    </div>
  );
}
