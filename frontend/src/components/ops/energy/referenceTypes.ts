import { WingCode } from "./types";

export type BillMonth = { month: string; month_name: string; days: number; consumption_kwh: number | null; daily_kwh: number | null; source: string; updated_at: string | null };
export type BillHistory = { device_id: string; wing: WingCode; end_month: string; months: BillMonth[]; reference_daily_kwh: number | null; valid_months: number; source: string; formula: string };
export type WingReference = { history: BillHistory; effective: { daily_kwh: number | null; source: string; operating_date: string | null } };
export type GridReference = { enabled: boolean; limit_kwh_day: number | null; version: number };
export type AllocationReference = { generation_kwh: number | null; generation_source: string; required_kwh: number | null; excess_kwh: number | null; unmet_kwh: number | null; grid_allocation_kwh: number; unassigned_excess_kwh: number | null; all_quotas_known: boolean; all_quotas_satisfied: boolean; status: string; reason: string;
  wings: { wing: WingCode; required_kwh: number | null; allocated_kwh: number | null; unmet_kwh: number | null; status: string }[] };
export type EnergyReferenceData = { wings: Record<WingCode, WingReference>; society_historical_daily_kwh: number | null; society_reference_daily_kwh: number | null; society_reference_source: string; grid: GridReference; allocation: AllocationReference };
export const dailyRate = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? "UNAVAILABLE" : `${value.toFixed(2)} kWh/day`;
export const referenceSource = (source: string) => source === "HISTORICAL" ? "Historical baseline" : source === "PHYSICAL" ? "Physical · completed day" : source === "MIXED_REFERENCE" ? "Physical + historical references" : "UNAVAILABLE";