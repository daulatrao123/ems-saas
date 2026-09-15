"use client";
import { label, panel } from "../DashboardHeader";
import { MonthlyBars } from "./MonthlyBars";
import { GenerationMeter, MonthlyGeneration, fmtKw, fmtKwh, reasonOf, sourceLabel, sourceTone, todayKwh } from "./types";

type Props = { meter: GenerationMeter; monthly: MonthlyGeneration | null; operatingDate: string; resetPeriod: string; activeGenerationWing?: string };

// M1 common generation meter: today / reset-period generation, attribution, health and the 6-month bar chart.
export function GenerationCard({ meter, monthly, operatingDate, resetPeriod, activeGenerationWing }: Props) {
  const enabled = meter.status !== "DISABLED";
  const today = enabled ? todayKwh(meter.generation) : null; const period = enabled && meter.generation && "reset_period" in meter.generation && meter.generation.reset_period.status === "PHYSICAL" ? meter.generation.reset_period.kwh : null;
  const unattr = enabled ? todayKwh(meter.unattributed) : null;
  const online = meter.status === "ONLINE";
  const healthTone = online ? "text-emerald-300 border-emerald-500/40" : meter.status === "DISABLED" ? "text-gray-500 border-gray-700" : "text-red-300 border-red-500/50";
  return (
    <section data-testid="energy-generation-card" className={`${panel} p-4 flex flex-col gap-3`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0"><h2 data-testid="energy-generation-heading" className="text-sm font-bold text-white leading-tight">Society Generation</h2><div data-testid="energy-generation-identity" className={`${label} break-words`}>M1 · Common Generation Meter · {meter.model || "model —"}{meter.serial ? ` · ${meter.serial}` : ""} · operating date {operatingDate}</div></div>
        <div className="flex flex-wrap gap-1">
          <span data-testid="energy-generation-source" className={`px-2 py-0.5 text-[10px] font-bold border ${sourceTone(meter.source)}`}>{sourceLabel(meter.source).toUpperCase()}</span>
          <span data-testid="energy-generation-health" className={`px-2 py-0.5 text-[10px] font-bold border ${healthTone}`}>{meter.status || "UNAVAILABLE"}</span>
        </div>
      </div>
      <dl className="grid grid-cols-2 sm:grid-cols-4 gap-x-3 gap-y-1 font-mono text-[11px]">
        <dt className="text-gray-500">TODAY</dt><dd data-testid="energy-generation-today" className={today == null ? "text-gray-500" : "text-emerald-300"}>{fmtKwh(today)}</dd>
        <dt className="text-gray-500">POWER NOW</dt><dd data-testid="energy-generation-power" className="text-gray-300">{fmtKw(online ? meter.current_power_kw : null)}</dd>
        <dt className="text-gray-500">RESET PERIOD {resetPeriod}</dt><dd data-testid="energy-generation-period" className={period == null ? "text-gray-500" : "text-white"}>{fmtKwh(period)}</dd>
        <dt className="text-gray-500">UNATTRIBUTED TODAY</dt><dd data-testid="energy-generation-unattributed" className={unattr == null ? "text-gray-500" : "text-amber-300"}>{fmtKwh(unattr)}</dd>
      </dl>
      <div data-testid="energy-active-wing" className="text-[11px] text-gray-400">Active wing · Pi attribution: {activeGenerationWing || "UNAVAILABLE"}</div>
      {today == null && reasonOf(meter.generation) && <div data-testid="energy-generation-unavailable" className="border border-gray-700 bg-[#0a0e17] px-3 py-2 font-mono text-[10px] text-gray-500">GENERATION UNAVAILABLE — {reasonOf(meter.generation)}</div>}
      <div>
        <div className="flex flex-wrap items-center justify-between gap-2"><h3 id="monthly-generation-heading" data-testid="energy-monthly-heading" className={label}>Monthly Generation — Last 6 Months</h3><span data-testid="energy-monthly-legend" className="font-mono text-[10px] text-gray-500">Complete · Partial · N/A = no physical data</span></div>
        {monthly ? <MonthlyBars rows={monthly.rows} unit={monthly.unit} /> : <div data-testid="energy-monthly-unavailable" className="mt-2 font-mono text-[10px] text-gray-500">MONTHLY GENERATION UNAVAILABLE</div>}
      </div>
    </section>
  );
}
