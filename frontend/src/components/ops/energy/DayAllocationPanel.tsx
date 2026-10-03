"use client";
import { useState } from "react";
import api from "@/lib/api";
import { btn, input, panel, tone } from "../DashboardHeader";
import { EnergySummary } from "./types";
import type { Confirm } from "../ConfirmDialog";

export function DayAllocationPanel({ societyId, deviceId, deviceName, summary, readOnly, refresh, wings, ask }: {
  societyId: string; deviceId: string; deviceName?: string; summary: EnergySummary | null; readOnly: boolean; refresh: () => Promise<void>; wings: readonly string[]; ask?: (c: Confirm) => void;
}) {
  const cycle = summary?.day_allocation?.cycle_days ?? 0;
  const [kind, setKind] = useState<"DAYS" | "UNITS">("DAYS");
  const [values, setValues] = useState<Record<string, string>>({ A: "", B: "", C: "", D: "" });
  const [days, setDays] = useState<Record<string, number> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const application = summary?.day_allocation?.application;
  const body = { society_id: societyId, device_id: deviceId, type: kind, wings: Object.fromEntries(wings.map((wing) => [wing, Number(values[wing] || 0)])) };
  const calculate = async () => {
    setBusy(true); setError("");
    try {
      const result = await api.post("/api/energy/day-allocation/calculate", body);
      setDays(result.data.days);
    } catch (e) { setDays(null); setError((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || "Calculation failed"); }
    setBusy(false);
  };
  const apply = async () => {
    setBusy(true); setError("");
    try {
      const result = await api.post("/api/energy/day-allocation/apply", { ...body, idempotency_key: crypto.randomUUID() });
      if (result.data.status !== "APPLYING") { setError("Allocation was not queued"); return false; }
      await refresh();
      return true;
    } catch (e) { setError((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || "Apply failed"); return false; }
    finally { setBusy(false); }
  };
  return <section data-testid="day-allocation-panel" className={panel}>
    <div className="text-xs text-slate-500">CYCLE LENGTH</div>
    <div data-testid="day-cycle-length" className="text-2xl font-bold">{cycle || "—"}</div>
    <div className="flex gap-2 mt-3">
      {(["DAYS", "UNITS"] as const).map((item) => <button key={item} type="button" disabled={readOnly || busy} onClick={() => { setKind(item); setDays(null); }} className={`${btn} ${kind === item ? tone.cyan : tone.gray}`}>{item}</button>)}
    </div>
    <div className="grid grid-cols-4 gap-2 mt-3">
      {wings.map((wing) => <label key={wing} className="text-xs text-slate-500">{wing}
        <input data-testid={`day-input-${wing}`} disabled={readOnly || busy} value={values[wing] || ""} onChange={(e) => { setValues((current) => ({ ...current, [wing]: e.target.value })); setDays(null); }} className={`${input} mt-1`} />
      </label>)}
    </div>
    {days && <div data-testid="day-calculated" className="mt-3 text-sm">{wings.map((wing) => `${wing} ${days[wing] ?? "—"}`).join(" · ")}</div>}
    {application && <div data-testid="day-application-status" className="mt-3 text-sm">
      <div>{application.status}{application.commands.map((command) => ` ${command.slot}:${command.status}`)}</div>
      <p data-testid="day-sync-note" className="mt-1 text-slate-500">A completed or acknowledged command stores the target in the cloud. The controller replaces its previous target only after its next successful sync.</p>
    </div>}
    {error && <div role="alert" className="mt-3 text-sm text-rose-700">{error}</div>}
    {!readOnly && <div className="flex gap-2 mt-3">
      <button type="button" data-testid="day-calculate" disabled={busy} onClick={() => void calculate()} className={`${btn} ${tone.gray}`}>CALCULATE</button>
      <button type="button" data-testid="day-apply" disabled={busy || !days} onClick={() => {
        if (!days) return;
        const listed = ["A", "B", "C", "D"].map((wing) => wings.includes(wing) ? `${wing}: ${days[wing] ?? "—"} days` : `${wing}: Disabled`).join("\n");
        ask?.({
          title: "Apply DAY_BASED schedule?",
          body: `${deviceName || deviceId}\n${listed}\nCycle: ${cycle || "—"} days\nReset day: ${summary?.day_allocation?.reset_day ?? "—"}`,
          consequence: "The new schedule will become the canonical device configuration after successful synchronization.",
          action: "Apply Schedule",
          severity: "WARNING",
          failure: "The schedule was not queued. The stored day allocation is unchanged.",
          onConfirm: () => apply(),
        });
      }} className={`${btn} ${tone.cyan}`}>SEND ALLOCATION</button>
    </div>}
  </section>;
}
