"use client";
import { useEffect, useRef, useState } from "react";
import api from "@/lib/api";
import { btn, input, tone } from "../DashboardHeader";
import { errorText } from "../types";
import { AllocationReference, GridReference, dailyRate } from "./referenceTypes";
import { fmtKwh } from "./types";

export function GridReferencePanel({ societyId, deviceId, grid, allocation: a, readOnly, auto, onSaved }: {
  societyId: string; deviceId: string; grid: GridReference; allocation: AllocationReference; readOnly: boolean; auto: boolean; onSaved: () => void;
}) {
  const [enabled, setEnabled] = useState(grid.enabled), [limit, setLimit] = useState(grid.limit_kwh_day === null ? "" : String(grid.limit_kwh_day));
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const pending = useRef(false), alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const valid = !enabled || limit.trim() !== "" && Number.isFinite(Number(limit)) && Number(limit) >= 0 && Number(limit) <= 10000000;
  return <section data-testid="grid-reference-panel" className="border-t border-[#1e2a3a] pt-4 space-y-4">
    <h2 data-testid="grid-reference-title" className="text-base font-bold text-white">Allocation / Grid reference</h2>
    <form data-testid="grid-reference-form" className="flex flex-wrap items-end gap-3" onSubmit={async (event) => {
      event.preventDefault(); if (readOnly || !valid || pending.current) return;
      pending.current = true; setBusy(true); setError(""); setNotice("");
      try {
        await api.put("/api/energy/grid-reference", { society_id: societyId, device_id: deviceId, enabled, limit_kwh_day: limit.trim() === "" ? null : Number(limit), expected_version: grid.version });
        if (alive.current) { setNotice("Grid reference saved"); onSaved(); }
      } catch (e) { if (alive.current) setError(errorText(e).detail); }
      finally { pending.current = false; if (alive.current) setBusy(false); }
    }}>
      <label className="text-xs text-gray-400">Feed excess generation to Grid<select data-testid="grid-enabled" aria-label="Feed excess generation to Grid" value={enabled ? "YES" : "NO"} disabled={readOnly || busy} onChange={(e) => setEnabled(e.target.value === "YES")} className={`${input} block mt-1`}><option value="YES">YES</option><option value="NO">NO</option></select></label>
      {enabled && <label className="text-xs text-gray-400">Grid export reference/limit (kWh/day)<input data-testid="grid-limit" aria-label="Grid export reference limit kWh per day" type="number" min="0" max="10000000" step="0.0001" value={limit} disabled={readOnly || busy} required onChange={(e) => setLimit(e.target.value)} className={`${input} block mt-1 w-full`} /></label>}
      {!readOnly && <button data-testid="grid-save" disabled={!valid || busy} className={`${btn} ${tone.cyan}`}>{busy ? "SAVING…" : "SAVE GRID REFERENCE"}</button>}
    </form>
    {error && <div data-testid="grid-error" role="alert" className="text-xs text-red-300">{error}</div>}
    {notice && <div data-testid="grid-saved" role="status" className="text-xs text-emerald-300">{notice}</div>}
    <div data-testid="grid-persisted-setting" className="text-xs text-gray-400">Saved setting: {grid.enabled ? `YES · ${dailyRate(grid.limit_kwh_day)}` : "NO"} · calculation only</div>
    <div data-testid="allocation-reference-flow" className="max-w-2xl space-y-2 text-xs font-mono">
      <div data-testid="allocation-generation" className="flex flex-wrap justify-between gap-2 text-emerald-300"><span>Society generation · {a.generation_source}</span><span>{fmtKwh(a.generation_kwh)}</span></div>
      {auto && <><div aria-hidden="true" className="text-gray-600">↓</div>
        {a.wings.map((w) => <div key={w.wing} data-testid={`quota-step-${w.wing}`} className="border-l-2 border-cyan-800 pl-3 pb-3 space-y-1">
          <div className="flex flex-wrap justify-between gap-2"><span>Required {w.wing}</span><span data-testid={`quota-required-${w.wing}`}>{dailyRate(w.required_kwh)}</span></div>
          <div data-testid={`quota-allocation-${w.wing}`} className="text-gray-400">Assigned reference: {fmtKwh(w.allocated_kwh)} · {w.status}</div>
          <div data-testid={`quota-unmet-${w.wing}`} className="text-gray-400">Unmet: {fmtKwh(w.unmet_kwh)}</div>
        </div>)}
        <div data-testid="quota-completion" className="text-cyan-200">All required quotas satisfied? {a.all_quotas_satisfied ? "YES" : a.all_quotas_known && a.generation_kwh !== null ? "NO" : "UNAVAILABLE"}</div>
        <div data-testid="quota-total-unmet" className="text-amber-300">Total unmet: {fmtKwh(a.unmet_kwh)}</div></>}
      <div aria-hidden="true" className="text-gray-600">↓</div><div data-testid="allocation-excess" className="flex justify-between gap-3"><span>Excess</span><span>{fmtKwh(a.excess_kwh)}</span></div>
      <div aria-hidden="true" className="text-gray-600">↓</div><div data-testid="allocation-grid" className="flex justify-between gap-3 text-cyan-200"><span>Grid</span><span>{fmtKwh(a.grid_allocation_kwh)}</span></div>
      <div data-testid="allocation-grid-status" className="text-gray-400 break-words">{a.status} · {a.reason.replaceAll("_", " ")}</div>
      <div data-testid="allocation-reference-provenance" className="text-gray-500">Reference allocation · not live dispatch</div>
    </div>
  </section>;
}