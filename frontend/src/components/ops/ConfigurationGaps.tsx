import type { Device } from "./types";
import type { AllocationConfig, EnergySummary } from "./energy/types";
import { WINGS } from "./energy/types";
import { record } from "./readRequest";

// Snapshot evidence only: no readiness score, new API reads, or inferred faults.
export function ConfigurationGaps({ device, summary, allocation }: { device: Device; summary: EnergySummary | null; allocation: AllocationConfig | null }) {
  const items: { id: string; title: string; detail: string }[] = [];
  if (device.feedback_hardware_installed !== true) items.push({ id: "feedback", title: "Contactor feedback", detail: device.feedback_hardware_installed === false ? "Not installed" : "UNKNOWN" });
  if (summary) {
    if (summary.generation_meter.status === "DISABLED") items.push({ id: "generation", title: "Physical generation · M1", detail: "Meter disabled" });
    const targets = WINGS.filter((w) => device.slots[w]?.disabled !== true && summary.wings[w]?.required_generation?.target_kwh_per_day == null);
    if (targets.length) items.push({ id: "targets", title: "Daily energy targets", detail: `UNAVAILABLE · ${targets.join(", ")}` });
    if (allocation?.enabled === false) items.push({ id: "allocation", title: "Allocation policy", detail: "Disabled in configuration" });
    const report = summary.target_delivery;
    if (!record(report) || report.status !== "REPORTED_CURRENT") items.push({ id: "energy-version", title: "Energy version report", detail: record(report) && report.status === "PENDING" ? "Pending controller report" : record(report) && report.status === "STALE_REPORT" ? "Stale report" : "UNKNOWN" });
  }
  if (!items.length) return null;
  return <section data-testid="configuration-gaps" className="ops-configuration-band" aria-label="Configuration and evidence gaps">
    <div className="flex flex-wrap items-center justify-between gap-2 mb-3"><h2 data-testid="configuration-gaps-title" className="text-sm font-semibold text-amber-200">Configuration & evidence</h2><span data-testid="configuration-gaps-count" className="text-xs text-amber-200">{items.length} reported gaps</span></div>
    <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2 xl:grid-cols-5">
      {items.map((item) => <div key={item.id} className="min-w-0"><dt data-testid={`configuration-gap-${item.id}-title`} className="text-xs text-gray-400">{item.title}</dt><dd data-testid={`configuration-gap-${item.id}-value`} className="mt-1 text-xs font-medium text-amber-200 break-words">{item.detail}</dd></div>)}
    </dl>
  </section>;
}