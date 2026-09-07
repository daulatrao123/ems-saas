"use client";
import { useState } from "react";
import api from "@/lib/api";
import { OneTimeSecret } from "./OneTimeSecret";
import { DeviceStateBadges } from "../StateBadges";
import { field, primaryBtn, errorText } from "./SuperAdminForms";

export type SocietyDevice = {
  id: string; name: string; status: string; hardware_profile: string; feedback_hardware_installed: boolean;
  firmware_version: string | null; key_id: string | null; credential_state: string; last_sync: string | null;
  online: boolean; config_state: string | null; ota_state: string | null; storage_state: string | null;
};

type Issued = { device_id: string; key_id: string; api_key: string; name: string };

export function RegisterPiForm({ onRegistered }: { onRegistered: () => void }) {
  const [name, setName] = useState(""); const [feedback, setFeedback] = useState(false);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState(""); const [issued, setIssued] = useState<Issued | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(""); setIssued(null);
    try {
      // society_id is intentionally NOT sent: the backend binds the device to the session's society.
      const res = await api.post("/api/admin/devices/register", { name, hardware_profile: "EMS-4CH-v1", feedback_hardware_installed: feedback });
      setIssued(res.data); setName(""); setFeedback(false); onRegistered();
    } catch (e2) { setErr(errorText(e2)); }
    setBusy(false);
  };
  return (
    <form onSubmit={submit} data-testid="register-pi-form" className="rounded-xl border border-gray-800 bg-gray-900/80 p-5 mb-6">
      <h3 className="text-sm font-bold text-white">Register Pi Controller</h3>
      <p className="text-[11px] text-gray-500 mb-3">Creates a device in your society and issues its credential. Provision the secret onto the Pi out-of-band.</p>
      <div className="flex flex-col md:flex-row gap-2 md:items-center">
        <input data-testid="pi-name-input" className={`${field} md:max-w-xs`} placeholder="Device name (e.g. Block A Pump Room)" value={name} onChange={(e) => setName(e.target.value)} required />
        <label className="flex items-center gap-2 text-xs text-gray-300 px-1">
          <input data-testid="pi-feedback-checkbox" type="checkbox" checked={feedback} onChange={(e) => setFeedback(e.target.checked)} className="accent-cyan-500" />
          Feedback hardware installed
        </label>
        <button data-testid="register-pi-submit" className={primaryBtn} disabled={busy || !name.trim()}>{busy ? "REGISTERING…" : "REGISTER PI"}</button>
        {err && <span data-testid="register-pi-error" className="text-xs text-red-400">{err}</span>}
      </div>
      {issued && (
        <OneTimeSecret testId="pi-credential" title={`Credential for "${issued.name}"`} onDismiss={() => setIssued(null)}
          rows={[{ label: "Device ID", value: issued.device_id }, { label: "Key ID", value: issued.key_id }, { label: "API Key", value: issued.api_key, secret: true }]} />
      )}
    </form>
  );
}

export function DeviceTable({ devices }: { devices: SocietyDevice[] }) {
  return (
    <div data-testid="society-device-table" className="rounded-xl border border-gray-800 bg-gray-900/80 p-5 mb-6 overflow-x-auto">
      <h3 className="text-sm font-bold text-white mb-3">Registered Pi Devices <span className="text-gray-500 font-normal">({devices.length})</span></h3>
      {devices.length === 0 ? <div data-testid="society-device-table-empty" className="text-xs text-gray-500">No devices registered in your society.</div> : (
        <table className="w-full text-left text-xs">
          <thead className="text-[10px] uppercase tracking-wide text-gray-500">
            <tr><th className="py-1 pr-3">Name</th><th className="py-1 pr-3">Device ID</th><th className="py-1 pr-3">Key ID</th><th className="py-1 pr-3">Status</th><th className="py-1 pr-3">Link</th><th className="py-1 pr-3">State</th><th className="py-1">Last sync</th></tr>
          </thead>
          <tbody className="text-gray-300">
            {devices.map((d) => (
              <tr key={d.id} data-testid={`device-row-${d.id}`} className="border-t border-gray-800">
                <td className="py-2 pr-3 font-semibold text-white">{d.name}{d.feedback_hardware_installed && <span className="ml-2 text-[9px] text-cyan-400">FEEDBACK HW</span>}</td>
                <td className="py-2 pr-3 font-mono text-[10px] text-gray-400">{d.id}</td>
                <td className="py-2 pr-3 font-mono text-[10px]">{d.key_id || <span className="text-red-400">none</span>}</td>
                <td className="py-2 pr-3">{d.status}</td>
                <td className="py-2 pr-3"><span className={d.online ? "text-emerald-400" : "text-gray-500"}>{d.online ? "ONLINE" : "OFFLINE"}</span></td>
                <td className="py-2 pr-3"><DeviceStateBadges dev={d} /></td>
                <td className="py-2 font-mono text-[10px] text-gray-400">{d.last_sync ? new Date(d.last_sync).toLocaleString() : "never"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
