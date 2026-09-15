import { ComparisonPoint } from "./types";

export const monthLabel = (month: string) => /^\d{4}-\d{2}$/.test(month) ? new Date(`${month}-01T00:00:00Z`).toLocaleDateString("en-IN", { month: "long", year: "numeric", timeZone: "UTC" }) : "Unknown month";
export function missingReason(point: ComparisonPoint, field: "generation" | "consumption" | "balance") {
  const reason = point[`${field}_reason`];
  if (reason === "BILL_NOT_ENTERED_FOR_MONTH") return `No bill saved for ${monthLabel(point.date.slice(0, 7))}`;
  if (reason === "M1_DISABLED") return "M1 is disabled; physical generation unavailable";
  if (reason === "CONSUMPTION_METER_UNAVAILABLE") return "Qualified consumption-meter reading unavailable";
  if (reason === "INCOMPLETE_WING_CONSUMPTION") return `Consumption unavailable for Wing ${(point.missing_consumption_wings || []).join(", ")}`;
  if (reason === "PHYSICAL_GENERATION_UNAVAILABLE") return "No qualified physical generation reading for this date";
  return field === "balance" ? "Generation and consumption are both required" : `${field === "generation" ? "Physical generation" : "Consumption"} unavailable for this date`;
}