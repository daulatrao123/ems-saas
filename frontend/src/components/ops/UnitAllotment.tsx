"use client";
import { useState } from "react";
import { Device, SLOT_CODES } from "./types";
import { QueueFn } from "./useOperations";
import { unitsToDays } from "./allocation";
import { btn, input, label, panel, tone } from "./DashboardHeader";
import { Confirm } from "./ConfirmDialog";

type Mode = "units" | "direct";

// Units mode: proportional largest-remainder allocation (existing algorithm). Direct mode: days typed per slot.
// Send All Days = one independent set_days command per slot, each with its own idempotency key (queue()).
export function UnitAllotment({ device, queue, isPending, ask }: { device: Device; queue: QueueFn; isPending: (d: string, c: string, s?: string) => boolean; ask: (c: Confirm) => void }) {
  const slots = SLOT_CODES.filter((c) => device.slots[c] && !device.slots[c].disabled);
  const [mode, setMode] = useState<Mode>("units"); const [cycle, setCycle] = useState("30");
  const [vals, setVals] = useState<Record<string, string>>({}); const [result, setResult] = useState<Record<string, number>>({});
  const [report, setReport] = useState(""); const [sending, setSending] = useState(false);
  const calculate = () => {
    const nums: Record<string, number> = {}; slots.forEach((s) => { nums[s] = parseFloat(vals[s] || "0") || 0; });
    setResult(mode === "units" ? unitsToDays(nums, parseInt(cycle) || 30) : Object.fromEntries(slots.map((s) => [s, Math.round(nums[s])])));
    setReport("");
  };
  const sendable = Object.entries(result).filter(([, d]) => Number.isInteger(d) && d >= 1 && d <= 31);
  const sendAll = async () => {
    setSending(true); let ok = 0;
    for (const [slot, d] of sendable) if (await queue(device.id, "set_days", slot, { days: d })) ok += 1;
    setReport(`${ok}/${sendable.length} set_days commands queued`); setSending(false);
  };
  return (
    <section data-testid={`unit-allotment-${device.id}`} className={`${panel} p-4`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className={label}>Unit Allotment · Unit → Days</div>
        <div className="flex items-center gap-2">
          <select data-testid="allot-mode" value={mode} onChange={(e) => { setMode(e.target.value as Mode); setResult({}); }} className={input}>
            <option value="units">Units mode (proportional)</option>
            <option value="direct">Direct days</option>
          </select>
          {mode === "units" && <><span className="text-[11px] text-gray-400">Cycle</span><input data-testid="allot-cycle" type="number" min={1} max={31} value={cycle} onChange={(e) => setCycle(e.target.value)} className={`${input} w-16 text-center`} /></>}
        </div>
      </div>
      <div className="mt-3 grid grid-cols-2 lg:grid-cols-4 gap-2">
        {slots.map((s) => (
          <label key={s} className="text-[10px] text-gray-400">
            <span className="block mb-1 font-mono">SLOT {s} <span className="text-gray-600">{mode === "units" ? "units" : "days"}</span></span>
            <input data-testid={`allot-input-${s}`} type="number" min={0} step={mode === "units" ? "0.1" : "1"} value={vals[s] || ""} onChange={(e) => setVals({ ...vals, [s]: e.target.value })} className={`${input} w-full`} placeholder="0" />
            {result[s] !== undefined && <span data-testid={`allot-result-${s}`} className="block mt-1 font-mono text-amber-300">→ {result[s]} days</span>}
          </label>
        ))}
        {slots.length === 0 && <span className="col-span-4 text-[11px] text-gray-500">No enabled slots.</span>}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button data-testid="allot-calculate" onClick={calculate} disabled={slots.length === 0} className={`${btn} ${tone.gray}`}>CALCULATE</button>
        <button data-testid="allot-send-all" disabled={sending || sendable.length === 0 || isPending(device.id, "set_days", sendable[0]?.[0] || "")} className={`${btn} ${tone.amber}`}
          onClick={() => ask({ title: `Send ${sendable.length} set_days commands`, body: sendable.map(([s, d]) => `Slot ${s} → ${d} days`).join("\n") + "\n\nOne independent command per slot; the Pi validates each (CONFIG_ACCEPTED).", action: "SEND ALL DAYS", onConfirm: sendAll })}>
          {sending ? "SENDING…" : `SEND ALL DAYS (${sendable.length})`}
        </button>
        {report && <span data-testid="allot-report" className="font-mono text-[11px] text-emerald-400">{report}</span>}
      </div>
    </section>
  );
}
