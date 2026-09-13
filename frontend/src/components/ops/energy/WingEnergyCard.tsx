"use client";
import { label, panel } from "../DashboardHeader";
import { WingAllocationConfig, WingSummary, fmtKw, fmtKwh, fmtPct, reasonOf, sourceLabel, sourceTone, todayKwh } from "./types";

type Props = { wing: WingSummary; sequenceIndex: number; allocationEnabled: boolean; wingConfig?: WingAllocationConfig; activeGenerationWing: string };

// One wing (A–D): required target, today's attributed generation and consumption, source badge, meter + allocation context.
// UNAVAILABLE is rendered as such — never as 0.
export function WingEnergyCard({ wing, sequenceIndex, allocationEnabled, wingConfig, activeGenerationWing }: Props) {
  const w = wing.wing; const gen = todayKwh(wing.generation); const cons = todayKwh(wing.consumption); const req = wing.required_generation;
  const meter = wing.consumption_meter; const meterState = !meter.enabled ? "DISABLED" : meter.comm_status;
  const meterTone = meter.enabled && meter.comm_status === "ONLINE" ? "text-emerald-300" : meter.enabled ? "text-red-300" : "text-gray-500";
  const attributed = activeGenerationWing === w;
  const targetTone = req.status === "REACHED" ? "text-emerald-300 border-emerald-500/40" : req.status === "NOT_REACHED" ? "text-amber-300 border-amber-500/40" : "text-gray-500 border-gray-700";
  const alloc = !allocationEnabled ? "POLICY DISABLED" : wingConfig && wingConfig.generation_attribution_enabled === false ? "EXCLUDED (attribution off)" : `SEQUENCE #${sequenceIndex + 1}${wingConfig?.manual_target_kwh != null ? " · MANUAL TARGET" : ""}`;
  return (
    <section data-testid={`energy-wing-card-${w}`} className={`${panel} ${attributed ? "border-emerald-500/40" : ""} p-4 flex flex-col gap-3`}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-3">
          <span className={`h-9 w-9 grid place-items-center font-mono text-lg font-bold border ${attributed ? "border-emerald-400 text-emerald-300" : "border-[#2a3646] text-gray-300"}`}>{w}</span>
          <div><div className="text-sm font-bold text-white leading-tight">Wing {w}</div><div className={label}>{meter.meter_id} · consumption meter</div></div>
        </div>
        <span data-testid={`energy-wing-source-${w}`} className={`px-2 py-0.5 text-[10px] font-bold border ${sourceTone(wing.source)}`}>{sourceLabel(wing.source).toUpperCase()}</span>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-[11px]">
        <dt className="text-gray-500">REQUIRED TARGET</dt>
        <dd data-testid={`energy-wing-target-${w}`} className={req.target_kwh_per_day == null ? "text-gray-500" : "text-white"}>
          {fmtKwh(req.target_kwh_per_day)}{req.target_kwh_per_day != null && <span className="text-gray-500"> / day{req.adjustment_percent != null ? ` · avg ${fmtKwh(req.base_daily_average_kwh)} × (1 + ${req.adjustment_percent}%)` : ""}</span>}
        </dd>
        <dt className="text-gray-500">ACTUAL GENERATION</dt>
        <dd data-testid={`energy-wing-generation-${w}`} className={gen == null ? "text-gray-500" : "text-emerald-300"}>{fmtKwh(gen)}{gen == null && reasonOf(wing.generation) ? <span className="text-gray-600"> · {reasonOf(wing.generation)}</span> : null}</dd>
        <dt className="text-gray-500">ACTUAL CONSUMPTION</dt>
        <dd data-testid={`energy-wing-consumption-${w}`} className={cons == null ? "text-gray-500" : "text-cyan-300"}>{fmtKwh(cons)}{cons == null && reasonOf(wing.consumption) ? <span className="text-gray-600"> · {reasonOf(wing.consumption)}</span> : null}</dd>
        <dt className="text-gray-500">TARGET STATUS</dt>
        <dd><span data-testid={`energy-wing-target-status-${w}`} className={`px-1.5 py-0.5 text-[10px] font-bold border ${targetTone}`}>{req.status.replace("_", " ")}</span>{req.achievement_percent != null && <span className="ml-2 text-gray-400">{fmtPct(req.achievement_percent)}</span>}</dd>
        <dt className="text-gray-500">METER</dt>
        <dd data-testid={`energy-wing-meter-${w}`} className={meterTone}>{meterState}{meter.enabled && meter.comm_status === "ONLINE" ? ` · ${fmtKw(meter.power_kw)}` : ""}{meter.serial ? <span className="text-gray-600"> · {meter.serial}</span> : null}</dd>
        <dt className="text-gray-500">ALLOCATION</dt>
        <dd data-testid={`energy-wing-allocation-${w}`} className="text-gray-300">{alloc}{attributed ? <span className="ml-2 text-emerald-300">● GENERATION ATTRIBUTED (verified feedback)</span> : null}</dd>
      </dl>
    </section>
  );
}
