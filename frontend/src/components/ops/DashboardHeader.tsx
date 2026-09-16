"use client";
import Link from "next/link";
import { Dashboard, Device } from "./types";

export const panel = "ops-surface border border-[#1e2a3a] bg-[#0f1520]";
export const label = "ops-label text-[10px] uppercase text-gray-500";
export const btn = "ops-button px-3 py-2 text-[11px] font-bold border transition-colors disabled:opacity-40 disabled:cursor-not-allowed";
export const tone = {
  cyan: "bg-cyan-500/10 text-cyan-300 border-cyan-500/40 hover:bg-cyan-500/20",
  amber: "bg-amber-500/10 text-amber-300 border-amber-500/40 hover:bg-amber-500/20",
  red: "bg-red-500/10 text-red-300 border-red-500/50 hover:bg-red-500/20",
  gray: "bg-[#131b29] text-gray-300 border-[#2a3646] hover:bg-[#1a2333]",
};
export const input = "bg-[#0a0e17] border border-[#2a3646] px-2 py-1.5 text-sm font-mono text-gray-100 focus:border-cyan-500 focus:outline-none disabled:opacity-40";

export function Dot({ on, warn }: { on: boolean; warn?: boolean }) {
  return <span className={`inline-block h-2 w-2 rounded-full ${on ? "bg-emerald-400 shadow-[0_0_6px_#34d399]" : warn ? "bg-amber-400" : "bg-red-500"}`} />;
}

export function DashboardHeader({ dash, device, backHref, onRefresh, readOnly }: { dash: Dashboard; device: Device | null; backHref: string; onRefresh: () => void; readOnly: boolean }) {
  const s = dash.society;
  return (
    <header data-testid="ops-header" className="ops-page-header flex flex-wrap items-end justify-between gap-6">
      <div className="flex flex-col gap-4 min-w-0">
        <Link href={backHref} data-testid="ops-back" className="w-fit text-xs font-medium text-gray-400 hover:text-white">← Back to societies</Link>
        <div className="min-w-0">
          <h1 data-testid="ops-society-name" className="text-4xl sm:text-5xl lg:text-6xl font-semibold text-white leading-tight break-words">{s.name}</h1>
          <div data-testid="ops-society-details" className="mt-3 text-xs text-gray-400 break-words">
            {s.location || "—"} · {s.plan || "—"} · CODE {s.society_code || "—"}
            {s.status && s.status.toLowerCase() !== "active" && <span className="ml-2 text-red-400">{s.status}</span>}
            {readOnly && <span className="ml-2 text-amber-300">READ-ONLY</span>}
          </div>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3 font-mono text-xs">
        {device ? (
          <>
            <span data-testid="ops-online" className={`flex items-center gap-2 font-bold ${device.connected ? "text-emerald-400" : "text-red-400"}`}><Dot on={device.connected} />LINK {device.connected ? "ONLINE" : "OFFLINE"}</span>
            <span data-testid="ops-firmware" className="text-gray-300">FW <b>{device.firmware_version || "N/A"}</b></span>
          </>
        ) : <span className="text-gray-500">NO DEVICE</span>}
        <button data-testid="ops-refresh" onClick={onRefresh} className={`${btn} ${tone.gray}`}><span aria-hidden="true" className="mr-2">↻</span>REFRESH</button>
      </div>
    </header>
  );
}
