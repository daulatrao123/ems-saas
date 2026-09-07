"use client";
import { useState } from "react";
import { QueueFn } from "./useOperations";
import { btn, input, label, panel, tone } from "./DashboardHeader";
import { Confirm } from "./ConfirmDialog";

type Ctl = { deviceId: string; queue: QueueFn; isPending: (d: string, c: string, s?: string) => boolean; ask: (c: Confirm) => void };

export function SystemControls({ deviceId, queue, isPending, ask }: Ctl) {
  const items: { cmd: string; label: string; t: string; danger: boolean; body: string }[] = [
    { cmd: "reset_days", label: "RESET DAYS", t: tone.amber, danger: false, body: "Reset the used-days counters of every slot on this Pi to 0.\nResult token: STATE_RESET (software state)." },
    { cmd: "off_all", label: "OFF ALL", t: tone.red, danger: true, body: "Turn OFF all slots?\n\nThis will affect all active slots. Completion requires positive hardware verification from the Pi." },
    { cmd: "restart", label: "RESTART", t: tone.gray, danger: true, body: "Restart the EMS controller service on the Pi.\nResult token: RESTART_SCHEDULED — the Pi restarts after acknowledging." },
    { cmd: "reboot", label: "REBOOT PI", t: tone.red, danger: true, body: "Reboot the Raspberry Pi operating system.\n\nThe device will be unreachable for about a minute. Result token: REBOOT_SCHEDULED." },
  ];
  return (
    <section data-testid={`system-controls-${deviceId}`} className={`${panel} p-4`}>
      <div className={label}>System Controls</div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        {items.map((i) => {
          const busy = isPending(deviceId, i.cmd);
          return (
            <button key={i.cmd} data-testid={`cmd-${i.cmd}-${deviceId}`} disabled={busy} className={`${btn} ${i.t} py-3`}
              onClick={() => ask({ title: i.label, body: i.body, action: i.label, danger: i.danger, onConfirm: () => queue(deviceId, i.cmd, "") })}>
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
      <div className="flex items-center justify-between"><div className={label}>Monthly Reset Day</div><div className="font-mono text-[11px] text-gray-400">current <b data-testid={`reset-day-current-${deviceId}`} className="text-white">{current}</b></div></div>
      <div className="mt-3 flex items-center gap-2">
        <span className="text-[11px] text-gray-400">Day</span>
        <input data-testid={`reset-day-input-${deviceId}`} type="number" min={1} max={28} value={day} onChange={(e) => setDay(e.target.value)} className={`${input} w-20 text-center`} />
        <button data-testid={`cmd-set_reset_day-${deviceId}`} disabled={busy || !valid || n === current} className={`${btn} ${tone.amber} flex-1`}
          onClick={() => ask({ title: `Set reset day to ${n}`, body: "The Pi validates the value; the cloud commits it on CONFIG_ACCEPTED (software configuration, not physical state).", action: "SET", onConfirm: () => queue(deviceId, "set_reset_day", "", { day: n }) })}>
          {busy ? "SENDING…" : "SET"}
        </button>
      </div>
      {!valid && <div className="mt-1 text-[10px] text-red-400">Allowed range 1–28</div>}
    </section>
  );
}
