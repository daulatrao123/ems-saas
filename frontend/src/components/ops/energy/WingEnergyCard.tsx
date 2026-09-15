"use client";
import { useContext } from "react";
import { label } from "../DashboardHeader";
import { BillHistoryButton } from "./BillHistoryButton";
import { EnergyComparisonChart, signedKwh } from "./EnergyComparisonChart";
import { CalendarComparisonContext } from "./CalendarComparisonContext";
import { missingReason, monthLabel } from "./comparisonLabels";
import { AllocationConfig, CalculationMode, ComparisonSeries, WingCode, WingSummary, WING_METERS, fmtKwh, fmtPct, sourceLabel, sourceTone, todayKwh } from "./types";

type Props = { code: WingCode; wing?: WingSummary; comparison?: ComparisonSeries; mode?: CalculationMode; allocation: AllocationConfig | null; activeGenerationWing?: string; excessEnabled: boolean };

export function WingEnergyCard({ code: w, wing: candidate, comparison, mode, allocation, activeGenerationWing, excessEnabled }: Props) {
  const wing = candidate?.wing === w ? candidate : undefined;
  const meter = wing?.consumption_meter?.meter_id === WING_METERS[w] ? wing.consumption_meter : undefined;
  const gen = todayKwh(wing?.generation), source = gen === null ? "UNAVAILABLE" : "PHYSICAL";
  const data = comparison?.today;
  const calendar = useContext(CalendarComparisonContext);
  const rows = calendar?.month ? calendar.data?.wings[w]?.rows : comparison?.rows.slice(-7);
  const reference = rows?.find((row) => row.consumption_source === "HISTORICAL");
  const cons = data?.consumed_kwh ?? null, delta = data?.generation_minus_consumption_kwh ?? null;
  const wingConfig = allocation?.wings?.[w], index = allocation?.sequence?.indexOf(w) ?? -1;
  const policy = !allocation ? "UNAVAILABLE" : allocation.enabled === false ? "DISABLED" : "ENABLED (CONFIGURATION)";
  const target = wing?.required_generation, progress = target?.achievement_percent;
  return <div data-testid={`energy-wing-card-${w}`} className="border-t border-[#1e2a3a] pt-3 space-y-3 min-w-0">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 data-testid={`energy-wing-meter-identity-${w}`} className={label}>Energy · Wing {w}</h3><span data-testid={`energy-wing-source-${w}`} className={`px-2 py-0.5 text-[10px] font-bold border ${sourceTone(source)}`}>{sourceLabel(source).toUpperCase()}</span></div>
    <dl className="grid grid-cols-2 gap-x-3 gap-y-2 font-mono text-[11px] [&>dd]:min-w-0 [&>dd]:break-words [&>dt]:min-w-0">
      <dt className="text-gray-500">ACTUAL GENERATION</dt><dd data-testid={`energy-wing-generation-${w}`} className="text-emerald-300">{fmtKwh(gen)}</dd>
      <dt className="text-gray-500">CONSUMPTION · {mode === "MANUAL" ? "DAILY REFERENCE" : "ACTUAL"}</dt><dd data-testid={`energy-wing-consumption-${w}`} className="text-amber-300">{fmtKwh(cons)}<span data-testid={`energy-wing-consumption-source-${w}`} className="block text-[10px]">{sourceLabel(data?.consumption_source)}</span></dd>
      {(delta === null || delta <= 0 || excessEnabled) && <><dt className="text-gray-500">GENERATION − CONSUMPTION</dt><dd data-testid={`energy-wing-balance-${w}`} className={delta !== null && delta < 0 ? "text-red-300" : "text-cyan-300"}>{signedKwh(delta)}</dd></>}
      <dt className="text-gray-500">REQUIRED TARGET · REFERENCE</dt><dd data-testid={`energy-wing-target-${w}`} className="text-gray-300">{fmtKwh(target?.target_kwh_per_day)} / day</dd>
      <dt className="text-gray-500">TARGET / ACHIEVEMENT</dt><dd data-testid={`energy-wing-target-status-${w}`} className="text-gray-300">{target?.status ?? "UNAVAILABLE"} · {fmtPct(progress)}</dd>
      <dt className="text-gray-500">ALLOCATION POLICY</dt><dd data-testid={`energy-wing-allocation-${w}`} className="text-gray-300">{policy}</dd>
      <dt className="text-gray-500">SEQUENCE · CONFIGURED</dt><dd data-testid={`energy-wing-sequence-${w}`} className="text-gray-300">{!allocation || !wingConfig || index < 0 ? "UNAVAILABLE" : wingConfig.generation_attribution_enabled === false ? "EXCLUDED" : `#${index + 1}`}</dd>
      <dt className="text-gray-500">LIVE ALLOCATION STATUS</dt><dd data-testid={`energy-wing-runtime-${w}`} className="text-gray-500">UNAVAILABLE</dd>
      <dt className="text-gray-500">ACTIVE GENERATION WING</dt><dd data-testid={`energy-wing-active-${w}`} className="text-gray-300">{activeGenerationWing === w ? `Wing ${w} · Pi feedback` : "—"}</dd>
    </dl>
    {progress != null && <div data-testid={`energy-wing-achievement-${w}`} role="meter" aria-label={`Wing ${w} reference target achievement`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(100, Math.max(0, progress))} aria-valuetext={`${progress}% of reference target`} className="h-2 bg-[#0a0e17]"><div className="h-full bg-emerald-400" style={{ width: `${Math.min(100, Math.max(0, progress))}%` }} /></div>}
    <div data-testid={`energy-wing-display-date-${w}`} className="text-[10px] text-gray-500">Operational values: {data?.date || calendar?.operatingDate || "UNAVAILABLE"}</div>
    {mode === "MANUAL" && <div data-testid={`energy-wing-bill-reference-${w}`} className="text-xs text-amber-200">{calendar?.month ? `${monthLabel(calendar.month)} daily table: ` : "Daily table: "}{reference ? `${fmtKwh(reference.consumed_kwh)} / day · bill ${reference.bill_month || reference.date.slice(0, 7)}` : calendar?.loading ? "Loading bill reference…" : "Matching monthly bill unavailable"}</div>}
    {data && data.consumed_kwh === null && <div data-testid={`energy-wing-operating-consumption-reason-${w}`} className="text-[10px] text-gray-400">Pi-day consumption: {missingReason(data, "consumption")}</div>}
    {mode && rows && <EnergyComparisonChart scope={w} rows={rows} mode={mode} excessEnabled={excessEnabled} compact />}
    {calendar?.month && !rows && <div data-testid={`energy-wing-month-state-${w}`} className="text-xs text-gray-500">{calendar.loading ? `Loading ${calendar.month}…` : `Daily values unavailable for ${calendar.month}`}</div>}
    <div data-testid={`energy-wing-owner-${w}`} className="text-[10px] text-gray-500">{meter ? `Consumption meter · ${meter.meter_id}` : "Consumption meter · UNAVAILABLE"}<span data-testid={`energy-wing-health-${w}`} className="block">{meter ? meter.enabled ? meter.comm_status : "DISABLED" : "UNAVAILABLE"}</span></div>
    {mode === "MANUAL" && <BillHistoryButton wing={w} />}
  </div>;
}