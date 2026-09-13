"use client";
import { btn, label, panel, tone } from "../DashboardHeader";
import { AllocationTimeline } from "./AllocationTimeline";
import { GenerationCard } from "./GenerationCard";
import { WingEnergyCard } from "./WingEnergyCard";
import { WINGS } from "./types";
import { useEnergy } from "./useEnergy";

// E4 energy view. Read-only for every role (GET-only): no energy configuration or command controls live here.
export function EnergyPanel({ societyId, deviceId }: { societyId: string; deviceId: string }) {
  const en = useEnergy(societyId, deviceId);
  if (en.loading && !en.summary) return <div data-testid="energy-loading" className={`${panel} p-4 font-mono text-[11px] text-gray-500`}>LOADING ENERGY…</div>;
  return (
    <div data-testid="energy-panel" className="space-y-3">
      <div className="flex items-center justify-between">
        <div className={label}>Energy · M1 generation + wing consumption meters (RS485) · {en.summary ? `as of ${en.summary.as_of_operating_date}` : "unavailable"}</div>
        <button data-testid="energy-refresh" onClick={() => en.refresh()} className={`${btn} ${tone.gray}`}>REFRESH</button>
      </div>
      {en.error && <div data-testid="energy-error" className="border border-red-500/40 bg-red-500/10 px-4 py-2 font-mono text-xs text-red-300">{en.error}</div>}
      {!en.summary ? (
        <div data-testid="energy-unavailable" className={`${panel} p-6 text-center font-mono text-sm text-gray-500`}>ENERGY DATA UNAVAILABLE</div>
      ) : (
        <>
          <GenerationCard meter={en.summary.generation_meter} monthly={en.monthly} operatingDate={en.summary.as_of_operating_date} resetPeriod={en.summary.reset_period} />
          <div data-testid="energy-wing-grid" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {WINGS.map((w) => (
              <WingEnergyCard key={w} wing={en.summary!.wings[w]} sequenceIndex={(en.allocation?.sequence ?? WINGS).indexOf(w)} allocationEnabled={!!en.allocation?.enabled}
                wingConfig={en.allocation?.wings?.[w]} activeGenerationWing={en.summary!.generation_meter.active_generation_wing} />
            ))}
          </div>
          <AllocationTimeline events={en.events} operatingDate={en.summary.as_of_operating_date} />
        </>
      )}
    </div>
  );
}
