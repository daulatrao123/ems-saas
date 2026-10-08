"use client";
import { useState, type ReactNode } from "react";
import { CommandRow, Device, Slot, LastResponse as Response } from "./types";
import { QueueFn, SlotConfigFn } from "./useOperations";
import { btn, input, label, panel, tone } from "./DashboardHeader";
import { LastResponse } from "./LastResponse";
import { WingEnergyCard } from "./energy/WingEnergyCard";
import { AllocationMode, DayBasedWingPresentation, allocationVisibility, dayBasedPresentation } from "./allocationMode";
import type { Confirm } from "./ConfirmDialog";
import { AllocationConfig, CalculationMode, ComparisonSeries, WingCode, WingSummary, WINGS } from "./energy/types";

type Props = { device: Device; code: WingCode; slot?: Slot; queue: QueueFn; setSlotConfig: SlotConfigFn; isPending: (d: string, c: string, s?: string) => boolean; readOnly: boolean; ask?: (c: Confirm) => void;
  lastCmd?: CommandRow; lastResponse?: Response | null; wing?: WingSummary; allocation: AllocationConfig | null; activeGenerationWing?: string; allotmentInput?: ReactNode;
  mode?: CalculationMode; allocationMode: AllocationMode; manualControlLabel: string; comparison?: ComparisonSeries; excessEnabled?: boolean;
  operatingDate?: string | null; resetDay?: number | null };
function calendarDaysUsed(row: DayBasedWingPresentation | null): number | null {
  // Includes the cycle day that has already started. Matches pi_firmware scheduled_usage.
  if (!row || row.assignedDays == null || row.cycleDayIndex == null || row.status === "UNAVAILABLE") return null;
  if (row.status === "COMPLETED") return row.assignedDays;
  if (row.status === "CURRENT" && row.completedDays != null) return Math.min(row.assignedDays, row.completedDays + 1);
  if (row.status === "UPCOMING" || row.status === "EXCLUDED") return 0;
  return null;
}
const telemetry = (v: string | undefined, offline: boolean) => offline ? "UNKNOWN (offline)" : v === "ON" || v === "OFF" ? v : "UNKNOWN";
const numberOrNull = (v: number | undefined) => v != null && Number.isFinite(v) ? v : null;

