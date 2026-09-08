"use client";
import { useState } from "react";
import { useOperations } from "./useOperations";
import { DashboardHeader, btn, panel, tone } from "./DashboardHeader";
import { StatusStrip } from "./StatusStrip";
import { StoragePanel } from "./StoragePanel";
import { LcdMessagePanel } from "./LcdMessagePanel";
import { SlotCard } from "./SlotCard";
import { SystemControls, ResetDayControl } from "./SystemControls";
import { UnitAllotment } from "./UnitAllotment";
import { LcdControl } from "./LcdControl";
import { LastResponse } from "./LastResponse";
import { OperationalLogs } from "./OperationalLogs";
import { Confirm, ConfirmDialog } from "./ConfirmDialog";
import { SLOT_CODES } from "./types";

// Society → Status → Slots A–D → Days → Controls → Last Response → LCD → Logs. Frontend only: same APIs,
// same command contracts, same tenant checks (backend authoritative). readOnly hides controls for members
// (the backend independently rejects member writes with 403).
export function OperationalDashboard({ societyId, readOnly, backHref }: { societyId: string | null; readOnly: boolean; backHref: string }) {
  const ops = useOperations(societyId);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [deviceIdx, setDeviceIdx] = useState(0);
  if (ops.loading && !ops.dash) return <div data-testid="ops-loading" className="p-10 text-center text-gray-500 font-mono text-sm">LOADING OPERATIONS…</div>;
  if (!ops.dash) return <div data-testid="ops-error" className={`${panel} m-6 p-6 text-red-400 font-mono text-sm`}>{ops.error || "Dashboard unavailable"}</div>;
  const devices = ops.dash.devices; const device = devices[Math.min(deviceIdx, Math.max(0, devices.length - 1))] || null;
  const cmds = device ? ops.commands[device.id] || [] : [];
  const lastRow = cmds[0] || null;
  const lastFor = (code: string) => cmds.find((c) => c.slot === code) || undefined;
  return (
    <div data-testid="operational-dashboard" className="space-y-3">
      <DashboardHeader dash={ops.dash} device={device} backHref={backHref} onRefresh={() => ops.refresh()} readOnly={readOnly} />
      {ops.error && <div data-testid="ops-inline-error" className="border border-red-500/40 bg-red-500/10 px-4 py-2 font-mono text-xs text-red-300">{ops.error}</div>}
      {devices.length > 1 && (
        <div data-testid="device-tabs" className="flex flex-wrap gap-1">
          {devices.map((d, i) => <button key={d.id} data-testid={`device-tab-${d.id}`} onClick={() => setDeviceIdx(i)} className={`${btn} ${i === deviceIdx ? tone.cyan : tone.gray}`}>{d.name} <span className={d.connected ? "text-emerald-400" : "text-red-400"}>●</span></button>)}
        </div>
      )}
      <StatusStrip device={device} resetDay={ops.dash.reset_day} />
      {device && (
        <div className="grid gap-4 lg:grid-cols-2">
          <StoragePanel device={device} />
          {societyId && <LcdMessagePanel societyId={societyId} device={device} readOnly={readOnly} />}
        </div>
      )}
      {device?.hardware_fault && (
        <div data-testid="hardware-fault-banner" className="border border-red-500/60 bg-red-500/10 px-4 py-3 font-mono text-xs text-red-300">
          <b>HARDWARE FAULT (Pi-reported)</b> — {device.hardware_fault}. GPIO layer unavailable: relays are held OFF, controller is in FAULT. Run <code>gpio_input_diag.py</code> on the Pi.
        </div>
      )}
      {!device ? <div className={`${panel} p-8 text-center text-gray-500 font-mono text-sm`}>NO PI DEVICE REGISTERED FOR THIS SOCIETY</div> : (
        <>
          <div className="grid gap-3 xl:grid-cols-[1fr_360px]">
            <div>
              <div className="mb-2 text-[10px] uppercase tracking-[0.14em] text-gray-500">Slots · {device.name} <span className="font-mono text-gray-600">{device.id}</span></div>
              <div data-testid="slot-grid" className="grid gap-3 sm:grid-cols-2">
                {SLOT_CODES.filter((c) => device.slots[c]).map((c) => <SlotCard key={c} device={device} code={c} slot={device.slots[c]} queue={ops.queue} setSlotConfig={ops.setSlotConfig} isPending={ops.isPending} readOnly={readOnly} lastCmd={lastFor(c)} />)}
                {SLOT_CODES.every((c) => !device.slots[c]) && <div className={`${panel} p-6 text-gray-500 text-sm sm:col-span-2`}>No slots configured for this device yet.</div>}
              </div>
            </div>
            <div className="space-y-3">
              <LastResponse last={ops.last} row={lastRow} />
              {!readOnly && <SystemControls deviceId={device.id} queue={ops.queue} isPending={ops.isPending} ask={setConfirm} />}
              {!readOnly && <ResetDayControl deviceId={device.id} current={ops.dash.reset_day} queue={ops.queue} isPending={ops.isPending} ask={setConfirm} />}
            </div>
          </div>
          {!readOnly && (
            <div className="grid gap-3 xl:grid-cols-2">
              <UnitAllotment device={device} queue={ops.queue} isPending={ops.isPending} ask={setConfirm} />
              <LcdControl deviceId={device.id} queue={ops.queue} isPending={ops.isPending} />
            </div>
          )}
          <OperationalLogs commands={cmds} events={ops.events} />
        </>
      )}
      <ConfirmDialog c={confirm} onClose={() => setConfirm(null)} />
    </div>
  );
}
