"use client";
import { CalendarComparison } from "./types";
import { monthLabel } from "./comparisonLabels";
import { EnergyComparisonChart } from "./EnergyComparisonChart";
import { ConsumptionScopeLabel } from "./ConsumptionScopeLabel";

export function CalendarSocietyComparison({ data, excessEnabled }: { data: CalendarComparison; excessEnabled: boolean }) {
  return <section data-testid="society-calendar-comparison" className="border-t border-[#1e2a3a] pt-4 space-y-3">
    <h2 data-testid="society-calendar-title" className="text-base font-bold text-white">Society · {monthLabel(data.period.month)}</h2>
    <ConsumptionScopeLabel data={data} scope="society-calendar" />
    <div data-testid="society-calendar-basis" className="text-xs text-gray-400">M1 physical generation · {data.mode === "MANUAL" ? `Consumption: matching monthly bills ÷ ${data.period.calendar_days} calendar days` : "Consumption: physical meters"} · kWh</div>
    <EnergyComparisonChart scope="society" rows={data.society.rows} mode={data.mode} excessEnabled={excessEnabled} />
  </section>;
}