"use client";
import { ReactNode } from "react";
import { btn, input, label, panel, tone } from "../DashboardHeader";
import { AllocationTimeline } from "./AllocationTimeline";
import { GenerationCard } from "./GenerationCard";
import { CalculationMode, fmtKwh } from "./types";
import { useEnergy } from "./useEnergy";
import { EnergyReferences } from "./EnergyReferences";

export function EnergyPanel({ energy: en, readOnly, activeGenerationWing, children, societyId }: {
  energy: ReturnType<typeof useEnergy>; readOnly: boolean; activeGenerationWing?: string; children: ReactNode; societyId: string;
}) {
  const mode = en.summary?.calculation?.mode;
  const entries = en.entries.filter((r) => r.kind === "MANUAL_GENERATION" && r.source === "MANUAL");
  const content = (
    <div data-testid="energy-panel" className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <label className="block text-xs text-gray-400">Energy Calculation Mode
          <select data-testid="energy-calculation-mode" aria-label="Energy Calculation Mode" value={mode || ""} disabled={readOnly || en.loading || en.saving || !mode}
            onChange={(e) => en.setMode(e.target.value as CalculationMode)} className={`${input} block mt-1 min-w-44`}>
            {!mode && <option value="">{en.loading ? "LOADING…" : "UNAVAILABLE"}</option>}
            <option value="AUTO">AUTO MODE</option><option value="MANUAL">MANUAL MODE</option>
          </select>
        </label>
        <div data-testid="energy-as-of" className={label}>Energy · {en.summary ? `as of ${en.summary.as_of_operating_date}` : "UNAVAILABLE"}</div>
        <button data-testid="energy-refresh" disabled={en.saving || en.loading} onClick={() => en.refresh()} className={`${btn} ${tone.gray}`}>{en.loading ? "LOADING…" : "REFRESH"}</button>
      </div>
      {en.saving && <div data-testid="energy-save-pending" role="status" className="text-xs text-amber-300">Saving…</div>}
      {(en.error || en.saveError) && <div data-testid="energy-error" role="alert" className="border border-red-500/40 bg-red-500/10 px-4 py-2 font-mono text-xs text-red-300">{en.saveError || en.error}</div>}
      {en.notice && !en.saveError && <div data-testid="energy-save-confirmation" role="status" className="text-xs text-emerald-300">{en.notice}</div>}
      {!en.summary ? <div data-testid="energy-unavailable" className={`${panel} p-6 text-center font-mono text-sm text-gray-500`}>{en.loading ? "LOADING ENERGY…" : "ENERGY DATA UNAVAILABLE"}</div> : <>
        {en.summary.generation_meter?.meter_id === "M1" ? <GenerationCard meter={en.summary.generation_meter} monthly={en.monthly} operatingDate={en.summary.as_of_operating_date} resetPeriod={en.summary.reset_period} activeGenerationWing={activeGenerationWing} /> : <div data-testid="energy-common-unavailable" className="p-4 text-gray-500">COMMON GENERATION UNAVAILABLE</div>}
        {mode === "MANUAL" && <div data-testid="manual-common-provenance" className="text-[11px] text-gray-400">Society Generation remains PHYSICAL M1 telemetry · common manual generation unavailable</div>}
      </>}
      {children}
      {mode === "AUTO" && en.summary && <AllocationTimeline events={en.events} operatingDate={en.summary.as_of_operating_date} />}
      {mode === "MANUAL" && entries.length > 0 && <section data-testid="manual-activity-history" className="border-t border-[#1e2a3a] pt-3">
        <h3 data-testid="manual-activity-title" className={label}>Manual generation activity · recent entries</h3>
        <div data-testid="manual-activity-provenance" className="text-xs text-gray-400">Generation adjustments by operating day · not consumption or bills</div>
        <ol className="mt-2 space-y-2">
          {entries.map((entry) => <li key={entry.id} data-testid={`manual-activity-${entry.id}`} className="flex flex-wrap gap-x-4 gap-y-1 border-b border-[#1e2a3a] pb-2 text-[11px] text-gray-300">
            <span>{entry.operating_date} · Wing {entry.wing}</span><span data-testid={`manual-activity-value-${entry.id}`} className="font-mono text-amber-300">{fmtKwh(entry.value_kwh)} · MANUAL</span><span className="break-words min-w-0">{entry.reason}</span>
          </li>)}
        </ol>
      </section>}
    </div>
  );
  return en.summary?.references ? <EnergyReferences key={en.summary.device_id} societyId={societyId} summary={en.summary} readOnly={readOnly} refresh={en.refresh}>{content}</EnergyReferences> : content;
}