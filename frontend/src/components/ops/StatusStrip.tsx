"use client";
import { Device, SLOT_CODES, ago, fmtUptime, nextResetDate } from "./types";
import { label, panel } from "./DashboardHeader";

function Stat({ id, name, value, sub, tone }: { id: string; name: string; value: string; sub?: string; tone?: string }) {
  return (
    <div data-testid={`stat-${id}`} className="px-4 py-3 border-r border-[#1e2a3a] last:border-r-0 min-w-[120px]">
      <div className={label}>{name}</div>
      <div className={`mt-1 font-mono text-lg font-bold leading-none ${tone || "text-white"}`}>{value}</div>
      {sub && <div className="mt-1 text-[10px] text-gray-500">{sub}</div>}
    </div>
  );
}

// Only real values: telemetry that was never reported (null) or reported as 0 by a Pi is shown as N/A.
export function StatusStrip({ device, resetDay }: { device: Device | null; resetDay: number }) {
  const slots = device ? SLOT_CODES.filter((c) => device.slots[c]) : [];
  const enabled = slots.filter((c) => !device!.slots[c].disabled).length;
  const t = device?.telemetry;
  const cpu = t?.cpu_temp != null && t.cpu_temp > 0 ? `${t.cpu_temp.toFixed(1)}°C` : "N/A";
  const up = fmtUptime(t?.uptime_seconds) || "N/A";
  const boots = device?.last_sync && t?.boot_count != null ? String(t.boot_count) : "N/A";
  return (
    <div data-testid="status-strip" className={`${panel} grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 divide-y sm:divide-y-0 divide-[#1e2a3a]`}>
      <Stat id="active-slot" name="Active Slot" value={device?.active_slot || "—"} sub={device?.active_slot ? device.slots[device.active_slot]?.display_name : "none active"} tone="text-cyan-300" />
      <Stat id="slots" name="Slots" value={slots.length ? `${enabled} / ${slots.length}` : "—"} sub="enabled / configured" />
      <Stat id="reset-day" name="Reset Day" value={String(resetDay)} sub="day of month" />
      <Stat id="next-reset" name="Next Reset" value={nextResetDate(resetDay)} sub="derived from reset day" />
      <Stat id="cpu" name="CPU Temp" value={cpu} tone={cpu === "N/A" ? "text-gray-500" : undefined} />
      <Stat id="uptime" name="Uptime" value={up} tone={up === "N/A" ? "text-gray-500" : undefined} />
      <Stat id="boots" name="Boots" value={boots} tone={boots === "N/A" ? "text-gray-500" : undefined} />
      <Stat id="last-sync" name="Last Sync" value={device?.last_sync ? ago(device.last_sync) : "never"} sub={device?.config_state ? `config ${device.config_state}` : undefined} tone={device?.connected ? "text-emerald-400" : "text-red-400"} />
    </div>
  );
}
