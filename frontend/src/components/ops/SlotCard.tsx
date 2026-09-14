"use client";
import { ReactNode, useState } from "react";
import { CommandRow, Device, Slot, LastResponse as Response } from "./types";
import { QueueFn, SlotConfigFn } from "./useOperations";
import { btn, input, label, panel, tone } from "./DashboardHeader";
import { LastResponse } from "./LastResponse";
import { WingEnergyCard } from "./energy/WingEnergyCard";
import { AllocationConfig, WingCode, WingSummary } from "./energy/types";

type Props = { device: Device; code: WingCode; slot?: Slot; queue: QueueFn; setSlotConfig: SlotConfigFn; isPending: (d: string, c: string, s?: string) => boolean; readOnly: boolean;
  lastCmd?: CommandRow; lastResponse?: Response | null; wing?: WingSummary; allocation: AllocationConfig | null; activeGenerationWing?: string; allotmentInput?: ReactNode };
const telemetry = (v: string | undefined, offline: boolean) => offline ? "UNKNOWN (offline)" : v === "ON" || v === "OFF" ? v : "UNKNOWN";
const numberOrNull = (v: number | undefined) => v != null && Number.isFinite(v) ? v : null;

export function SlotCard({ device, code, slot, queue, setSlotConfig, isPending, readOnly, lastCmd, lastResponse, wing, allocation, activeGenerationWing, allotmentInput }: Props) {
  const active = device.active_slot === code;
  const [days, setDays] = useState(String(slot?.target_days ?? ""));
  const [seenTarget, setSeenTarget] = useState(slot?.target_days);
  if (seenTarget !== slot?.target_days) { setSeenTarget(slot?.target_days); setDays(String(slot?.target_days ?? "")); }
  const n = Number(days); const validDays = Number.isInteger(n) && n >= 1 && n <= 31;
  const used = numberOrNull(slot?.used_days), target = numberOrNull(slot?.target_days);
  const pct = target != null && target > 0 && used != null ? Math.round((used / target) * 100) : null;
  const remaining = target != null && used != null ? Math.max(0, target - used) : null;
  const state = !slot || typeof slot.disabled !== "boolean" ? "UNAVAILABLE" : slot.disabled ? "DISABLED" : active ? "ACTIVE" : "INACTIVE";
  const stateTone = slot?.disabled ? "text-gray-500 border-gray-700" : active ? "text-emerald-300 border-emerald-500/50 bg-emerald-500/10" : "text-gray-300 border-[#2a3646]";
  const toggleIn = telemetry(slot?.toggle_input, !device.connected);
  // Only the corresponding Pi feedback field; never infer from toggle, command, ACK or active_slot.
  const contactor = device.hardware_fault || device.feedback_hardware_installed !== true || slot?.feedback_enabled === false ? "UNKNOWN" : telemetry(slot?.physical_toggle, !device.connected);
  const busyCfg = isPending(device.id, "slot-config", code);
  const busyAct = isPending(device.id, "set_active_slot", code), busyOff = isPending(device.id, "off_slot", code), busyDays = isPending(device.id, "set_days", code);
  const displayName = slot?.display_name?.trim();
  const customName = displayName && ![code, `slot ${code}`, `wing ${code}`].some((v) => v.toLowerCase() === displayName.toLowerCase()) ? displayName : null;
  return (
    <section data-testid={`slot-card-${code}`} className={`${panel} ${active ? "border-emerald-500/40" : ""} p-4 flex flex-col gap-3 min-w-0`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0"><h2 data-testid={`slot-heading-${code}`} className="text-base font-bold text-white">Wing {code}</h2><div data-testid={`slot-name-${code}`} className={`${label} break-words`}>Slot {code}{customName ? ` · ${customName}` : ""}</div></div>
        <span data-testid={`slot-state-${code}`} className={`px-2 py-0.5 text-[10px] font-bold border ${stateTone}`}>{state}</span>
      </div>
      <div>
        <dl className="grid grid-cols-3 gap-2 font-mono text-[11px] text-gray-300">
          <div><dt className={label}>Target</dt><dd data-testid={`slot-target-${code}`} className="text-white">{target == null ? "UNAVAILABLE" : `${target} D`}</dd></div>
          <div><dt className={label}>Used</dt><dd data-testid={`slot-used-${code}`} className="text-white">{used == null ? "UNAVAILABLE" : `${used} D`}</dd></div>
          <div><dt className={label}>Days Left</dt><dd data-testid={`slot-remaining-${code}`} className="text-amber-300">{remaining == null ? "UNAVAILABLE" : `${remaining} D`}</dd></div>
        </dl>
        {pct != null && <div aria-hidden="true" className="mt-1.5 h-2 w-full bg-[#0a0e17] border border-[#1e2a3a]"><div className={`h-full ${pct >= 90 ? "bg-red-500" : pct >= 70 ? "bg-amber-400" : "bg-cyan-500"}`} style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} /></div>}
        <div data-testid={`slot-achievement-${code}`} className="mt-1 font-mono text-[10px] text-gray-400">ACHIEVEMENT · {pct == null ? "UNAVAILABLE" : `${pct}%`}</div>
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-[10px]">
        <dt className="text-gray-500">LOGICAL SLOT</dt><dd data-testid={`slot-enabled-${code}`} className="text-gray-300">{typeof slot?.disabled === "boolean" ? slot.disabled ? "DISABLED" : "ENABLED" : "UNAVAILABLE"}</dd>
        <dt className="text-gray-500">PHYSICAL TOGGLE</dt><dd data-testid={`slot-toggle-input-${code}`} className={toggleIn === "ON" ? "text-emerald-300" : "text-gray-400"}>{toggleIn}</dd>
        <dt className="text-gray-500">CONTACTOR</dt><dd data-testid={`slot-physical-${code}`} className={contactor === "ON" ? "text-emerald-300" : "text-gray-400"}>{contactor}</dd>
      </dl>
      <WingEnergyCard code={code} wing={wing} allocation={allocation} activeGenerationWing={device.feedback_hardware_installed === true && !device.hardware_fault && slot?.feedback_enabled !== false ? activeGenerationWing : undefined} />
      <LastResponse embedded scope={`slot-lastcmd-${code}`} row={lastCmd?.slot === code ? lastCmd : null} last={lastResponse?.slot === code && lastResponse.device_id === device.id ? lastResponse : null} />
      {!readOnly && slot && !slot.disabled && (
        <div className="grid grid-cols-2 gap-2">
          <button data-testid={`cmd-set_active_slot-${device.id}-${code}`} disabled={active || busyAct} onClick={() => queue(device.id, "set_active_slot", code)} className={`${btn} ${tone.cyan}`}>{busyAct ? "EXECUTING…" : "ACTIVATE"}</button>
          <button data-testid={`cmd-off_slot-${device.id}-${code}`} disabled={!active || busyOff} onClick={() => queue(device.id, "off_slot", code)} className={`${btn} ${tone.red}`}>{busyOff ? "EXECUTING…" : "DEACTIVATE"}</button>
        </div>
      )}
      {!readOnly && slot && (
        <div className="flex items-center justify-between gap-2 border-t border-[#1e2a3a] pt-3">
          <span className={label}>Logical slot control</span>
          <button data-testid={`slot-${slot.disabled ? "enable" : "disable"}-${code}`} disabled={busyCfg} onClick={() => setSlotConfig(device.id, code, { disabled: !slot.disabled })} className={`${btn} ${slot.disabled ? tone.cyan : tone.gray}`}>{busyCfg ? "SAVING…" : slot.disabled ? "ENABLE" : "DISABLE"}</button>
        </div>
      )}
      {!readOnly && slot && !slot.disabled && (
        <div className="flex items-center gap-2 border-t border-[#1e2a3a] pt-3">
          <span className={label}>Days</span>
          <input data-testid={`slot-days-input-${code}`} type="number" min={1} max={31} value={days} onChange={(e) => setDays(e.target.value)} className={`${input} w-16 text-center`} />
          <button data-testid={`slot-days-submit-${code}`} disabled={busyDays || !validDays || n === target} onClick={() => queue(device.id, "set_days", code, { days: n })} className={`${btn} ${tone.amber} flex-1`}>{busyDays ? "SENDING…" : "SET DAYS"}</button>
        </div>
      )}
      {!readOnly && allotmentInput}
    </section>
  );
}