// Allocation presentation. Independent of CalculationMode (AUTO | MANUAL), which remains
// the consumption data source. The persisted mode arrives from the energy summary.
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

// DAY_BASED card summary. Same cycle as backend/energy_api/day_allocation.py and
// pi_firmware/energy/day_based.py: cycle day 1 is the reset day, and enabled wings
// with a positive target_days take the next sequential days. Completed means elapsed
// scheduled days before the current cycle day. The legacy quota counter is not an input.
const DAY_WINGS = ["A", "B", "C", "D"] as const;
export type DayWing = (typeof DAY_WINGS)[number];
export type DayBasedStatus = "COMPLETED" | "CURRENT" | "UPCOMING" | "EXCLUDED" | "UNAVAILABLE";
export type DayBasedWingPresentation = {
  wing: DayWing;
  enabled: boolean;
  assignedDays: number | null;
  cycleDays: number | null;
  cycleDayIndex: number | null;
  startDay: number | null;
  endDay: number | null;
  completedDays: number | null;
  remainingDays: number | null;
  status: DayBasedStatus;
  scheduleLabel: string;
  isTodayScheduled: boolean;
  scheduledWing: DayWing | null;
};

export type DayBasedWingInput = { wing: DayWing; enabled: boolean; assignedDays: number | null | undefined };

function utcDate(year: number, month: number, day: number): Date {
  return new Date(Date.UTC(year, month - 1, day));
}

function parseOperatingDate(value: string | null | undefined): Date | null {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const [year, month, day] = value.split("-").map(Number);
  const date = utcDate(year, month, day);
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return null;
  return date;
}

function periodBounds(operating: Date, resetDay: number): { start: Date; end: Date } | null {
  if (!Number.isInteger(resetDay) || resetDay < 1 || resetDay > 28) return null;
  const year = operating.getUTCFullYear();
  const month = operating.getUTCMonth() + 1;
  const day = operating.getUTCDate();
  if (day >= resetDay) {
    return {
      start: utcDate(year, month, resetDay),
      end: month === 12 ? utcDate(year + 1, 1, resetDay) : utcDate(year, month + 1, resetDay),
    };
  }
  return {
    start: month === 1 ? utcDate(year - 1, 12, resetDay) : utcDate(year, month - 1, resetDay),
    end: utcDate(year, month, resetDay),
  };
}

function daySpan(start: Date, end: Date): number {
  return Math.round((end.getTime() - start.getTime()) / 86400000);
}

function assignedCount(value: number | null | undefined): number | null {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) return null;
  return value;
}

export function cycleDayIndex(operatingDate: string | null | undefined, resetDay: number | null | undefined): number | null {
  const operating = parseOperatingDate(operatingDate);
  if (!operating || typeof resetDay !== "number") return null;
  const bounds = periodBounds(operating, resetDay);
  if (!bounds) return null;
  return daySpan(bounds.start, operating) + 1;
}

export function dayBasedPresentation(input: {
  operatingDate: string | null | undefined;
  resetDay: number | null | undefined;
  wings: DayBasedWingInput[];
}): { cycleDays: number | null; cycleDayIndex: number | null; scheduledWing: DayWing | null; wings: DayBasedWingPresentation[] } {
  const operating = parseOperatingDate(input.operatingDate);
  const bounds = operating && typeof input.resetDay === "number" ? periodBounds(operating, input.resetDay) : null;
  const index = bounds && operating ? daySpan(bounds.start, operating) + 1 : null;
  const cycleDays = bounds ? daySpan(bounds.start, bounds.end) : null;
  const byWing = new Map(input.wings.map((wing) => [wing.wing, wing]));
  const ranges = new Map<DayWing, { start: number; end: number }>();
  if (index != null) {
    let cursor = 1;
    for (const wing of DAY_WINGS) {
      const row = byWing.get(wing);
      const count = row?.enabled ? assignedCount(row.assignedDays) : null;
      if (!row?.enabled || count == null || count <= 0) continue;
      ranges.set(wing, { start: cursor, end: cursor + count - 1 });
      cursor += count;
    }
  }
  const scheduled = index == null ? null : DAY_WINGS.find((wing) => {
    const range = ranges.get(wing);
    return range != null && range.start <= index && index <= range.end;
  }) ?? null;
  const wings = DAY_WINGS.map((wing): DayBasedWingPresentation => {
    const row = byWing.get(wing);
    const enabled = row?.enabled === true;
    const assigned = row ? assignedCount(row.assignedDays) : null;
    const blank = { wing, enabled, assignedDays: assigned, cycleDays, cycleDayIndex: index, startDay: null, endDay: null, completedDays: null, remainingDays: null, isTodayScheduled: false, scheduledWing: scheduled };
    if (!row || assigned == null || index == null || cycleDays == null) {
      return { ...blank, status: enabled ? "UNAVAILABLE" : "EXCLUDED", scheduleLabel: enabled ? "UNAVAILABLE" : "EXCLUDED" };
    }
    if (!enabled || assigned === 0) {
      return { ...blank, completedDays: 0, remainingDays: assigned, status: "EXCLUDED", scheduleLabel: enabled ? "NOT SCHEDULED" : "EXCLUDED" };
    }
    const range = ranges.get(wing);
    if (!range) return { ...blank, status: "UNAVAILABLE", scheduleLabel: "UNAVAILABLE" };
    if (index > range.end) {
      return { ...blank, startDay: range.start, endDay: range.end, completedDays: assigned, remainingDays: 0, status: "COMPLETED", scheduleLabel: "COMPLETED" };
    }
    if (index < range.start) {
      return { ...blank, startDay: range.start, endDay: range.end, completedDays: 0, remainingDays: assigned, status: "UPCOMING", scheduleLabel: "UPCOMING" };
    }
    const completedDays = index - range.start;
    return { ...blank, startDay: range.start, endDay: range.end, completedDays, remainingDays: assigned - completedDays, status: "CURRENT", scheduleLabel: "CURRENT · TODAY", isTodayScheduled: true };
  });
  return { cycleDays, cycleDayIndex: index, scheduledWing: scheduled, wings };
}
