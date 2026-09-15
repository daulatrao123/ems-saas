"use client";
import { Device, SLOT_CODES, ago, fmtDateTime, fmtUptime, nextResetDate } from "./types";
import { label, panel } from "./DashboardHeader";
import { healthFreshness, WatchdogHealth } from "./WatchdogHealth";

function Stat({ id, name, value, sub, tone }: { id: string; name: string; value: string; sub?: string; tone?: string }) {
  return (
    <div data-testid={`stat-${id}`} className="px-4 py-3 border-r border-[#1e2a3a] last:border-r-0 min-w-0 break-words">
      <div className={label}>{name}</div>
      <div data-testid={`stat-${id}-value`} className={`mt-1 font-mono text-lg font-bold leading-tight ${tone || "text-white"}`}>{value}</div>
      {sub && <div data-testid={`stat-${id}-detail`} className="mt-1 text-[10px] text-gray-500">{sub}</div>}
    </div>
  );
}

// Legacy placeholders are never promoted to measured/observed health.
export function StatusStrip({ device, resetDay }: { device: Device | null; resetDay: number }) {
  const slots = device ? SLOT_CODES.filter((c) => device.slots[c]) : [];
  const enabled = slots.filter((c) => !device!.slots[c].disabled).length;
  const t = device?.telemetry;
  const health = t?.health, freshness = healthFreshness(health, device?.connected === true);
  const measured = health?.cpu.celsius, count = health?.boot.count;
  const cpu = measured == null ? "UNKNOWN" : freshness !== "CURRENT" ? freshness : `${measured.toFixed(1)}°C`;
  const up = fmtUptime(t?.uptime_seconds) || "N/A";
  const boots = count == null ? "UNKNOWN" : freshness !== "CURRENT" ? freshness : String(count);
  return (
    <div data-testid="status-strip" className={panel}>
    <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 divide-y sm:divide-y-0 divide-[#1e2a3a]">
      <Stat id="active-slot" name="Active Slot" value={device?.active_slot || "—"} sub={device?.active_slot ? device.slots[device.active_slot]?.display_name : "none active"} tone="text-cyan-300" />
      <Stat id="slots" name="Slots" value={slots.length ? `${enabled} / ${slots.length}` : "—"} sub="enabled / configured" />
      <Stat id="reset-day" name="Reset Day" value={String(resetDay)} sub="day of month" />
      <Stat id="next-reset" name="Next Reset" value={nextResetDate(resetDay)} sub="derived from reset day" />
      <Stat id="cpu" name="CPU Temp" value={cpu} sub={health?.sampled_at ? `${freshness === "STALE" && measured != null ? `Last ${measured.toFixed(1)}°C · ` : ""}Sampled ${fmtDateTime(health.sampled_at)}` : "Measurement unavailable"} tone={freshness !== "CURRENT" || measured == null ? "text-gray-500" : undefined} />
      <Stat id="uptime" name="Uptime" value={up} tone={up === "N/A" ? "text-gray-500" : undefined} />
      <Stat id="boots" name="Boots since tracking began" value={boots} sub={health?.boot.tracking_since ? `${freshness === "STALE" && count != null ? `Last ${count} · ` : ""}Since ${fmtDateTime(health.boot.tracking_since)}` : "Observed OS boots · unknown"} tone={freshness !== "CURRENT" || count == null ? "text-gray-500" : undefined} />
      <Stat id="last-sync" name="Last Sync" value={device?.last_sync ? ago(device.last_sync) : "never"} sub={`${fmtDateTime(device?.last_sync)}${device?.config_state ? ` · config ${device.config_state}` : ""}`} tone={device?.connected ? "text-emerald-400" : "text-red-400"} />
    </div>
    <WatchdogHealth health={health} connected={device?.connected === true} />
    </div>
  );
}
