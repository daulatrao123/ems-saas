"use client";
import Link from "next/link";
import { useCallback, useState } from "react";
import { useRead } from "./hooks";
import { SetupForms } from "./SetupForms";
import { Alert, SelectField } from "./controls";
import { Dashboard } from "../ops/types";
import { dashboardValid } from "../ops/operationsValidation";

export const EnergySetup = ({ societyId, initialDeviceId }: { societyId: string; initialDeviceId: string }) => {
  const validate = useCallback((d: Dashboard) => dashboardValid(d, societyId), [societyId]);
  const read = useRead<Dashboard>(`/api/admin/dashboard?society_id=${encodeURIComponent(societyId)}`, validate);
  const [did, setDid] = useState(initialDeviceId), [locked, setLocked] = useState(false);
  const device = read.data?.devices.find(d => d.id === did);
  return <div data-testid="energy-setup-page" className="ems-dashboard mx-auto min-w-0 max-w-[1440px] pb-12">
    <header className="ops-page-header"><Link data-testid="setup-back" href={`/society/${societyId}`} onClick={e => { if (locked && !window.confirm("Leave with unsaved changes or a pending save?")) e.preventDefault(); }} className="text-xs text-gray-400 hover:text-white">← Back to operations</Link>
      <p data-testid="setup-society" className="mb-2 mt-6 text-sm text-cyan-200">{read.data?.society.name || "Society"}</p><h1 data-testid="setup-heading" className="text-4xl font-semibold text-white sm:text-5xl lg:text-6xl">Energy setup</h1><p data-testid="setup-context" className="mt-4 text-sm text-gray-400">Physical M1 · consumption references · versioned targets</p></header>
    {read.error && <Alert id="setup-society-error">{read.error}<button data-testid="setup-society-retry" type="button" onClick={read.refresh} className="ml-2 underline">Retry</button></Alert>}
    {read.loading && <p data-testid="setup-society-loading" className="py-5 text-sm text-gray-500">Loading society…</p>}
    {read.data && <div className="mt-6 flex flex-wrap items-end justify-between gap-5"><div className="w-full min-w-0 max-w-lg"><SelectField id="setup-device-select" label="Registered controller" disabled={locked} value={did} onChange={e => { setDid(e.target.value); window.history.replaceState(null, "", `/society/${societyId}/energy-setup/${e.target.value}`); }}>
      {!device && <option value={did}>Select a registered controller</option>}{read.data.devices.map(d => <option key={d.id} value={d.id}>{d.name || "Controller"} · {d.id}</option>)}</SelectField></div><span data-testid="setup-device-link" className="text-xs text-gray-400">Link: {device ? device.connected ? "ONLINE" : "OFFLINE" : "UNAVAILABLE"}</span></div>}
    {read.data && !device && <Alert id="setup-no-device">This controller is not registered in this society. Select an existing controller.</Alert>}
    {device && <SetupForms key={`${societyId}:${did}`} sid={societyId} did={did} onLocked={setLocked} />}
  </div>;
};