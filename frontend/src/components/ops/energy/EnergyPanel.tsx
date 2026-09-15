"use client";
import { ReactNode, useState } from "react";
import { btn, input, label, panel, tone } from "../DashboardHeader";
import { AllocationTimeline } from "./AllocationTimeline";
import { GenerationCard } from "./GenerationCard";
import { CalculationMode } from "./types";
import { useEnergy } from "./useEnergy";
import { EnergyReferences } from "./EnergyReferences";
import { SocietyEnergyComparison } from "./SocietyEnergyComparison";
import { CalendarComparisonContext } from "./CalendarComparisonContext";
import { useCalendarComparison } from "./useCalendarComparison";
import { ComparisonPeriod } from "./ComparisonPeriod";
import { CalendarSocietyComparison } from "./CalendarSocietyComparison";

export function EnergyPanel({ energy: en, readOnly, activeGenerationWing, children, societyId, societyName, controllerName }: {
  energy: ReturnType<typeof useEnergy>; readOnly: boolean; activeGenerationWing?: string; children: ReactNode; societyId: string; societyName?: string; controllerName?: string;
}) {
  const mode = en.summary?.calculation?.mode;
  const key = `${societyId}:${en.summary?.device_id}:${mode}`;
  const [selection, setSelection] = useState({ key: "", month: "", revision: 0 });
  const month = selection.key === key ? selection.month : "";
  const calendar = useCalendarComparison(societyId, en.summary, month, selection.revision);
  const selectMonth = (value: string) => setSelection((prev) => ({ key, month: value, revision: prev.revision + 1 }));
  const savedMonth = (value: string) => { selectMonth(value); void en.refresh(); };
  const societyLabel = societyName || `Society ${societyId}`, controllerLabel = controllerName || en.summary?.device_id || "UNAVAILABLE";
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
        <button data-testid="energy-refresh" disabled={en.saving || en.loading} onClick={() => { selectMonth(month); void en.refresh(); }} className={`${btn} ${tone.gray}`}>{en.loading ? "LOADING…" : "REFRESH"}</button>
      </div>
      {en.saving && <div data-testid="energy-save-pending" role="status" className="text-xs text-amber-300">Saving…</div>}
      {(en.error || en.saveError) && <div data-testid="energy-error" role="alert" className="border border-red-500/40 bg-red-500/10 px-4 py-2 font-mono text-xs text-red-300">{en.saveError || en.error}</div>}
      {en.notice && !en.saveError && <div data-testid="energy-save-confirmation" role="status" className="text-xs text-emerald-300">{en.notice}</div>}
      {!en.summary ? <div data-testid="energy-unavailable" className={`${panel} p-6 text-center font-mono text-sm text-gray-500`}>{en.loading ? "LOADING ENERGY…" : "ENERGY DATA UNAVAILABLE"}</div> : <>
        {en.summary.generation_meter?.meter_id === "M1" ? <GenerationCard meter={en.summary.generation_meter} monthly={en.monthly} operatingDate={en.summary.as_of_operating_date} resetPeriod={en.summary.reset_period} activeGenerationWing={activeGenerationWing} /> : <div data-testid="energy-common-unavailable" className="p-4 text-gray-500">COMMON GENERATION UNAVAILABLE</div>}
      </>}
      <ComparisonPeriod society={societyLabel} controller={controllerLabel} operatingDate={en.summary?.as_of_operating_date} month={month} data={calendar.data} onChange={selectMonth} />
      {calendar.error && <div data-testid="calendar-comparison-error" role="alert" className="text-xs text-red-300">{calendar.error}</div>}
      {calendar.loading && <div data-testid="calendar-comparison-loading" role="status" className="text-xs text-gray-400">Loading {month} daily values…</div>}
      {children}
      {month ? calendar.data && <CalendarSocietyComparison data={calendar.data} excessEnabled={en.summary?.references?.grid.enabled === true} /> : <SocietyEnergyComparison key={en.summary?.device_id} data={en.comparison} excessEnabled={en.summary?.references?.grid.enabled === true} />}
      {en.summary && <AllocationTimeline events={en.events} operatingDate={en.summary.as_of_operating_date} />}
    </div>
  );
  return <CalendarComparisonContext.Provider value={{ month, ...calendar, operatingDate: en.summary?.as_of_operating_date }}>
    {en.summary?.references ? <EnergyReferences key={`${en.summary.device_id}:${mode}`} societyId={societyId} societyName={societyLabel} controllerName={controllerLabel} summary={en.summary} readOnly={readOnly} refresh={en.refresh} onSavedMonth={savedMonth}>{content}</EnergyReferences> : content}
  </CalendarComparisonContext.Provider>;
}