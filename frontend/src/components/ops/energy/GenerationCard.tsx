"use client";
import { label, panel } from "../DashboardHeader";
import { MonthlyBars } from "./MonthlyBars";
import { GenerationMeter, MonthlyGeneration, fmtKw, fmtKwh, reasonOf, sourceLabel, sourceTone, todayKwh } from "./types";

type Props = { meter: GenerationMeter; monthly: MonthlyGeneration | null; operatingDate: string; resetPeriod: string };

// M1 common generation meter: today / reset-period generation, attribution, health and the 6-month bar chart.
export function GenerationCard({ meter, monthly, operatingDate, resetPeriod }: Props) {
  const today = todayKwh(meter.generation); const period = meter.generation && "reset_period" in meter.generation ? meter.generation.reset_period.kwh : null;
  const unattr = todayKwh(meter.unattributed);
  const online = meter.status === "ONLINE";
  const healthTone = online ? "text-emerald-300 border-emerald-500/40" : meter.status === "DISABLED" ? "text-gray-500 border-gray-700" : "text-red-300 border-red-500/50";
  const active = meter.active_generation_wing;
  const activeTone = active === "FAULT" ? "text-red-300" : active === "UNAVAILABLE" || active === "UNATTRIBUTED" ? "text-gray-500" : "text-emerald-300";
  return (
    <section data-testid="energy-generation-card" className={`${panel} p-4 flex flex-col gap-3`}>
      <div className="flex items-start justify-between gap-2">
        <div><div className="text-sm font-bold text-white leading-tight">M1 · Common Generation Meter</div><div className={label}>{meter.model || "model —"}{meter.serial ? ` · ${meter.serial}` : ""} · operating date {operatingDate}</div></div>
        <div className="flex gap-1">
          <span data-testid="energy-generation-source" className={`px-2 py-0.5 text-[10px] font-bold border ${sourceTone(meter.source)}`}>{sourceLabel(meter.source).toUpperCase()}</span>
          <span data-testid="energy-generation-health" className={`px-2 py-0.5 text-[10px] font-bold border ${healthTone}`}>{meter.status}</span>
        </div>
      </div>
      <dl className="grid grid-cols-2 sm:grid-cols-4 gap-x-3 gap-y-1 font-mono text-[11px]">
        <dt className="text-gray-500">TODAY</dt><dd data-testid="energy-generation-today" className={today == null ? "text-gray-500" : "text-emerald-300"}>{fmtKwh(today)}</dd>
        <dt className="text-gray-500">POWER NOW</dt><dd className="text-gray-300">{fmtKw(meter.current_power_kw)}</dd>
        <dt className="text-gray-500">RESET PERIOD {resetPeriod}</dt><dd data-testid="energy-generation-period" className={period == null ? "text-gray-500" : "text-white"}>{fmtKwh(period)}</dd>
        <dt className="text-gray-500">UNATTRIBUTED TODAY</dt><dd className={unattr == null ? "text-gray-500" : "text-amber-300"}>{fmtKwh(unattr)}</dd>
        <dt className="text-gray-500">ATTRIBUTED TO</dt>
        <dd data-testid="energy-generation-active-wing" className={`${activeTone} sm:col-span-3`}>{active === "UNAVAILABLE" || active === "UNATTRIBUTED" || active === "FAULT" ? active : `WING ${active}`}<span className="text-gray-600"> · verified contactor feedback, one wing at a time</span></dd>
      </dl>
      {today == null && reasonOf(meter.generation) && <div data-testid="energy-generation-unavailable" className="border border-gray-700 bg-[#0a0e17] px-3 py-2 font-mono text-[10px] text-gray-500">GENERATION UNAVAILABLE — {reasonOf(meter.generation)}</div>}
      <div>
        <div className="flex items-center justify-between"><div className={label}>Monthly generation · last {monthly?.months ?? 6} months</div>{monthly && <span className="font-mono text-[10px] text-gray-500">{sourceLabel(monthly.source).toUpperCase()} · ▮ complete ▮ partial · N/A = no physical data</span>}</div>
        {monthly ? <MonthlyBars rows={monthly.rows} unit={monthly.unit} /> : <div data-testid="energy-monthly-unavailable" className="mt-2 font-mono text-[10px] text-gray-500">MONTHLY GENERATION UNAVAILABLE</div>}
      </div>
    </section>
  );
}
