"use client";
import { ControllerHealth, fmtDateTime } from "./types";
import { label } from "./DashboardHeader";
import { normalizeControllerHealth } from "./healthValidation";

export function healthFreshness(value: unknown, connected: boolean) {
  const health = normalizeControllerHealth(value);
  if (!health?.sampled_at || health.status === "UNKNOWN") return "UNKNOWN";
  const age = (Date.now() - Date.parse(health.sampled_at)) / 1000;
  if (!Number.isFinite(age) || age < -30) return "UNKNOWN";
  return !connected || health.status === "STALE" || age > health.max_age_seconds ? "STALE" : "CURRENT";
}

export function WatchdogHealth({ health: value, connected }: { health?: ControllerHealth; connected: boolean }) {
  const health = normalizeControllerHealth(value);
  const freshness = healthFreshness(health, connected), wd = health?.watchdog;
  const timeout = wd?.service_timeout_us;
  const configured = freshness !== "CURRENT" ? freshness : timeout == null ? "UNKNOWN" : timeout > 0 ? "CONFIGURED" : "NOT CONFIGURED";
  const hardware = freshness !== "CURRENT" ? freshness : wd?.hardware_state || "UNKNOWN";
  return <div data-testid="watchdog-health" className="grid grid-cols-1 sm:grid-cols-3 border-t border-[#1e2a3a]">
    <div data-testid="watchdog-service" className="min-w-0 px-4 py-3 space-y-1 border-b sm:border-b-0 sm:border-r border-[#1e2a3a]">
      <div className={label}>Controller service watchdog</div>
      <div data-testid="watchdog-service-status" className="font-mono text-sm text-gray-300 break-words">{configured}</div>
      <div data-testid="watchdog-service-evidence" className="text-[10px] text-gray-500 break-words">{freshness === "CURRENT" && wd ? `Service: ${wd.service_state}${timeout != null ? ` · timeout ${timeout / 1000000}s` : ""}` : "Current service evidence unavailable"}</div>
    </div>
    <div data-testid="watchdog-hardware" className="min-w-0 px-4 py-3 space-y-1 border-b sm:border-b-0 sm:border-r border-[#1e2a3a]">
      <div className={label}>Kernel watchdog evidence</div>
      <div data-testid="watchdog-hardware-status" className="font-mono text-sm text-gray-300 break-words">{hardware}</div>
      <div data-testid="watchdog-sampled-at" className="text-[10px] text-gray-500 break-words">{health?.sampled_at ? `Sampled ${fmtDateTime(health.sampled_at)}` : "Sample time unknown"}</div>
    </div>
    <div data-testid="watchdog-recovery" className="min-w-0 px-4 py-3 space-y-1">
      <div className={label}>Watchdog recovery verification</div>
      <div data-testid="watchdog-recovery-status" className="font-mono text-sm text-amber-300">NOT VERIFIED</div>
      <div data-testid="watchdog-recovery-evidence" className="text-[10px] text-gray-500">No recovery-test evidence recorded</div>
    </div>
  </div>;
}