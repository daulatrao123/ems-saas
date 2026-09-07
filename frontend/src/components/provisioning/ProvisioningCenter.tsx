"use client";
import { useCallback, useEffect, useState } from "react";
import api from "@/lib/api";
import { SocietyDevice } from "./AdminDevices";
import { Confirm, ConfirmDialog } from "@/components/ops/ConfirmDialog";
import { Dot, btn, input, label, panel, tone } from "@/components/ops/DashboardHeader";
import { ago, errorText } from "@/components/ops/types";

type Issued = { key_id: string; api_key: string };   // React state only; never persisted

function Field({ k, v, tone: t, testId }: { k: string; k2?: string; v: string; tone?: string; testId?: string }) {
  return <><dt className="text-gray-500">{k}</dt><dd data-testid={testId} className={`truncate ${t || "text-gray-100"}`}>{v}</dd></>;
}

function DeviceCard({ d, sid, onChanged, ask, notify }: { d: SocietyDevice; sid: string; onChanged: () => void; ask: (c: Confirm) => void; notify: (m: string, ok: boolean) => void }) {
  const [issued, setIssued] = useState<Issued | null>(null);   // secret from THIS session's rotation only
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const copy = (v: string) => navigator.clipboard.writeText(v).then(() => notify("Copied", true)).catch(() => notify("Clipboard unavailable", false));
  const run = async (name: string, fn: () => Promise<void>) => { setBusy(name); try { await fn(); } catch (e) { notify(errorText(e).detail, false); } finally { setBusy(null); } };
  const rotate = () => run("rotate", async () => {
    const r = await api.post(`/api/super-admin/devices/${d.id}/credentials/rotate`, {});
    setIssued({ key_id: r.data.key_id, api_key: r.data.api_key }); setReveal(false); notify(`Key rotated → ${r.data.key_id}. Previous key is now invalid.`, true); onChanged();
  });
  const download = () => run("download", async () => {
    const r = await api.post(`/api/super-admin/devices/${d.id}/provisioning-package`, {}, { responseType: "blob" });
    const url = URL.createObjectURL(r.data); const a = document.createElement("a"); a.href = url; a.download = `ems-pi-provisioning-${d.id.slice(0, 8)}.zip`; a.click(); URL.revokeObjectURL(url);
    setIssued(null); setReveal(false); notify("Provisioning package downloaded successfully. The previous API key is now invalid.", true); onChanged();
  });
  const revoke = () => run("revoke", async () => { await api.post(`/api/super-admin/devices/${d.id}/credentials/revoke`, { reason: "super_admin_revoke" }); setIssued(null); notify("Device credential revoked — the Pi can no longer authenticate.", true); onChanged(); });
  const setFeedback = (installed: boolean) => run("feedback", async () => { await api.post(`/api/super-admin/devices/${d.id}/feedback-hardware`, { installed }); notify(`Feedback hardware ${installed ? "ON" : "OFF"}`, true); onChanged(); });
  const cred = d.credential_state === "active" ? "ACTIVE" : "NONE / REVOKED";
  return (
    <article data-testid={`prov-device-${d.id}`} className={`${panel} p-4`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><div className="text-base font-bold text-white">{d.name}</div><div className="font-mono text-[11px] text-gray-500">{d.hardware_profile} · society #{sid} · {d.status}</div></div>
        <span data-testid={`prov-online-${d.id}`} className={`flex items-center gap-2 font-mono text-xs font-bold ${d.online ? "text-emerald-400" : "text-red-400"}`}><Dot on={d.online} />{d.online ? "ONLINE" : "OFFLINE"} <span className="text-gray-500 font-normal">· last sync {ago(d.last_sync)}</span></span>
      </div>
      <dl className="mt-3 grid grid-cols-[130px_1fr] gap-y-1.5 font-mono text-[11px]">
        <dt className="text-gray-500">DEVICE ID</dt>
        <dd className="flex items-center gap-2 min-w-0"><span data-testid={`prov-device-id-${d.id}`} className="truncate text-gray-100">{d.id}</span><button data-testid={`prov-copy-id-${d.id}`} onClick={() => copy(d.id)} className={`${btn} ${tone.gray} py-0.5`}>COPY</button></dd>
        <dt className="text-gray-500">API KEY</dt>
        <dd className="flex flex-wrap items-center gap-2 min-w-0">
          <span data-testid={`prov-api-key-${d.id}`} className={`truncate ${issued && reveal ? "text-amber-200" : "text-gray-400"}`}>{issued ? (reveal ? issued.api_key : "•".repeat(24)) : "•••••••••••• (not retrievable — stored hashed; rotate to issue a new key)"}</span>
          <button data-testid={`prov-reveal-${d.id}`} disabled={!issued} onClick={() => setReveal(!reveal)} className={`${btn} ${tone.gray} py-0.5`}>{reveal ? "HIDE" : "REVEAL"}</button>
          <button data-testid={`prov-copy-key-${d.id}`} disabled={!issued} onClick={() => issued && copy(issued.api_key)} className={`${btn} ${tone.gray} py-0.5`}>COPY</button>
          <button data-testid={`prov-rotate-${d.id}`} disabled={busy !== null} className={`${btn} ${tone.amber} py-0.5`}
            onClick={() => ask({ title: "Rotate API key", body: "The current key stops working immediately. The new key is shown once here and must be provisioned onto the Pi.", action: "ROTATE KEY", danger: true, onConfirm: rotate })}>ROTATE</button>
        </dd>
        <Field k="KEY ID" v={issued?.key_id || d.key_id || "—"} testId={`prov-key-id-${d.id}`} />
        <Field k="CREDENTIAL" v={cred} tone={cred === "ACTIVE" ? "text-emerald-300" : "text-red-400"} testId={`prov-cred-${d.id}`} />
        <Field k="FIRMWARE" v={d.firmware_version || "N/A"} />
        <Field k="CONFIG / OTA" v={`${d.config_state || "—"} / ${d.ota_state || "—"}`} />
        <Field k="STORAGE" v={d.storage_state || "UNKNOWN"} />
        <dt className="text-gray-500">FEEDBACK HW</dt>
        <dd className="flex items-center gap-2">
          <span data-testid={`prov-feedback-${d.id}`} className={d.feedback_hardware_installed ? "text-emerald-300" : "text-gray-400"}>{d.feedback_hardware_installed ? "ON — physically installed" : "OFF — not installed"}</span>
          <button data-testid={`prov-feedback-toggle-${d.id}`} disabled={busy !== null} onClick={() => setFeedback(!d.feedback_hardware_installed)} className={`${btn} ${d.feedback_hardware_installed ? tone.gray : tone.cyan} py-0.5`}>{d.feedback_hardware_installed ? "SET OFF" : "SET ON"}</button>
        </dd>
      </dl>
      <div className="mt-4 flex flex-wrap gap-2">
        <button data-testid={`prov-download-${d.id}`} disabled={busy !== null || d.status === "RETIRED"} className={`${btn} ${tone.cyan} py-2.5`}
          onClick={() => ask({ title: "Generate New Provisioning Package?", body: "To keep provisioning secure, this package will contain a newly rotated API key. The previous key will stop working.\n\nAny Pi currently using the previous key must be reprovisioned with this package.", action: "GENERATE & DOWNLOAD", danger: true, onConfirm: download })}>
          {busy === "download" ? "GENERATING…" : "DOWNLOAD PI PROVISIONING ZIP"}
        </button>
        <button data-testid={`prov-revoke-${d.id}`} disabled={busy !== null || d.credential_state !== "active"} className={`${btn} ${tone.red} py-2.5`}
          onClick={() => ask({ title: "Revoke device credential", body: "The Pi will be rejected on its next sync and go OFFLINE. The device stays registered (audit history preserved); download a new package to reprovision.", action: "REVOKE DEVICE", danger: true, onConfirm: revoke })}>REVOKE DEVICE</button>
      </div>
    </article>
  );
}

export function ProvisioningCenter({ societyId }: { societyId: string }) {
  const [devices, setDevices] = useState<SocietyDevice[]>([]); const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [msg, setMsg] = useState<{ t: string; ok: boolean } | null>(null); const [name, setName] = useState(""); const [busy, setBusy] = useState(false);
  const load = useCallback(() => api.get(`/api/admin/devices?society_id=${societyId}`).then((r) => setDevices(r.data.devices || [])).catch(() => setDevices([])), [societyId]);
  useEffect(() => { Promise.resolve().then(load); }, [load]);
  const notify = (t: string, ok: boolean) => { setMsg({ t, ok }); setTimeout(() => setMsg(null), 5000); };
  const register = async () => {   // new device: register + credential issued once -> then DOWNLOAD builds the package (rotates)
    setBusy(true);
    try { const r = await api.post("/api/admin/devices/register", { name, society_id: Number(societyId) }); notify(`Registered ${r.data.name} (${r.data.key_id}). Use DOWNLOAD PI PROVISIONING ZIP to get its installer.`, true); setName(""); await load(); }
    catch (e) { notify(errorText(e).detail, false); } finally { setBusy(false); }
  };
  return (
    <section data-testid="provisioning-center" className={`${panel} p-4 space-y-3`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><div className={label}>Pi Provisioning Center</div><div className="text-[11px] text-gray-500">Register → download installer ZIP → copy to Pi → <span className="font-mono">sudo ./install.sh</span> → ONLINE after first authenticated sync.</div></div>
        <div className="flex items-center gap-2">
          <input data-testid="prov-register-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="New device name" className={`${input} w-52`} />
          <button data-testid="prov-register-submit" disabled={busy || !name.trim()} onClick={register} className={`${btn} ${tone.amber}`}>REGISTER DEVICE</button>
        </div>
      </div>
      {msg && <div data-testid="prov-message" className={`border px-3 py-2 font-mono text-xs ${msg.ok ? "border-emerald-500/40 text-emerald-300" : "border-red-500/40 text-red-300"}`}>{msg.t}</div>}
      <div className="grid gap-3 xl:grid-cols-2">{devices.map((d) => <DeviceCard key={d.id} d={d} sid={societyId} onChanged={load} ask={setConfirm} notify={notify} />)}</div>
      {devices.length === 0 && <div className="text-sm text-gray-500">No registered Pi devices in this society.</div>}
      <ConfirmDialog c={confirm} onClose={() => setConfirm(null)} />
    </section>
  );
}
