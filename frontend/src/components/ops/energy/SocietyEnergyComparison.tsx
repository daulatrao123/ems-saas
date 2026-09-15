"use client";
import { useState } from "react";
import { EnergyComparison, fmtKwh } from "./types";
import { EnergyComparisonChart, signedKwh } from "./EnergyComparisonChart";
import { btn, input, tone } from "../DashboardHeader";

export function SocietyEnergyComparison({ data, excessEnabled }: { data: EnergyComparison | null; excessEnabled: boolean }) {
  const [days, setDays] = useState(30);
  if (!data) return <section data-testid="society-comparison-unavailable" className="text-xs text-gray-500 py-4">SOCIETY COMPARISON UNAVAILABLE</section>;
  const point = data.society.today, delta = point.generation_minus_consumption_kwh;
  return <section data-testid="society-energy-comparison" className="border-t border-[#1e2a3a] pt-4 space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-3"><h2 data-testid="society-comparison-title" className="text-base font-bold text-white">Society · Generation & Consumption</h2>
      <select data-testid="society-comparison-days" aria-label="Comparison period" value={days} onChange={(e) => setDays(Number(e.target.value))} className={`${input} ${btn} ${tone.gray}`}><option value={7}>7 days</option><option value={30}>30 days</option></select>
    </div>
    <dl className="flex flex-wrap gap-x-10 gap-y-3 text-xs">
      <div><dt className="text-gray-500">M1 generation · today</dt><dd data-testid="society-comparison-generation" className="mt-1 text-emerald-300">{fmtKwh(point.generated_kwh)}</dd></div>
      <div><dt className="text-gray-500">A–D consumption · {data.mode === "MANUAL" ? "bill-derived daily reference" : "physical today"}</dt><dd data-testid="society-comparison-consumption" className="mt-1 text-amber-300">{fmtKwh(point.consumed_kwh)}</dd></div>
      {(delta === null || delta <= 0 || excessEnabled) && <div><dt className="text-gray-500">Generation − consumption</dt><dd data-testid="society-comparison-balance" className={`mt-1 ${delta !== null && delta < 0 ? "text-red-300" : "text-cyan-300"}`}>{signedKwh(delta)}</dd></div>}
    </dl>
    <EnergyComparisonChart scope="society" rows={data.society.rows.slice(-days)} mode={data.mode} excessEnabled={excessEnabled} />
  </section>;
}