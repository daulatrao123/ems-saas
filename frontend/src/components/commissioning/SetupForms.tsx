"use client";
import { useCallback, useEffect, useState } from "react";
import { Setup, setupValid } from "./types";
import { useRead, useSave } from "./hooks";
import { Alert } from "./controls";
import { BusForm } from "./BusForm";
import { MeterForm } from "./MeterForm";
import { ModeForm } from "./ModeForm";
import { TargetForm } from "./TargetForm";
import { btn, tone } from "../ops/DashboardHeader";

export function SetupForms({ sid, did, onLocked }: { sid: string; did: string; onLocked: (v: boolean) => void }) {
  const read = useRead<Setup>(`/api/energy/commissioning?society_id=${encodeURIComponent(sid)}&device_id=${encodeURIComponent(did)}`, setupValid);
  const action = useSave(sid, did, read.refresh);
  const [dirty, setDirty] = useState<string[]>([]), [wing, setWing] = useState("A");
  const mark = useCallback((name: string, value: boolean) => setDirty(old => value ? old.includes(name) ? old : [...old, name] : old.includes(name) ? old.filter(x => x !== name) : old), []);
  const locked = action.busy || dirty.length > 0;
  useEffect(() => { onLocked(locked); return () => onLocked(false); }, [locked, onLocked]);
  useEffect(() => {
    if (!locked) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn);
  }, [locked]);
  const data = read.data?.device_id === did ? read.data : undefined;
  const disabled = (name: string) => action.busy || read.loading || !!read.error || !!data?.allocation_enabled || dirty.some(n => n !== name);
  return <div data-testid="setup-workspace">
    <div className="my-6 flex flex-wrap items-center justify-between gap-4"><p data-testid="setup-write-state" className="text-xs text-gray-400">{action.busy ? "Saving…" : dirty.length ? "One draft open · save or discard before changing sections" : "Changes require an explicit save"}</p><button data-testid="setup-refresh" disabled={locked || read.loading} className={`${btn} ${tone.gray}`} onClick={read.refresh}>↻ Refresh evidence</button></div>
    {read.error && <Alert id="setup-load-error">Configuration unavailable: {read.error}<button data-testid="setup-retry" type="button" className="ml-2 underline" disabled={locked} onClick={read.refresh}>Retry</button></Alert>}
    {read.loading && <p data-testid="setup-loading" className="my-4 text-sm text-gray-500">Loading configuration…</p>}
    {action.error && <Alert id="setup-save-error">{action.error}</Alert>}
    {action.notice && <p data-testid="setup-save-success" role="status" className="my-4 border-l-2 border-cyan-500 bg-cyan-500/5 p-3 text-sm text-cyan-200">{action.notice}</p>}
    {data && <><div data-testid="setup-delivery" className="grid grid-cols-1 gap-5 border-y border-[#2a3646] bg-[#0f1822] px-5 py-5 sm:grid-cols-3">
      <div><p className="text-xs text-gray-500">Desired energy version</p><p data-testid="setup-desired-version" className="mt-2 font-mono text-xl text-white">#{data.config_version}</p></div>
      <div><p className="text-xs text-gray-500">Pi-reported version</p><p data-testid="setup-reported-version" className="mt-2 font-mono text-xl text-white">{data.delivery.reported_version == null ? "UNKNOWN" : `#${data.delivery.reported_version}`}</p></div>
      <div><p data-testid="setup-delivery-status" className={`text-sm ${data.delivery.status === "REPORTED_CURRENT" ? "text-cyan-200" : "text-amber-200"}`}>{data.delivery.status.replaceAll("_", " ")}</p><p data-testid="setup-report-time" className="mt-2 break-words text-xs text-gray-400">Received: {data.delivery.reported_at || "UNKNOWN"}</p></div>
      <p data-testid="setup-evidence-note" className="text-xs text-gray-500 sm:col-span-3">Snapshot evidence only · version agreement is not physical hardware verification.</p></div>
      <p data-testid="setup-allocation-state" className="my-4 text-xs text-gray-400">Automatic allocation: {data.allocation_enabled ? "ENABLED · setup writes locked" : "DISABLED · unchanged by commissioning"}</p>
      {data.allocation_enabled && <Alert id="setup-allocation-block">Pause automatic allocation through the approved operating procedure before changing hardware settings.</Alert>}
      <div className="grid min-w-0 grid-cols-1 items-start gap-x-10 xl:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]"><div className="min-w-0"><BusForm setup={data} save={action.save} disabled={disabled("bus")} mark={mark} /><MeterForm setup={data} save={action.save} disabled={disabled("m1")} mark={mark} /></div><div className="min-w-0"><ModeForm setup={data} save={action.save} disabled={disabled("mode")} mark={mark} /><TargetForm sid={sid} did={did} wing={wing} setWing={setWing} save={action.save} disabled={disabled("target")} dirty={dirty.includes("target")} mark={mark} operationsHref={`/society/${sid}`} version={data.config_version} /></div></div>
    </>}
    {!read.loading && !read.error && !data && <Alert id="setup-identity-error">The configuration response does not match this controller.</Alert>}
  </div>;
}