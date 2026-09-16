import type { ControllerHealth } from "./types";

const object = (v: unknown): Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : {};
const number = (v: unknown, low: number, high: number, integer = false): number | null =>
  typeof v === "number" && Number.isFinite(v) && v >= low && v <= high && (!integer || Number.isInteger(v)) ? v : null;
const timestamp = (v: unknown, now: number): string | null => {
  if (typeof v !== "string" || v.length > 40 || !/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(v)) return null;
  const time = Date.parse(v);
  return Number.isFinite(time) && time <= now + 30000 ? v : null;
};
const choice = (v: unknown, allowed: string[]) => typeof v === "string" && allowed.includes(v) ? v : "UNKNOWN";

// Optional telemetry must never disable operations. Invalid fields are isolated,
// not coerced to numbers or replaced with the legacy placeholder measurements.
export function normalizeControllerHealth(value: unknown, now = Date.now()): ControllerHealth {
  const raw = object(value), valid = raw.version === 1;
  const sampled_at = valid ? timestamp(raw.sampled_at, now) : null;
  const maxAge = number(raw.max_age_seconds, 1, 120, true);
  const known = sampled_at !== null && maxAge !== null && ["CURRENT", "STALE"].includes(String(raw.status));
  const cpu = known ? object(raw.cpu) : {}, boot = known ? object(raw.boot) : {}, wd = known ? object(raw.watchdog) : {};
  const celsius = cpu.source === "LINUX_THERMAL" ? number(cpu.celsius, -40, 150) : null;
  const since = timestamp(boot.tracking_since, now);
  const count = boot.status === "OBSERVED" && since && sampled_at && Date.parse(since) <= Date.parse(sampled_at)
    ? number(boot.count, 1, 2147483647, true) : null;
  return {
    version: 1, sampled_at, max_age_seconds: maxAge ?? 120,
    status: !known ? "UNKNOWN" : raw.status === "STALE" || now - Date.parse(sampled_at!) > maxAge! * 1000 ? "STALE" : "CURRENT",
    cpu: { celsius, source: celsius === null ? "UNKNOWN" : "LINUX_THERMAL" },
    boot: { count, tracking_since: count === null ? null : since, status: count === null ? "UNKNOWN" : "OBSERVED" },
    watchdog: {
      service_timeout_us: number(wd.service_timeout_us, 0, 86400000000, true),
      service_state: choice(wd.service_state, ["ACTIVE", "INACTIVE", "FAILED", "ACTIVATING", "DEACTIVATING", "RELOADING"]),
      hardware_state: choice(wd.hardware_state, ["ACTIVE", "INACTIVE"]),
      hardware_timeout_seconds: number(wd.hardware_timeout_seconds, 0, 86400, true), recovery: "NOT_VERIFIED",
    },
  };
}