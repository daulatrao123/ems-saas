"use client";
import { useEffect, useRef, useState } from "react";
import api from "@/lib/api";
import { btn, input, tone } from "../DashboardHeader";
import { errorText } from "../types";
import { WingCode } from "./types";
import { BillHistory, dailyRate } from "./referenceTypes";

export function BillMonthEditor({ societyId, deviceId, wing, endMonth, readOnly, onSaved }: {
  societyId: string; deviceId: string; wing: WingCode; endMonth: string; readOnly: boolean; onSaved: (history: BillHistory, changedMonths: string[]) => void;
}) {
  const [history, setHistory] = useState<BillHistory | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const alive = useRef(false), submitting = useRef(false);
  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    api.get(`/api/energy/bills?${new URLSearchParams({ society_id: societyId, device_id: deviceId, wing, end_month: endMonth })}`, { signal: controller.signal, timeout: 12000 })
      .then(({ data }: { data: BillHistory }) => {
        if (!controller.signal.aborted && data.device_id === deviceId && data.wing === wing && data.end_month === endMonth && Array.isArray(data.months)) {
          setHistory(data); setValues(Object.fromEntries(data.months.map((m) => [m.month, m.consumption_kwh === null ? "" : String(m.consumption_kwh)])));
        } else if (!controller.signal.aborted) setError("Bill history does not match this controller, wing and month window.");
      }).catch((e) => { if (!controller.signal.aborted) setError(errorText(e).detail); });
    return () => { alive.current = false; controller.abort(); };
  }, [societyId, deviceId, wing, endMonth]);
  const invalid = Object.values(values).some((v) => v.trim() !== "" && (!Number.isFinite(Number(v)) || Number(v) < 0 || Number(v) > 10000000));
  return <form data-testid="bills-month-form" className="space-y-3" onSubmit={async (event) => {
    event.preventDefault();
    if (!history || history.device_id !== deviceId || history.wing !== wing || history.end_month !== endMonth || invalid || readOnly || submitting.current) return;
    submitting.current = true; setBusy(true); setError("");
    try {
      const months = history.months.filter((m) => {
        const entered = values[m.month]?.trim();
        return entered !== undefined && entered !== "" && Number(entered) !== m.consumption_kwh;
      }).map((m) => ({ month: m.month, consumption_kwh: Number(values[m.month]) }));
      if (!months.length) { setError("No changed month to save."); return; }
      const { data } = await api.put("/api/energy/bills", { society_id: societyId, device_id: deviceId, wing, end_month: endMonth, months }, { timeout: 12000 });
      if (alive.current) {
        if (data?.device_id !== deviceId || data.wing !== wing || data.end_month !== endMonth || !Array.isArray(data.months)) { setError("Save response could not be verified for this wing. Reopen history before retrying."); return; }
        if (!months.every((change) => data.months.some((row: BillHistory["months"][number]) => row.month === change.month && typeof row.consumption_kwh === "number" && row.consumption_kwh === change.consumption_kwh))) { setError("Saved month values could not be confirmed. Reopen history before retrying."); return; }
        onSaved(data, months.map((m) => m.month));
      }
    } catch (e) { if (alive.current) setError(`${errorText(e).detail}. Save not confirmed; reopen this wing's history before retrying.`); }
    finally { submitting.current = false; if (alive.current) setBusy(false); }
  }}>
    {error && <div data-testid="bills-error" role="alert" className="text-sm text-red-300">{error}</div>}
    {!history ? <p data-testid="bills-loading" className="text-gray-400">{error ? "History unavailable" : "Loading history…"}</p> : <>
      <table data-testid="bills-month-table" className="w-full table-fixed text-xs text-gray-300">
        <thead><tr className="text-left text-gray-400 border-b border-gray-700"><th className="w-[27%] py-2">Month</th><th className="w-[32%]">Units Consumed (kWh)</th><th className="w-[12%] text-center">Days</th><th className="w-[29%] text-right">kWh/day</th></tr></thead>
        <tbody>{history.months.map((m) => {
          const raw = values[m.month] || "";
          const rate = raw.trim() !== "" && Number.isFinite(Number(raw)) && Number(raw) >= 0 ? Number(raw) / m.days : null;
          return <tr key={m.month} data-testid={`bills-row-${m.month}`} className="border-b border-[#1e2a3a]">
            <th data-testid={`bills-month-${m.month}`} className="py-2 pr-1 font-normal text-left break-words">{m.month_name}</th>
            <td><input data-testid={`bills-input-${m.month}`} aria-label={`${m.month_name} consumption kWh`} type="number" min="0" max="10000000" step="0.001" disabled={readOnly || busy} value={raw} onChange={(e) => setValues((v) => ({ ...v, [m.month]: e.target.value }))} className={`${input} w-full px-1 sm:px-2`} /></td>
            <td data-testid={`bills-days-${m.month}`} className="text-center">{m.days}</td>
            <td data-testid={`bills-daily-${m.month}`} className="text-right pl-1 break-all tabular-nums">{rate === null ? "—" : rate.toFixed(2)}</td>
          </tr>;
        })}</tbody>
      </table>
      <div data-testid="bills-saved-summary" className="text-xs text-gray-400">Multi-month historical average: {dailyRate(history.reference_daily_kwh)} · {history.valid_months} saved months</div>
      {!readOnly && <><div data-testid="bills-upsert-contract" className="text-xs text-gray-400">Blank = omitted; saved months retained. Entered values replace that month only.</div>
        <button data-testid="bills-save" type="submit" disabled={busy || invalid} className={`${btn} ${tone.cyan} w-full`}>{busy ? "SAVING…" : "SAVE CONSUMPTION"}</button></>}
    </>}
  </form>;
}