"use client";
import { useState } from "react";
import { QueueFn } from "./useOperations";
import { btn, input, label, panel, tone } from "./DashboardHeader";
import { Confirm, Severity } from "./ConfirmDialog";

type Ctl = { deviceId: string; queue: QueueFn; isPending: (d: string, c: string, s?: string) => boolean; ask: (c: Confirm) => void };
type Item = { cmd: string; label: string; t: string; severity: Severity; title: string; body: string; consequence: string; action: string };

export function SystemControls({ deviceId, queue, isPending, ask }: Ctl) {
  const items: Item[] = [
    { cmd: "reset_days", label: "RESET DAYS", t: tone.amber, severity: "WARNING", title: "Reset used-day counters?", action: "Reset Days", body: "You are about to reset the used-day counters of every slot on this controller to 0.", consequence: "This changes the software day counters used by allocation. It does not switch a contactor." },
    { cmd: "off_all", label: "OFF ALL", t: tone.red, severity: "CRITICAL", title: "Confirm emergency shutdown", action: "Turn Everything OFF", body: "This will request all controllable wings to be switched OFF.", consequence: "Active electrical allocation will be interrupted. The command is sent to the EMS controller and may change physical contactor state." },
    { cmd: "restart", label: "RESTART", t: tone.gray, severity: "DANGER", title: "Restart EMS controller?", action: "Restart Controller", body: "You are requesting a restart of the EMS controller service on this device.", consequence: "Runtime control will temporarily stop while the controller restarts." },
    { cmd: "reboot", label: "REBOOT PI", t: tone.red, severity: "CRITICAL", title: "Reboot Raspberry Pi?", action: "Reboot Device", body: "You are requesting a reboot of this Raspberry Pi.", consequence: "The Raspberry Pi and EMS controller will temporarily go offline." },
  ];
  return (
    <section data-testid={`system-controls-${deviceId}`} className={`${panel} p-4`}>
      <div data-testid={`system-controls-heading-${deviceId}`} className={label}>System Controls</div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        {items.map((i) => {
          const busy = isPending(deviceId, i.cmd);
          return (
            <button key={i.cmd} data-testid={`cmd-${i.cmd}-${deviceId}`} disabled={busy} className={`${btn} ${i.t} py-3`}
              onClick={() => ask({ title: i.title, body: i.body, consequence: i.consequence, action: i.action, severity: i.severity, failure: `${i.action} was not accepted. No command was confirmed.`, onConfirm: () => queue(deviceId, i.cmd, "") })}>
              {busy ? "EXECUTING…" : i.label}
            </button>
          );
        })}
      </div>
    </section>
  );
}

export function ResetDayControl({ deviceId, current, queue, isPending, ask }: Ctl & { current: number }) {
  const [day, setDay] = useState(String(current)); const [seen, setSeen] = useState(current);
  if (current !== seen) { setSeen(current); setDay(String(current)); }   // re-sync when the backend value changes
  const n = Number(day); const valid = Number.isInteger(n) && n >= 1 && n <= 28; const busy = isPending(deviceId, "set_reset_day");
  return (
    <section data-testid={`reset-day-control-${deviceId}`} className={`${panel} p-4`}>
      <div className="flex flex-wrap items-center justify-between gap-2"><div data-testid={`reset-day-heading-${deviceId}`} className={label}>Monthly Reset Day</div><div className="font-mono text-[11px] text-gray-400">current <b data-testid={`reset-day-current-${deviceId}`} className="text-white">{current}</b></div></div>
      <div className="mt-3 flex items-center gap-2">
        <span className="text-[11px] text-gray-400">Day</span>
        <input data-testid={`reset-day-input-${deviceId}`} type="number" min={1} max={28} value={day} onChange={(e) => setDay(e.target.value)} className={`${input} w-20 text-center`} />
        <button data-testid={`cmd-set_reset_day-${deviceId}`} disabled={busy || !valid || n === current} className={`${btn} ${tone.amber} flex-1`}
          onClick={() => ask({ title: "Change reset day?", body: `Current reset day: ${current}\nNew reset day: ${n}`, consequence: "The new reset day becomes the stored controller configuration after the device accepts it. This does not switch a contactor.", action: "Apply Reset Day", severity: "WARNING", failure: "Reset day was not accepted. The stored day is unchanged.", onConfirm: () => queue(deviceId, "set_reset_day", "", { day: n }) })}>
          {busy ? "SENDING…" : "SET"}
        </button>
      </div>
      {!valid && <div data-testid={`reset-day-range-error-${deviceId}`} role="alert" className="mt-1 text-xs text-red-400">Allowed range 1–28</div>}
    </section>
  );
}
