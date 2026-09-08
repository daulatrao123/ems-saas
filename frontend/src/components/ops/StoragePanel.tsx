"use client";
import { Fragment } from "react";
import { Device, StorageHealth } from "./types";
import { label, panel } from "./DashboardHeader";

const gb = (b?: number | null) => (b == null ? "N/A" : `${(b / 1e9).toFixed(1)} GB`);
const toneFor = (h?: string) => (h === "GOOD" ? "text-emerald-300" : h === "WARNING" ? "text-amber-300" : h === "FAILED" ? "text-red-300" : "text-gray-400");
const TYPE_LABEL: Record<string, string> = { "usb-removable": "USB / PEN DRIVE", "scsi-fixed": "USB / SCSI DISK", "sd-emmc": "SD / eMMC", nvme: "NVMe", virtual: "VIRTUAL" };

// Read-only Pi telemetry. Health is only GOOD with positive SMART evidence; UNKNOWN is never shown green.
export function StoragePanel({ device }: { device: Device }) {
  const s: StorageHealth | null | undefined = device.storage_health;
  const health = s?.health || "UNAVAILABLE";
  const rows: [string, string, string?][] = s
    ? [
        ["Device", `${s.device || "N/A"}${s.device_type ? ` (${TYPE_LABEL[s.device_type] || s.device_type})` : ""}`],
        ["Mounted", s.mounted ? `YES · ${s.mount_point || ""}` : "NO", s.mounted ? "text-emerald-300" : "text-red-300"],
        ["Filesystem", s.filesystem || "N/A"],
        ["Capacity", gb(s.total_bytes)],
        ["Used", `${gb(s.used_bytes)}${s.used_percent != null ? ` · ${s.used_percent.toFixed(1)}%` : ""}`, s.used_percent != null && s.used_percent >= 90 ? "text-amber-300" : undefined],
        ["Free", gb(s.free_bytes)],
        ["Write test", s.writable ? "PASSED" : "FAILED", s.writable ? "text-emerald-300" : "text-red-300"],
        ["SMART", s.smart || "UNAVAILABLE", s.smart === "PASSED" ? "text-emerald-300" : s.smart === "UNAVAILABLE" ? "text-gray-400" : "text-amber-300"],
      ]
    : [];
  return (
    <section data-testid="storage-panel" className={`${panel} p-4`}>
      <div className="flex items-center justify-between">
        <span className={label}>Storage / data volume</span>
        <span data-testid="storage-health" className={`font-mono text-xs font-bold ${toneFor(health)}`}>STATUS: {health}</span>
      </div>
      {!s ? (
        <p className="mt-3 font-mono text-xs text-gray-500">No storage telemetry reported yet.</p>
      ) : (
        <dl className="mt-3 grid grid-cols-[110px_1fr] gap-y-1 font-mono text-xs">
          {rows.map(([k, v, t]) => (
            <Fragment key={k}>
              <dt className="text-gray-500">{k}</dt>
              <dd data-testid={`storage-${k.toLowerCase().replace(/[^a-z]+/g, "-")}`} className={t || "text-gray-200"}>{v}</dd>
            </Fragment>
          ))}
          {s.error && (<><dt className="text-gray-500">Error</dt><dd data-testid="storage-error" className="text-red-300">{s.error}</dd></>)}
        </dl>
      )}
      {s && !s.smart_available && <p className="mt-2 text-[10px] text-gray-500">SMART unavailable on this device/controller — health cannot be GOOD without it.</p>}
    </section>
  );
}
