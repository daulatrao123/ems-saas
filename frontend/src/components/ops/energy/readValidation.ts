import { record, finite, nullableNumber, text } from "../readRequest";
import { EnergySummary, EnergyComparison, MonthlyGeneration, AllocationConfig, ManualEntry, WINGS } from "./types";
import { scopeValid } from "./scopeValidation";

const optionalText = (v: unknown) => v == null || text(v);
const metric = (m: unknown) => record(m) && (m.status === "UNAVAILABLE" && optionalText(m.reason) ||
  ["today", "yesterday", "this_month", "previous_month", "this_year", "lifetime", "reset_period"].every((p) => record(m[p]) && nullableNumber(m[p].kwh) && text(m[p].status)));
export function summaryValid(d: unknown, did: string): d is EnergySummary {
  if (!record(d) || d.device_id !== did || !text(d.as_of_operating_date) || !record(d.calculation)
    || !["AUTO", "MANUAL"].includes(String(d.calculation.mode)) || !finite(d.calculation.version)
    || d.calculation.operating_date !== d.as_of_operating_date || !record(d.wings) || !record(d.generation_meter)) return false;
  const wings = d.wings;
  return [d.generation_meter.source, d.generation_meter.status, d.generation_meter.model, d.generation_meter.serial, d.reset_period].every(optionalText)
    && metric(d.generation_meter.generation) && (d.generation_meter.unattributed == null || metric(d.generation_meter.unattributed))
    && WINGS.every((w) => record(wings[w]) && wings[w].wing === w && record(wings[w].consumption_meter)
      && text(wings[w].consumption_meter.comm_status) && metric(wings[w].generation) && metric(wings[w].consumption)
      && record(wings[w].required_generation) && nullableNumber(wings[w].required_generation.target_kwh_per_day)
      && nullableNumber(wings[w].required_generation.achievement_percent) && optionalText(wings[w].required_generation.status))
    && (d.references == null || referencesValid(d.references));
}
function referencesValid(d: unknown) {
  if (!record(d) || !record(d.wings) || !record(d.grid) || typeof d.grid.enabled !== "boolean" || !finite(d.grid.version)
    || !record(d.allocation) || !Array.isArray(d.allocation.wings)) return false;
  const wings = d.wings;
  return scopeValid(d) && text(d.allocation.reason) && text(d.allocation.status) && text(d.allocation.generation_source)
    && d.allocation.wings.every((w) => record(w) && text(w.wing) && text(w.status)
    && ["required_kwh", "allocated_kwh", "unmet_kwh"].every((k) => nullableNumber(w[k])))
    && WINGS.every((w) => record(wings[w]) && record(wings[w].history) && record(wings[w].effective)
      && nullableNumber(wings[w].history.reference_daily_kwh) && finite(wings[w].history.valid_months)
      && nullableNumber(wings[w].effective.daily_kwh) && text(wings[w].effective.source));
}
function pointValid(r: unknown, mode: string): boolean {
  return record(r) && text(r.date) && /^\d{4}-\d{2}-\d{2}$/.test(r.date)
    && [r.generated_kwh, r.consumed_kwh].every((v) => v === null || (finite(v) && v >= 0))
    && nullableNumber(r.generation_minus_consumption_kwh)
    && text(r.generation_source) && text(r.consumption_source)
    && [r.bill_month, r.generation_reason, r.consumption_reason, r.balance_reason].every(optionalText)
    && (r.missing_consumption_wings === undefined || Array.isArray(r.missing_consumption_wings) && r.missing_consumption_wings.every(text))
    && (r.generated_kwh === null || r.generation_source === "PHYSICAL")
    && (r.consumed_kwh === null || r.consumption_source === (mode === "MANUAL" ? "HISTORICAL" : "PHYSICAL"));
}
export function comparisonValid(d: unknown): d is EnergyComparison {
  if (!record(d) || !record(d.wings) || !record(d.society) || !["AUTO", "MANUAL"].includes(String(d.mode))) return false;
  const wings = d.wings;
  const series = (s: unknown) => record(s) && pointValid(s.today, String(d.mode)) && record(s.today)
    && s.today.date === d.operating_date && Array.isArray(s.rows) && s.rows.length > 0 && s.rows.length <= 30 && s.rows.every((r) => pointValid(r, String(d.mode)));
  return scopeValid(d) && series(d.society) && WINGS.every((w) => record(wings[w]) && wings[w].wing === w && series(wings[w]));
}
export const monthlyValid = (d: unknown): d is MonthlyGeneration => record(d) && d.meter_id === "M1" && Array.isArray(d.rows)
  && d.rows.every((r) => record(r) && text(r.month) && nullableNumber(r.generation_kwh) && text(r.source));
export const allocationValid = (d: unknown): d is AllocationConfig => record(d) && typeof d.enabled === "boolean" && Array.isArray(d.sequence)
  && d.sequence.every((w) => text(w) && WINGS.includes(w as typeof WINGS[number])) && record(d.wings)
  && Object.values(d.wings).every((w) => record(w) && typeof w.generation_attribution_enabled === "boolean");
export const entriesValid = (rows: unknown): rows is ManualEntry[] => Array.isArray(rows) && rows.every((r) => record(r)
  && finite(r.id) && text(r.operating_date) && text(r.kind) && text(r.source) && finite(r.value_kwh) && text(r.reason));