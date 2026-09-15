"use client";
import { ReactNode, useState } from "react";
import { BillHistoryContext } from "./BillHistoryButton";
import { BillHistoryDialog } from "./BillHistoryDialog";
import { GridReferencePanel } from "./GridReferencePanel";
import { EnergySummary, WingCode, WINGS } from "./types";
import { dailyRate, referenceSource } from "./referenceTypes";
import { ConsumptionScopeLabel } from "./ConsumptionScopeLabel";

export function EnergyReferences({ societyId, societyName, controllerName, summary, readOnly, refresh, onSavedMonth, children }: {
  societyId: string; societyName: string; controllerName: string; summary: EnergySummary; readOnly: boolean; refresh: () => void; onSavedMonth: (month: string) => void; children: ReactNode;
}) {
  const [editor, setEditor] = useState<WingCode | null>(null);
  const data = summary.references;
  return <BillHistoryContext.Provider value={{ open: setEditor, readOnly }}>
    {children}
    <details data-testid="historical-consumption-section" className="border-t border-[#1e2a3a] pt-4 space-y-4">
      <summary data-testid="historical-consumption-title" className="text-base font-bold text-white cursor-pointer">Historical averages · separate from selected-month values</summary>
      <ConsumptionScopeLabel data={data} scope="society-reference" />
      <dl className="flex flex-wrap gap-x-10 gap-y-3 text-xs">
        <div><dt className="text-gray-400">Society historical baseline</dt><dd data-testid="society-historical-baseline" className="text-lg text-cyan-200 mt-1">{dailyRate(data.society_historical_daily_kwh)}</dd></div>
        <div><dt className="text-gray-400">Reference daily consumption</dt><dd data-testid="society-consumption-reference" className="text-lg text-white mt-1">{dailyRate(data.society_reference_daily_kwh)}</dd></div>
        <div><dt className="text-gray-400">Source</dt><dd data-testid="society-reference-source" className="text-gray-200 mt-1">{referenceSource(data.society_reference_source)}</dd></div>
      </dl>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {WINGS.map((w) => <div key={w} data-testid={`wing-reference-${w}`} className="border-l-2 border-cyan-900 pl-3 space-y-1 text-xs">
          <h3 data-testid={`wing-reference-title-${w}`} className="text-gray-200 font-bold">Wing {w}</h3>
          <div data-testid={`wing-historical-rate-${w}`} className="text-cyan-200">{dailyRate(data.wings[w].history.reference_daily_kwh)} · Historical</div>
          <div data-testid={`wing-valid-months-${w}`} className="text-gray-400">Based on {data.wings[w].history.valid_months} valid months</div>
          <div data-testid={`wing-effective-reference-${w}`} className="text-gray-300">Selected reference: {dailyRate(data.wings[w].effective.daily_kwh)}</div>
          <div data-testid={`wing-reference-source-${w}`} className="text-gray-500">{referenceSource(data.wings[w].effective.source)}{data.wings[w].effective.operating_date ? ` · ${data.wings[w].effective.operating_date}` : ""}</div>
        </div>)}
      </div>
    </details>
    <div data-testid="allocation-reference-operating-date" className="text-xs text-gray-400">Allocation / Grid reference date: {summary.as_of_operating_date} · Pi operating day</div>
    <GridReferencePanel key={`${summary.device_id}:${data.grid.version}`} societyId={societyId} deviceId={summary.device_id} grid={data.grid} allocation={data.allocation} readOnly={readOnly} onSaved={refresh} />
    {editor && <BillHistoryDialog societyId={societyId} societyName={societyName} controllerName={controllerName} operatingDate={summary.as_of_operating_date} deviceId={summary.device_id} initialWing={editor} data={data} readOnly={readOnly} onClose={() => setEditor(null)} onSavedMonth={onSavedMonth} />}
  </BillHistoryContext.Provider>;
}