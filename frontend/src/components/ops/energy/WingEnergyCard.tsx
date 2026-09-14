"use client";
import { label } from "../DashboardHeader";
import { AllocationConfig, WingCode, WingSummary, WING_METERS, fmtKw, fmtKwh, fmtPct, reasonOf, sourceLabel, sourceTone, todayKwh } from "./types";

type Props = { code: WingCode; wing?: WingSummary; allocation: AllocationConfig | null; activeGenerationWing?: string };

// Unframed energy details INSIDE the owning SlotCard. EMS identity is not a Modbus slave address.
export function WingEnergyCard({ code: w, wing: candidate, allocation, activeGenerationWing }: Props) {
  const wing = candidate?.wing === w ? candidate : undefined;
  const meter = wing?.consumption_meter?.meter_id === WING_METERS[w] ? wing.consumption_meter : undefined;
  const gen = todayKwh(wing?.generation);
  const cons = meter?.enabled === true ? todayKwh(wing?.consumption) : null;
  const req = meter ? wing?.required_generation : undefined;
  const targetStatus = gen != null && req ? req.status : "UNAVAILABLE";
  const wingConfig = allocation?.wings?.[w];
  const sequenceIndex = allocation?.sequence?.indexOf(w) ?? -1;
  const alloc = !allocation ? "UNAVAILABLE" : allocation.enabled === false ? "POLICY DISABLED"
    : !wingConfig ? "UNAVAILABLE" : wingConfig.generation_attribution_enabled === false ? "EXCLUDED (attribution off)"
    : sequenceIndex < 0 ? "UNAVAILABLE" : `SEQUENCE #${sequenceIndex + 1}${wingConfig.manual_target_kwh != null ? " · MANUAL TARGET" : ""}`;
  const meterState = !meter ? "UNAVAILABLE" : meter.enabled === false ? "DISABLED" : meter.comm_status || "UNAVAILABLE";
  const meterTone = meter?.enabled && meter.comm_status === "ONLINE" ? "text-emerald-300" : "text-gray-400";
  const source = wing && meter ? wing.source : "UNAVAILABLE";
  return (
    <div data-testid={`energy-wing-card-${w}`} className="border-t border-[#1e2a3a] pt-3 space-y-3 min-w-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 data-testid={`energy-wing-meter-identity-${w}`} className={label}>{meter ? `${meter.meter_id} · Consumption Meter` : "Consumption Meter · UNAVAILABLE"}</h3>
        <span data-testid={`energy-wing-source-${w}`} className={`px-2 py-0.5 text-[10px] font-bold border ${sourceTone(source)}`}>{sourceLabel(source).toUpperCase()}</span>
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-2 font-mono text-[11px] [&>dd]:min-w-0 [&>dd]:break-words">
        <dt className="text-gray-500">REQUIRED TARGET</dt>
        <dd data-testid={`energy-wing-target-${w}`} className="text-gray-300">{fmtKwh(req?.target_kwh_per_day)}{req?.target_kwh_per_day != null && <span className="text-gray-500"> / day{req.adjustment_percent != null ? ` · avg ${fmtKwh(req.base_daily_average_kwh)} × (1 + ${req.adjustment_percent}%)` : ""}</span>}</dd>
        <dt className="text-gray-500">ACTUAL GENERATION</dt>
        <dd data-testid={`energy-wing-generation-${w}`} className={gen == null ? "text-gray-500" : "text-emerald-300"}>{fmtKwh(gen)}{gen == null && <span> · {reasonOf(wing?.generation) || "Wing-specific physical attribution unavailable"}</span>}</dd>
        <dt className="text-gray-500">ACTUAL CONSUMPTION</dt>
        <dd data-testid={`energy-wing-consumption-${w}`} className={cons == null ? "text-gray-500" : "text-cyan-300"}>{fmtKwh(cons)}{cons == null && <span> · {!meter ? "Meter ownership unavailable" : !meter.enabled ? "Consumption meter disabled" : reasonOf(wing?.consumption) || "No physical consumption data"}</span>}</dd>
        <dt className="text-gray-500">TARGET STATUS</dt>
        <dd data-testid={`energy-wing-target-status-${w}`} className="text-gray-300">{targetStatus || "UNAVAILABLE"}{gen != null && req?.achievement_percent != null && <span> · {fmtPct(req.achievement_percent)}</span>}</dd>
        <dt className="text-gray-500">METER STATUS</dt>
        <dd data-testid={`energy-wing-meter-${w}`} className={meterTone}>{meterState}{meter?.serial && <span> · {meter.serial}</span>}</dd>
        <dt className="text-gray-500">POWER NOW</dt>
        <dd data-testid={`energy-wing-power-${w}`} className="text-gray-300">{meter?.enabled && meter.comm_status === "ONLINE" && meter.power_kw != null ? fmtKw(meter.power_kw) : "UNAVAILABLE"}</dd>
        <dt className="text-gray-500">ALLOCATION</dt>
        <dd data-testid={`energy-wing-allocation-${w}`} className="text-gray-300">{alloc}{gen != null && activeGenerationWing === w && <span className="block text-emerald-300">GENERATION ATTRIBUTED (Pi feedback)</span>}</dd>
      </dl>
    </div>
  );
}