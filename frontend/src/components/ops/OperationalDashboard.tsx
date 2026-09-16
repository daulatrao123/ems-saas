"use client";
import { ReactNode, useState } from "react";
import { useOperations } from "./useOperations";
import { DashboardHeader, btn, panel, tone } from "./DashboardHeader";
import { StatusStrip } from "./StatusStrip";
import { StoragePanel } from "./StoragePanel";
import { LcdMessagePanel } from "./LcdMessagePanel";
import { SlotCard, SlotOperations } from "./SlotCard";
import { SystemControls, ResetDayControl } from "./SystemControls";
import { UnitAllotment } from "./UnitAllotment";
import { LastResponse } from "./LastResponse";
import { OperationalLogs } from "./OperationalLogs";
import { Confirm, ConfirmDialog } from "./ConfirmDialog";
import { EnergyPanel } from "./energy/EnergyPanel";
import { useEnergy } from "./energy/useEnergy";
import { WINGS } from "./energy/types";
import { SLOT_CODES } from "./types";
import { DashboardNavigation, SectionHeading } from "./DashboardSections";
import { ConfigurationGaps } from "./ConfigurationGaps";

// Society → Status → Slots A–D → Days → Controls → Last Response → LCD → Logs. Frontend only: same APIs,
// same command contracts, same tenant checks (backend authoritative). readOnly hides controls for members
// (the backend independently rejects member writes with 403).
export function OperationalDashboard({ societyId, readOnly, backHref }: { societyId: string | null; readOnly: boolean; backHref: string }) {
  const ops = useOperations(societyId);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [selectedDeviceId, setSelectedDeviceId] = useState<string | null>(null);
  const devices = ops.dash?.devices || [];
  const device = devices.find((d) => d.id === selectedDeviceId) || devices[0] || null;
  const energy = useEnergy(societyId, device?.id || null);
  const mode = energy.summary?.calculation?.mode;
  const reportedWing = energy.summary?.generation_meter?.active_generation_wing;
  const activeGenerationWing = device?.connected && device.feedback_hardware_installed === true && !device.hardware_fault
    && reportedWing && device.slots[reportedWing]?.feedback_enabled === true ? reportedWing : undefined;
  if (ops.loading && !ops.dash) return <div data-testid="ops-loading" className="p-10 text-center text-gray-500 font-mono text-sm">LOADING OPERATIONS…</div>;
  if (!ops.dash) return <div data-testid="ops-error" className={`${panel} m-6 p-6 text-red-400 font-mono text-sm`}>{ops.error || "Dashboard unavailable"}<button data-testid="ops-retry" className={`${btn} ${tone.gray} ml-3`} onClick={() => void ops.refresh()}>RETRY</button></div>;
  const cmds = device ? ops.commands[device.id] || [] : [];
  const lastRow = cmds.find((c) => !SLOT_CODES.includes(c.slot)) || null;
  const last = ops.last?.device_id === device?.id ? ops.last : null;
  const lastFor = (code: string) => cmds.find((c) => c.slot === code) || undefined;
  const renderWings = () => device && (
    <div data-testid="slot-grid" className="grid items-start gap-4 md:grid-cols-2 xl:grid-cols-4">
      {WINGS.map((c) => <SlotCard key={`${device.id}:${c}:${mode}`} device={device} code={c} slot={device.slots[c]} queue={ops.queue} setSlotConfig={async (...args) => { const saved = await ops.setSlotConfig(...args); if (saved) void energy.refresh(); return saved; }} isPending={ops.isPending} readOnly={readOnly}
        lastCmd={lastFor(c)} lastResponse={last?.slot === c ? last : null} wing={energy.summary?.wings?.[c]} allocation={energy.allocation}
        mode={mode} comparison={energy.comparison?.wings[c]?.wing === c ? energy.comparison.wings[c] : undefined} activeGenerationWing={activeGenerationWing}
        excessEnabled={energy.summary?.references?.grid.enabled === true} />)}
    </div>
  );
  const renderDayControls = (inputs: Record<string, ReactNode> = {}) => device && (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
      {WINGS.map((c) => <SlotOperations key={`${device.id}:${c}`} device={device} code={c} slot={device.slots[c]} queue={ops.queue} isPending={ops.isPending} readOnly={readOnly}
        lastCmd={lastFor(c)} lastResponse={last?.slot === c ? last : null} allotmentInput={inputs[c]} />)}
    </div>
  );
  return (
    <div data-testid="operational-dashboard" className="ems-dashboard min-w-0 [&_input]:min-w-0 [&_input]:max-w-full">
      <DashboardHeader dash={ops.dash} device={device} backHref={backHref} onRefresh={() => { void ops.refresh(); void energy.refresh(); }} readOnly={readOnly} />
      <DashboardNavigation readOnly={readOnly} hasDevice={!!device} />
      {ops.error && <div data-testid="ops-inline-error" className="border border-red-500/40 bg-red-500/10 px-4 py-2 font-mono text-xs text-red-300">{ops.error}</div>}
      {ops.panelErrors.length > 0 && <div data-testid="ops-history-errors" role="alert" className="text-xs text-amber-300">{ops.panelErrors.join(" · ")}</div>}
      {devices.length > 1 && (
        <div data-testid="device-tabs" className="flex flex-wrap gap-2 mt-5">
          {devices.map((d) => <button key={d.id} data-testid={`device-tab-${d.id}`} onClick={() => setSelectedDeviceId(d.id)} className={`${btn} ${d.id === device?.id ? tone.cyan : tone.gray}`}>{d.name} <span className={d.connected ? "text-emerald-400" : "text-red-400"}>●</span></button>)}
        </div>
      )}
      {device?.hardware_fault && (
        <div data-testid="hardware-fault-banner" role="alert" className="mt-5 border border-red-500/60 bg-red-500/10 px-4 py-3 font-mono text-xs text-red-300 break-words">
          <b>HARDWARE FAULT (Pi-reported)</b> — {device.hardware_fault}
        </div>
      )}
      <section id="ops-overview" data-testid="dashboard-overview" className="ops-section space-y-4">
        <SectionHeading id="overview" number="01" title="Controller overview" context="Pi-reported snapshot" />
        <StatusStrip device={device} resetDay={ops.dash.reset_day} />
        {device && <ConfigurationGaps device={device} summary={energy.summary} allocation={energy.allocation} />}
        {device && <details data-testid="controller-diagnostics" className="ops-disclosure">
          <summary data-testid="controller-diagnostics-toggle" className="text-sm font-medium"><span>Storage & LCD{readOnly ? " diagnostics" : " messaging"}</span>
            <span data-testid="controller-storage-summary" className={`ml-auto text-xs ${device.storage_health?.health === "FAILED" ? "text-red-300" : device.storage_health?.health === "WARNING" ? "text-amber-200" : "text-gray-400"}`}>Storage: {device.storage_health?.health || "UNAVAILABLE"}{device.storage_health?.smart === "UNAVAILABLE" ? " · SMART UNAVAILABLE" : ""}</span>
          </summary>
          <div className={`grid gap-5 pb-5 ${readOnly ? "" : "lg:grid-cols-2"}`}>
            <StoragePanel device={device} />
            {societyId && <LcdMessagePanel key={`${societyId}:${device.id}`} societyId={societyId} device={device} readOnly={readOnly} />}
          </div>
        </details>}
      </section>
      {!device ? <div className={`${panel} p-8 text-center text-gray-500 font-mono text-sm`}>NO PI DEVICE REGISTERED FOR THIS SOCIETY</div> : (
        <>
          <div data-testid="ops-device-identity" className="mt-5 text-xs text-gray-400 break-words">{device.name} · <span className="font-mono">{device.id}</span></div>
          {societyId && <EnergyPanel societyId={societyId} societyName={ops.dash.society.name} controllerName={device.name} energy={energy} readOnly={readOnly} activeGenerationWing={activeGenerationWing}>{renderWings()}</EnergyPanel>}
          <section id="ops-controls" data-testid="dashboard-controls" className="ops-section space-y-5">
            <SectionHeading id="controls" number="04" title={readOnly ? "Days & command evidence" : "Controller controls"} context={readOnly ? "Read only" : "Operator actions"} />
          <details data-testid="days-command-details" className="ops-disclosure">
            <summary data-testid="days-command-details-toggle" className="text-sm font-medium">Days, unit allotment & wing commands</summary>
            {readOnly && renderDayControls()}
            {!readOnly && <UnitAllotment key={device.id} device={device} queue={ops.queue} isPending={ops.isPending} ask={setConfirm}>{renderDayControls}</UnitAllotment>}
          </details>
          <div className={`grid gap-3 ${readOnly ? "" : "md:grid-cols-2 xl:grid-cols-3"}`}>
              <LastResponse last={last && !SLOT_CODES.includes(last.slot) ? last : null} row={lastRow} />
              {!readOnly && <SystemControls deviceId={device.id} queue={ops.queue} isPending={ops.isPending} ask={setConfirm} />}
              {!readOnly && <ResetDayControl deviceId={device.id} current={ops.dash.reset_day} queue={ops.queue} isPending={ops.isPending} ask={setConfirm} />}
          </div>
          </section>
          <section id="ops-history" data-testid="dashboard-history" className="ops-section pb-6">
          <SectionHeading id="history" number="05" title="Command & event history" context="Recorded events" />
          <OperationalLogs commands={cmds} events={ops.events} />
          </section>
        </>
      )}
      <ConfirmDialog c={confirm} onClose={() => setConfirm(null)} />
    </div>
  );
}
