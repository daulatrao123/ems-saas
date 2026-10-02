// Presentation-only allocation selection. Independent of CalculationMode (AUTO | MANUAL),
// which remains the consumption data source. This value is not persisted.
export type AllocationMode = "AUTO" | "MANUAL" | "DAY_BASED";
export const ALLOCATION_MODES = ["AUTO", "MANUAL", "DAY_BASED"] as const;
export type GenerationTargetVisibility = "show" | "optional" | "hide";
export type AllocationVisibility = {
  generationMeter: true;
  consumptionMeter: true;
  meterStatus: true;
  actualReadings: true;
  excessGeneration: boolean;
  generationTarget: GenerationTargetVisibility;
  dayAllocation: boolean;
  cycleSettings: boolean;
  manualControl: true;
  manualControlLabel: string;
};

export function isAllocationMode(value: string): value is AllocationMode {
  return (ALLOCATION_MODES as readonly string[]).includes(value);
}

export function allocationVisibility(mode: AllocationMode): AllocationVisibility {
  const meters = { generationMeter: true as const, consumptionMeter: true as const, meterStatus: true as const, actualReadings: true as const, manualControl: true as const };
  if (mode === "MANUAL") return { ...meters, excessGeneration: true, generationTarget: "optional", dayAllocation: false, cycleSettings: false, manualControlLabel: "Manual allocation" };
  if (mode === "DAY_BASED") return { ...meters, excessGeneration: false, generationTarget: "hide", dayAllocation: true, cycleSettings: true, manualControlLabel: "Manual override" };
  return { ...meters, excessGeneration: true, generationTarget: "show", dayAllocation: false, cycleSettings: false, manualControlLabel: "Manual override · emergency" };
}

export function showGenerationTarget(mode: AllocationMode, targetKwh: number | null | undefined): boolean {
  const rule = allocationVisibility(mode).generationTarget;
  if (rule === "show") return true;
  if (rule === "hide") return false;
  return targetKwh != null && Number.isFinite(targetKwh);
}

// Positive values are surplus. Zero, deficit, and missing values stay visible.
// When surplus is allowed, the existing grid-reference gate still applies.
export function excessPresentationEnabled(mode: AllocationMode, gridExcessEnabled: boolean): boolean {
  return allocationVisibility(mode).excessGeneration && gridExcessEnabled;
}

export function showBalance(mode: AllocationMode, value: number | null | undefined, gridExcessEnabled: boolean): boolean {
  if (value == null || !Number.isFinite(value) || value <= 0) return true;
  return excessPresentationEnabled(mode, gridExcessEnabled);
}
