"use client";
import { label } from "../DashboardHeader";
import { GenerationTrend } from "./GenerationTrend";
import { BillHistoryButton } from "./BillHistoryButton";
import { AllocationConfig, CalculationMode, CalculationWing, WingCode, WingSummary, WING_METERS, fmtKwh, fmtPct, sourceLabel, sourceTone } from "./types";

type Props = { code: WingCode; wing?: WingSummary; calculation?: CalculationWing; mode?: CalculationMode; allocation: AllocationConfig | null; activeGenerationWing?: string };

// Energy data only. Days/command transmission details live outside these cards.
export function WingEnergyCard({ code: w, wing: candidate, calculation, mode, allocation, activeGenerationWing }: Props) {
  const wing = candidate?.wing === w ? candidate : undefined;
  const meter = wing?.consumption_meter?.meter_id === WING_METERS[w] ? wing.consumption_meter : undefined;
  const meterReason = meter && wing?.consumption && "reason" in wing.consumption ? wing.consumption.reason : null;
  const selected = calculation?.wing === w ? calculation : undefined;
  const expectedSource = mode === "MANUAL" ? "MANUAL" : "PHYSICAL";
  const data = selected?.today;
  const gen = mode && data?.generation_source === expectedSource && data.generated_kwh != null && Number.isFinite(data.generated_kwh) ? data.generated_kwh : null;
  const source = gen === null ? "UNAVAILABLE" : expectedSource;
  const wingConfig = allocation?.wings?.[w];
  const index = allocation?.sequence?.indexOf(w) ?? -1;
  const policy = !allocation ? "UNAVAILABLE" : allocation.enabled === false ? "DISABLED" : "ENABLED (CONFIGURATION)";
  const progress = data?.required_kwh != null && data.required_kwh > 0 && gen !== null ? data.target_achievement_percent : null;
  return (
    <div data-testid={`energy-wing-card-${w}`} className="border-t border-[#1e2a3a] pt-3 space-y-3 min-w-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 data-testid={`energy-wing-meter-identity-${w}`} className={label}>Generation · Wing {w}</h3>
        <span data-testid={`energy-wing-source-${w}`} className={`px-2 py-0.5 text-[10px] font-bold border ${sourceTone(source)}`}>{sourceLabel(source).toUpperCase()}</span>
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-2 font-mono text-[11px] [&>dd]:min-w-0 [&>dd]:break-words">
        <dt className="text-gray-500">{mode === "MANUAL" ? "ENTERED GENERATION" : "ACTUAL GENERATION"}</dt>
        <dd data-testid={`energy-wing-generation-${w}`} className={gen === null ? "text-gray-500" : mode === "MANUAL" ? "text-amber-300" : "text-emerald-300"}>{fmtKwh(gen)}</dd>
        {mode === "AUTO" && <>
          <dt className="text-gray-500">REQUIRED TARGET · REFERENCE</dt>
          <dd data-testid={`energy-wing-target-${w}`} className="text-gray-300">{fmtKwh(data?.required_kwh)} / day</dd>
          <dt className="text-gray-500">TARGET / ACHIEVEMENT</dt>
          <dd data-testid={`energy-wing-target-status-${w}`} className="text-gray-300">{gen === null ? "UNAVAILABLE" : data?.target_status} · {fmtPct(progress)}</dd>
          <dt className="text-gray-500">AUTO ALLOCATION</dt>
          <dd data-testid={`energy-wing-allocation-${w}`} className="text-gray-300">{policy}</dd>
          <dt className="text-gray-500">AUTO SEQUENCE · CONFIGURED</dt>
          <dd data-testid={`energy-wing-sequence-${w}`} className="text-gray-300">{!allocation || !wingConfig || index < 0 ? "UNAVAILABLE" : wingConfig.generation_attribution_enabled === false ? "EXCLUDED" : `#${index + 1}`}{wingConfig?.manual_target_kwh != null && <span className="block">Explicit policy target: {fmtKwh(wingConfig.manual_target_kwh)}</span>}</dd>
          <dt className="text-gray-500">LIVE ALLOCATION STATUS</dt><dd data-testid={`energy-wing-runtime-${w}`} className="text-gray-500">UNAVAILABLE</dd>
          <dt className="text-gray-500">ACTIVE GENERATION WING</dt><dd data-testid={`energy-wing-active-${w}`} className="text-gray-300">{activeGenerationWing === w ? `Wing ${w} · Pi feedback` : "—"}</dd>
        </>}
        {mode === "MANUAL" && <><dt className="text-gray-500">MANUAL DATA STATUS</dt><dd data-testid={`energy-wing-manual-status-${w}`} className="text-gray-300">{gen === null ? "NO GENERATION ENTRY" : "ENTRY RECORDED"}</dd></>}
      </dl>
      {mode === "AUTO" && progress != null && <div data-testid={`energy-wing-achievement-${w}`} role="meter" aria-label={`Wing ${w} reference target achievement`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(100, Math.max(0, progress))} aria-valuetext={`${progress}% of reference target`} className="h-2 bg-[#0a0e17]"><div className="h-full bg-emerald-400" style={{ width: `${Math.min(100, Math.max(0, progress))}%` }} /></div>}
      {mode && selected && <GenerationTrend wing={w} rows={selected.generation_trend} source={expectedSource} />}
      <div data-testid={`energy-wing-owner-${w}`} className="text-[10px] text-gray-500">{meter ? `Meter ownership · ${meter.meter_id}` : "Meter ownership · UNAVAILABLE"}
        <span data-testid={`energy-wing-health-${w}`} className="block">{meter ? meter.enabled ? meter.comm_status : "DISABLED" : "UNAVAILABLE"}</span>
        {meterReason && <span data-testid={`energy-wing-unavailable-${w}`} className="block break-words">{meterReason}</span>}
      </div>
      <BillHistoryButton wing={w} />
    </div>
  );
}