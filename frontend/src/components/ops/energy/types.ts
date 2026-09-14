import { EnergyReferenceData } from "./referenceTypes";
// E4 energy view types — mirror backend/energy_api/routes.py (/summary, /generation/monthly, /allocation) and /api/admin/pi-events.
// Values are never coerced: null / UNAVAILABLE stays UNAVAILABLE (an OFF meter is NOT zero).
export type Period = { kwh: number | null; days: number; status: "PHYSICAL" | "UNAVAILABLE" };
export type Periods = { today: Period; yesterday: Period; this_month: Period; previous_month: Period; this_year: Period; lifetime: Period; reset_period: Period & { period: string } };
export type Unavailable = { status: "UNAVAILABLE"; reason: string };
export type Metric = Periods | Unavailable;
export type MeterRef = { meter_id: string; enabled: boolean; comm_status: string; serial: string | null; last_seen: string | null; power_kw: number | null };
export type RequiredGeneration = {
  target_kwh_per_day: number | null; adjustment_percent: number | null; base_daily_average_kwh: number | null; effective_from: string | null;
  generated_today_kwh: number | null; achievement_percent: number | null; status: "REACHED" | "NOT_REACHED" | "UNAVAILABLE";
};
export type WingSummary = { wing: string; consumption_meter: MeterRef; consumption: Metric; generation: Metric; required_generation: RequiredGeneration; source: string };
export type GenerationMeter = {
  meter_id: "M1"; status: string; serial: string | null; model: string | null; current_power_kw: number | null; generation: Metric;
  unattributed: Metric | null; active_generation_wing: string; attribution: { wing?: string | null; status?: string } | null; source: string;
};
export type CalculationMode = "AUTO" | "MANUAL";
export type GenerationPoint = { date: string; generated_kwh: number | null; generation_source: string };
export type CalculationWing = { wing: WingCode; today: GenerationPoint & { required_kwh: number | null; target_achievement_percent: number | null; target_status: string }; generation_trend: GenerationPoint[] };
export type Calculation = { mode: CalculationMode; version: number; operating_date: string; wings: Record<WingCode, CalculationWing> };
export type ManualEntry = { id: number; wing: WingCode | null; operating_date: string; kind: string; value_kwh: number; source: string; reason: string; created_at: string };
export type ManualEntryInput = { wing: WingCode; operating_date: string; kind: "MANUAL_GENERATION"; value_kwh: number; reason: string };
export type EnergySummary = { device_id: string; as_of_operating_date: string; reset_day: number; reset_period: string; generation_meter: GenerationMeter; wings: Record<string, WingSummary>; calculation: Calculation; references: EnergyReferenceData };
export type MonthRow = { month: string; generation_kwh: number | null; source: string; completeness: "COMPLETE" | "PARTIAL" | "UNAVAILABLE"; physical_days: number; expected_days: number };
export type MonthlyGeneration = { meter_id: "M1"; unit: string; months: number; as_of_operating_date: string; rows: MonthRow[]; source: string };
export type WingAllocationConfig = { generation_attribution_enabled: boolean; manual_target_kwh: number | null };
export type AllocationConfig = { enabled: boolean; sequence: string[]; tolerance_kwh: number; persistence_s: number; wings: Record<string, WingAllocationConfig> };

export const WING_METERS = { A: "M2", B: "M3", C: "M4", D: "M5" } as const;
export type WingCode = keyof typeof WING_METERS;
export const WINGS: WingCode[] = ["A", "B", "C", "D"];
export const DEFAULT_MONTHS = 6;
export const ALLOCATION_EVENT_PREFIX = "ENERGY_ALLOCATION_";

// Source labels the operator sees. ADAPTIVE_ESTIMATE is reserved for the E5 estimator; the backend does not emit it yet.
export const SOURCE_LABEL: Record<string, string> = { PHYSICAL: "Physical", ADAPTIVE_ESTIMATE: "Adaptive Estimate", MANUAL: "Manual", MIXED: "Mixed", UNAVAILABLE: "Unavailable" };
export const sourceLabel = (s: string | null | undefined) => { const k = (s || "").toUpperCase(); return SOURCE_LABEL[k] ?? (k ? `Unknown (${k})` : "Unavailable"); };
export const sourceTone = (s: string | null | undefined) => {
  const k = (s || "").toUpperCase();
  return k === "PHYSICAL" ? "text-emerald-300 border-emerald-500/40" : k === "MANUAL" ? "text-amber-300 border-amber-500/40" : k === "MIXED" || k === "ADAPTIVE_ESTIMATE" ? "text-cyan-300 border-cyan-500/40" : "text-gray-500 border-gray-700";
};
export const todayKwh = (m: Metric | null | undefined): number | null => (m && "today" in m && m.today?.status === "PHYSICAL" && Number.isFinite(m.today.kwh) ? m.today.kwh : null);
export const reasonOf = (m: Metric | null | undefined): string | null => (m && "reason" in m ? m.reason : null);
// Formatting never substitutes zero for a missing value.
export const fmtKwh = (v: number | null | undefined, digits = 2) => (v == null || !Number.isFinite(v) ? "UNAVAILABLE" : `${v.toFixed(digits)} kWh`);
export const fmtKw = (v: number | null | undefined) => (v == null || !Number.isFinite(v) ? "—" : `${v.toFixed(2)} kW`);
export const fmtPct = (v: number | null | undefined) => (v == null || !Number.isFinite(v) ? "—" : `${v.toFixed(1)}%`);
export const isAllocationEvent = (level: string) => (level || "").toUpperCase().startsWith(ALLOCATION_EVENT_PREFIX);
export const allocationEventKind = (level: string) => (level || "").toUpperCase().slice(ALLOCATION_EVENT_PREFIX.length) || "EVENT";
export const eventTone = (kind: string) =>
  kind === "BLOCKED" || kind === "FAULT" ? "text-red-300 border-red-500/50" : kind === "PAUSED" || kind === "SKIPPED" ? "text-amber-300 border-amber-500/40"
  : kind === "COMPLETED" || kind === "TARGET_REACHED" || kind === "TRANSITION_VERIFIED" ? "text-emerald-300 border-emerald-500/40" : "text-cyan-300 border-cyan-500/40";
