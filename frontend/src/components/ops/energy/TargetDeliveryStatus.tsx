import { finite, record, text } from "../readRequest";

export function TargetDeliveryStatus({ delivery }: { delivery: unknown }) {
  if (!record(delivery)) return null;
  const version = (v: unknown) => finite(v) && Number.isInteger(v) && v >= 0 ? `#${v}` : "UNKNOWN";
  const states: Record<string, string> = { UNKNOWN: "Awaiting controller report", PENDING: "Pending controller version", STALE_REPORT: "Stale controller report", REPORTED_CURRENT: "Controller reports current version" };
  const status = text(delivery.status) ? states[delivery.status] || "Report unavailable" : "Report unavailable";
  return <div data-testid="energy-target-delivery" className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-400 break-words">
    <span data-testid="energy-target-delivery-desired">Energy configuration · desired {version(delivery.desired_version)}</span>
    <span data-testid="energy-target-delivery-reported">Pi reported {version(delivery.reported_version)}</span>
    <span data-testid="energy-target-delivery-status">{status}</span>
    {text(delivery.reported_at) && <span data-testid="energy-target-delivery-time">Last report: {delivery.reported_at}</span>}
  </div>;
}