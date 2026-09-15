import { ConsumptionScope } from "./types";

export function scopeValid(data: ConsumptionScope): boolean {
  const { included_wings: included, excluded_wings: excluded } = data;
  if (included === undefined && excluded === undefined) return true; // older read API
  if (!Array.isArray(included) || !Array.isArray(excluded)) return false;
  const combined = [...included, ...excluded];
  return combined.length === 4 && new Set(combined).size === 4 && combined.every((w) => ["A", "B", "C", "D"].includes(w));
}