export function SlotCard({ device, code, slot, queue, setSlotConfig, isPending, readOnly, ask, wing, allocation, activeGenerationWing, mode, allocationMode, manualControlLabel, comparison, excessEnabled = false, operatingDate = null, resetDay = null }: Props) {
  const active = device.active_slot === code;
  const state = !slot || typeof slot.disabled !== "boolean" ? "UNAVAILABLE" : slot.disabled ? "DISABLED" : active ? "ACTIVE" : "INACTIVE";
  const stateTone = slot?.disabled ? "text-gray-500 border-gray-700" : active ? "text-emerald-300 border-emerald-500/50 bg-emerald-500/10" : "text-gray-300 border-[#2a3646]";
  // Only the corresponding Pi feedback field; never infer from toggle, command, ACK or active_slot.
  const contactor = device.hardware_fault || device.feedback_hardware_installed !== true || slot?.feedback_enabled === false ? "UNKNOWN" : telemetry(slot?.physical_toggle, !device.connected);
  const busyCfg = isPending(device.id, "slot-config", code);
  const busyAct = isPending(device.id, "set_active_slot", code), busyOff = isPending(device.id, "off_slot", code);
  const displayName = slot?.display_name?.trim();
  const customName = displayName && ![code, `slot ${code}`, `wing ${code}`].some((v) => v.toLowerCase() === displayName.toLowerCase()) ? displayName : null;
  const meterDisabled = wing?.consumption_meter?.enabled === false;
  // Calendar progress uses target_days of enabled wings. Contactor feedback stays on physical_toggle above.
  const dayBased = allocationVisibility(allocationMode).dayAllocation
    ? dayBasedPresentation({
        operatingDate,
        resetDay,
        wings: WINGS.map((wingCode) => ({ wing: wingCode, enabled: device.slots[wingCode]?.disabled === false, assignedDays: device.slots[wingCode]?.target_days })),
      }).wings.find((row) => row.wing === code) ?? null
    : null;
  const energyCard = <WingEnergyCard code={code} wing={wing} mode={mode} allocationMode={allocationMode} comparison={comparison} excessEnabled={excessEnabled} allocation={allocation} activeGenerationWing={meterDisabled || slot?.disabled || device.feedback_hardware_installed !== true || device.hardware_fault || slot?.feedback_enabled === false ? undefined : activeGenerationWing} dayBased={dayBased} />;
  return (
    <section data-testid={`slot-card-${code}`} data-state={state} className={`ops-wing ${panel} ${active ? "border-emerald-500/40" : ""} p-4 flex flex-col gap-4 min-w-0`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0"><h2 data-testid={`slot-heading-${code}`} className="text-base font-bold text-white">Wing {code}</h2><div data-testid={`slot-name-${code}`} className={`${label} break-words`}>Slot {code}{customName ? ` · ${customName}` : ""}</div></div>
        <span data-testid={`slot-state-${code}`} className={`px-2 py-0.5 text-[10px] font-bold border ${stateTone}`}>{state === "ACTIVE" ? "COMMANDED" : state}</span>
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-[10px]">
        <dt className="text-gray-500">LOGICAL SLOT</dt><dd data-testid={`slot-enabled-${code}`} className="text-gray-300">{typeof slot?.disabled === "boolean" ? slot.disabled ? "DISABLED" : "ENABLED" : "UNAVAILABLE"}</dd>
        <dt className="text-gray-500">CONTACTOR</dt><dd data-testid={`slot-physical-${code}`} className={contactor === "ON" ? "text-emerald-300" : "text-gray-400"}>{contactor}</dd>
        <dt className="text-gray-500">FEEDBACK</dt><dd data-testid={`slot-feedback-${code}`} className="text-gray-300">{contactor === "UNKNOWN" ? "NOT VERIFIED" : (active && contactor === "ON") || (!active && contactor === "OFF") ? "VERIFIED" : "NOT VERIFIED"}</dd>
      </dl>
      {slot?.disabled && <div data-testid={`slot-exclusion-${code}`} className="text-xs leading-relaxed text-gray-400 border-t border-dashed border-gray-700 pt-3">Disabled logical wing · excluded from society consumption</div>}
      {slot?.disabled ? <details data-testid={`slot-disabled-data-${code}`} className="ops-disclosure"><summary data-testid={`slot-disabled-data-toggle-${code}`} className="text-xs">Energy data & bill history</summary>{energyCard}</details>
        : energyCard}
      {!readOnly && slot && !slot.disabled && (
        <div data-testid={`manual-control-${code}`} className="grid grid-cols-2 gap-2">
          <span data-testid={`manual-control-label-${code}`} className={`${label} col-span-2`}>{manualControlLabel}</span>
          <button data-testid={`cmd-set_active_slot-${device.id}-${code}`} disabled={active || busyAct} onClick={() => ask?.({ title: `Confirm Wing ${code} activation`, body: `You are requesting activation of Wing ${code} on ${device.name}.`, consequence: "This command will be sent to the EMS controller and may change the physical contactor state.", action: `Activate Wing ${code}`, severity: "DANGER", failure: `Wing ${code} activation was not queued. No command was sent.`, onConfirm: () => queue(device.id, "set_active_slot", code) })} className={`${btn} ${tone.cyan}`}>{busyAct ? "EXECUTING…" : "ACTIVATE"}</button>
          <button data-testid={`cmd-off_slot-${device.id}-${code}`} disabled={!active || busyOff} onClick={() => ask?.({ title: `Deactivate Wing ${code}?`, body: `You are requesting deactivation of Wing ${code} on ${device.name}.`, consequence: "This command will be sent to the EMS controller and may change the physical contactor state.", action: `Deactivate Wing ${code}`, severity: "DANGER", failure: `Wing ${code} deactivation was not queued. No command was sent.`, onConfirm: () => queue(device.id, "off_slot", code) })} className={`${btn} ${tone.red}`}>{busyOff ? "EXECUTING…" : "DEACTIVATE"}</button>
        </div>
      )}
      {!readOnly && slot && (
        <div className="flex items-center justify-between gap-2 border-t border-[#1e2a3a] pt-3">
          <span className={label}>Logical slot control</span>
          <button data-testid={`slot-${slot.disabled ? "enable" : "disable"}-${code}`} disabled={busyCfg} onClick={() => ask?.({ title: slot.disabled ? `Enable Wing ${code}?` : `Disable Wing ${code}?`, body: `${slot.disabled ? "Enable" : "Disable"} the logical slot for Wing ${code} on ${device.name}.`, consequence: slot.disabled ? "The wing becomes eligible for allocation and manual control after the configuration is saved." : "The wing is excluded from allocation and manual control after the configuration is saved. This does not by itself change GPIO polarity.", action: slot.disabled ? `Enable Wing ${code}` : `Disable Wing ${code}`, severity: "WARNING", failure: "Logical slot configuration was not saved.", onConfirm: () => setSlotConfig(device.id, code, { disabled: !slot.disabled }) })} className={`${btn} ${slot.disabled ? tone.cyan : tone.gray}`}>{busyCfg ? "SAVING…" : slot.disabled ? "ENABLE" : "DISABLE"}</button>
        </div>
      )}
    </section>
  );
}

// Existing days editing and command evidence remain available, outside energy cards.
export function SlotOperations({ device, code, slot, queue, isPending, readOnly, ask, lastCmd, lastResponse, allotmentInput, allocationMode = "AUTO", operatingDate = null, resetDay = null }: Pick<Props, "device" | "code" | "slot" | "queue" | "isPending" | "readOnly" | "ask" | "lastCmd" | "lastResponse" | "allotmentInput" | "allocationMode" | "operatingDate" | "resetDay">) {
  const [days, setDays] = useState(String(slot?.target_days ?? ""));
  const [seenTarget, setSeenTarget] = useState(slot?.target_days);
  if (seenTarget !== slot?.target_days) { setSeenTarget(slot?.target_days); setDays(String(slot?.target_days ?? "")); }
  const n = Number(days); const validDays = Number.isInteger(n) && n >= 0 && n <= 31;
  // used_days is the legacy quota counter. With feedback hardware it moves only when that wing's contactor feedback is ON.
  // Day allocation without feedback hardware follows the calendar presentation: a started cycle day counts, with no contactor wait.
  const calendarAuthority = allocationVisibility(allocationMode).dayAllocation && device.feedback_hardware_installed === false;
  const scheduled = calendarAuthority ? dayBasedPresentation({
    operatingDate, resetDay,
    wings: WINGS.map((wingCode) => ({ wing: wingCode, enabled: device.slots[wingCode]?.disabled === false, assignedDays: device.slots[wingCode]?.target_days })),
  }).wings.find((row) => row.wing === code) ?? null : null;
  const calendarUsed = calendarDaysUsed(scheduled);
  const used = calendarAuthority ? calendarUsed : numberOrNull(slot?.used_days), target = numberOrNull(slot?.target_days);
  const remaining = target != null && used != null ? Math.max(0, target - used) : null;
  const busyDays = isPending(device.id, "set_days", code);
  return (
    <div data-testid={`slot-operations-${code}`} className="p-4 space-y-3 min-w-0">
      <h3 data-testid={`slot-operations-heading-${code}`} className={label}>Wing {code} · Days & commands</h3>
      <dl className="grid grid-cols-3 gap-2 font-mono text-[11px] text-gray-300">
        <div><dt className={label}>Target</dt><dd data-testid={`slot-target-${code}`}>{target == null ? "UNAVAILABLE" : `${target} D`}</dd></div>
        <div><dt className={label}>Used</dt><dd data-testid={`slot-used-${code}`}>{used == null ? "UNAVAILABLE" : `${used} D`}</dd></div>
        <div><dt className={label}>Days Left</dt><dd data-testid={`slot-remaining-${code}`}>{remaining == null ? "UNAVAILABLE" : `${remaining} D`}</dd></div>
      </dl>
      {calendarAuthority && calendarUsed != null && <p data-testid={`slot-usage-source-${code}`} className="text-[10px] text-gray-500">No contactor feedback installed. Used days follow the calendar schedule.</p>}
      <LastResponse embedded scope={`slot-lastcmd-${code}`} row={lastCmd?.slot === code ? lastCmd : null} last={lastResponse?.slot === code && lastResponse.device_id === device.id ? lastResponse : null} />
      {!readOnly && slot && !slot.disabled && (
        <div className="flex items-center gap-2 border-t border-[#1e2a3a] pt-3">
          <span className={label}>Days</span>
          <input data-testid={`slot-days-input-${code}`} type="number" min={0} max={31} value={days} onChange={(e) => setDays(e.target.value)} className={`${input} w-16 text-center`} />
          <button data-testid={`slot-days-submit-${code}`} disabled={busyDays || !validDays || n === target} onClick={() => ask?.({ title: `Change Wing ${code} day allocation?`, body: `Wing ${code} on ${device.name}\nCurrent target: ${target == null ? "UNAVAILABLE" : `${target} days`}\nNew target: ${n} days`, consequence: "The new day target is queued for this wing. The controller replaces its previous target only after a successful sync.", action: `Set Wing ${code} Days`, severity: "WARNING", failure: "The day target was not queued. The stored target is unchanged.", onConfirm: () => queue(device.id, "set_days", code, { days: n }) })} className={`${btn} ${tone.amber} flex-1`}>{busyDays ? "SENDING…" : "SET DAYS"}</button>
        </div>
      )}
      {!readOnly && allotmentInput}
    </div>
  );
}