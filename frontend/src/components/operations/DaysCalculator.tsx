"use client";
import { useState } from "react";
import { QueueFn, ctlBtn, ctlInput, tone } from "./Controls";

// Proportional unit->days allocation with largest-remainder rounding so the slots sum to the cycle length.
export function unitsToDays(units: Record<string, number>, cycleDays: number): Record<string, number> {
  const slots = Object.keys(units); const total = slots.reduce((a, s) => a + (units[s] || 0), 0);
  if (total <= 0 || cycleDays <= 0) return {};
  const exact: Record<string, number> = {}; const out: Record<string, number> = {}; let sum = 0;
  for (const s of slots) { exact[s] = ((units[s] || 0) / total) * cycleDays; out[s] = Math.round(exact[s]); sum += out[s]; }
  let diff = cycleDays - sum;
  const order = slots.slice().sort((a, b) => (diff > 0 ? (exact[b] % 1) - (exact[a] % 1) : (exact[a] % 1) - (exact[b] % 1)));
  for (let i = 0; diff !== 0 && i < order.length; i++) { out[order[i]] += diff > 0 ? 1 : -1; diff += diff > 0 ? -1 : 1; }
  for (const s of slots) if ((units[s] || 0) > 0 && out[s] < 1) out[s] = 1;
  return out;
}

type SlotMeta = { code: string; name: string; disabled: boolean };

export function DaysCalculator({ deviceId, slots, queue }: { deviceId: string; slots: SlotMeta[]; queue: QueueFn }) {
  const [cycle, setCycle] = useState("30"); const [units, setUnits] = useState<Record<string, string>>({});
  const [result, setResult] = useState<Record<string, number>>({}); const [sending, setSending] = useState(false); const [report, setReport] = useState("");
  const enabled = slots.filter((s) => !s.disabled);
  const compute = () => {
    const u: Record<string, number> = {}; for (const s of enabled) u[s.code] = parseFloat(units[s.code] || "0") || 0;
    setResult(unitsToDays(u, parseInt(cycle) || 30)); setReport("");
  };
  const sendable = Object.entries(result).filter(([, d]) => d >= 1 && d <= 31);
  // N independent set_days commands, one per slot, each with its own idempotency key (queue() generates it).
  const sendAll = async () => {
    if (!window.confirm(`Send ${sendable.length} set_days commands (one per slot)?`)) return;
    setSending(true); let ok = 0;
    for (const [slot, days] of sendable) { if (await queue(deviceId, "set_days", slot, { days })) ok += 1; }
    setReport(`${ok}/${sendable.length} set_days commands queued`); setSending(false);
  };
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-800/30 p-3" data-testid={`days-calculator-${deviceId}`}>
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <span className="text-[10px] uppercase tracking-wide text-gray-500">Unit → days calculator</span>
        <span className="text-[10px] text-gray-500">Cycle</span>
        <input data-testid={`calc-cycle-${deviceId}`} type="number" min={1} max={31} value={cycle} onChange={(e) => setCycle(e.target.value)} className={`${ctlInput} w-16`} />
        <span className="text-[10px] text-gray-500">days</span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        {enabled.map((s) => (
          <label key={s.code} className="text-[10px] text-gray-400">
            <span className="block mb-0.5">{s.name} <span className="font-mono text-gray-500">{s.code}</span> units</span>
            <input data-testid={`calc-units-${s.code}`} type="number" min={0} step="0.1" value={units[s.code] || ""} onChange={(e) => setUnits({ ...units, [s.code]: e.target.value })} className={`${ctlInput} w-full`} placeholder="0" />
            {result[s.code] !== undefined && <span data-testid={`calc-result-${s.code}`} className="block mt-0.5 font-mono text-amber-300">→ {result[s.code]} d</span>}
          </label>
        ))}
        {enabled.length === 0 && <span className="text-[10px] text-gray-500 col-span-4">No enabled slots to allocate.</span>}
      </div>
      <div className="mt-2 flex items-center gap-2">
        <button data-testid={`calc-compute-${deviceId}`} onClick={compute} disabled={enabled.length === 0} className={`${ctlBtn} ${tone.gray}`}>CALCULATE</button>
        <button data-testid={`calc-send-all-${deviceId}`} onClick={sendAll} disabled={sending || sendable.length === 0} className={`${ctlBtn} ${tone.amber}`}>{sending ? "SENDING…" : `SEND ALL DAYS (${sendable.length})`}</button>
        {report && <span data-testid={`calc-report-${deviceId}`} className="text-[10px] text-emerald-400">{report}</span>}
      </div>
    </div>
  );
}
