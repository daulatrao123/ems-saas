"use client";
import { useState } from "react";
import { CommandRow, Device, Slot, TERMINAL, statusTone } from "./types";
import { QueueFn } from "./useOperations";
import { btn, input, label, panel, tone } from "./DashboardHeader";

type Props = { device: Device; code: string; slot: Slot; queue: QueueFn; isPending: (d: string, c: string, s?: string) => boolean; readOnly: boolean; lastCmd?: CommandRow };

export function SlotCard({ device, code, slot, queue, isPending, readOnly, lastCmd }: Props) {
  const active = device.active_slot === code;
  const [days, setDays] = useState(String(slot.target_days || 1));
  const n = Number(days); const validDays = Number.isInteger(n) && n >= 1 && n <= 31;
  const used = slot.used_days || 0, target = slot.target_days || 0;
  const pct = target > 0 ? Math.min(100, Math.round((used / target) * 100)) : 0;
  const remaining = Math.max(0, target - used);
  const state = slot.disabled ? "DISABLED" : active ? "ACTIVE" : "INACTIVE";
  const stateTone = slot.disabled ? "text-gray-500 border-gray-700" : active ? "text-emerald-300 border-emerald-500/50 bg-emerald-500/10" : "text-gray-300 border-[#2a3646]";
  // Physical state is only what the Pi last reported; when offline it is stale, never "guaranteed".
  const physical = !device.connected ? "UNKNOWN (offline)" : slot.physical_toggle || "UNKNOWN";
  const busyAct = isPending(device.id, "set_active_slot", code), busyOff = isPending(device.id, "off_slot", code), busyDays = isPending(device.id, "set_days", code);
  return (
    <section data-testid={`slot-card-${code}`} className={`${panel} ${active ? "border-emerald-500/40" : ""} p-4 flex flex-col gap-3`}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-3">
          <span className={`h-9 w-9 grid place-items-center font-mono text-lg font-bold border ${active ? "border-emerald-400 text-emerald-300" : "border-[#2a3646] text-gray-300"}`}>{code}</span>
          <div><div className="text-sm font-bold text-white leading-tight">{slot.display_name || `Slot ${code}`}</div><div className={label}>Slot {code}</div></div>
        </div>
        <span data-testid={`slot-state-${code}`} className={`px-2 py-0.5 text-[10px] font-bold border ${stateTone}`}>{state}</span>
      </div>

      <div>
        <div className="flex justify-between font-mono text-[11px] text-gray-300">
          <span>TARGET <b className="text-white">{target}</b> D</span><span>USED <b className="text-white">{used}</b> D</span><span data-testid={`slot-remaining-${code}`}><b className="text-amber-300">{remaining}</b> D LEFT</span>
        </div>
        <div className="mt-1.5 h-2 w-full bg-[#0a0e17] border border-[#1e2a3a]"><div className={`h-full ${pct >= 90 ? "bg-red-500" : pct >= 70 ? "bg-amber-400" : "bg-cyan-500"}`} style={{ width: `${pct}%` }} /></div>
        <div className="mt-1 text-right font-mono text-[10px] text-gray-500">{pct}%</div>
      </div>

      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-[10px]">
        <dt className="text-gray-500">PHYSICAL</dt><dd data-testid={`slot-physical-${code}`} className={physical === "ON" ? "text-emerald-300" : "text-gray-300"}>{physical}</dd>
        <dt className="text-gray-500">LAST CMD</dt>
        <dd data-testid={`slot-lastcmd-${code}`} className={lastCmd ? statusTone(lastCmd.status) : "text-gray-500"}>
          {lastCmd ? `${lastCmd.command} · ${lastCmd.status.toUpperCase()}${lastCmd.result ? ` · ${lastCmd.result}` : ""}${!TERMINAL.has(lastCmd.status) ? " …" : ""}` : "—"}
        </dd>
      </dl>

      {!readOnly && !slot.disabled && (
        <div className="grid grid-cols-2 gap-2">
          <button data-testid={`cmd-set_active_slot-${device.id}-${code}`} disabled={active || busyAct} onClick={() => queue(device.id, "set_active_slot", code)} className={`${btn} ${tone.cyan}`}>{busyAct ? "EXECUTING…" : "ACTIVATE"}</button>
          <button data-testid={`cmd-off_slot-${device.id}-${code}`} disabled={!active || busyOff} onClick={() => queue(device.id, "off_slot", code)} className={`${btn} ${tone.red}`}>{busyOff ? "EXECUTING…" : "DEACTIVATE"}</button>
        </div>
      )}
      {!readOnly && !slot.disabled && (
        <div className="flex items-center gap-2 border-t border-[#1e2a3a] pt-3">
          <span className={label}>Days</span>
          <input data-testid={`slot-days-input-${code}`} type="number" min={1} max={31} value={days} onChange={(e) => setDays(e.target.value)} className={`${input} w-16 text-center`} />
          <button data-testid={`slot-days-submit-${code}`} disabled={busyDays || !validDays || n === target} onClick={() => queue(device.id, "set_days", code, { days: n })} className={`${btn} ${tone.amber} flex-1`}>{busyDays ? "SENDING…" : "SET DAYS"}</button>
        </div>
      )}
    </section>
  );
}
