"use client";
import { useState } from "react";
import api from "@/lib/api";
import { OneTimeSecret } from "./OneTimeSecret";

export const field = "w-full rounded-md border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-600 focus:border-cyan-500 focus:outline-none";
export const primaryBtn = "rounded-md bg-cyan-500/20 border border-cyan-500/40 px-4 py-2 text-xs font-bold text-cyan-300 hover:bg-cyan-500/30 disabled:opacity-50";

export function errorText(err: unknown): string {
  const e = err as { response?: { data?: { detail?: string } } };
  return e?.response?.data?.detail || "Request failed";
}

export type SocietyOption = { id: number; name: string };

export function CreateSocietyForm({ onCreated }: { onCreated: (s: SocietyOption) => void }) {
  const [name, setName] = useState(""); const [location, setLocation] = useState(""); const [resetDay, setResetDay] = useState("1");
  const [busy, setBusy] = useState(false); const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setMsg(null);
    try {
      const res = await api.post("/api/super-admin/societies", { name, location, reset_day: Number(resetDay) });
      setMsg({ ok: true, text: `Society #${res.data.society_id} "${res.data.name}" created` });
      onCreated({ id: res.data.society_id, name: res.data.name });
      setName(""); setLocation("");
    } catch (err) { setMsg({ ok: false, text: errorText(err) }); }
    setBusy(false);
  };
  return (
    <form onSubmit={submit} data-testid="create-society-form" className="rounded-xl border border-gray-800 bg-gray-900/80 p-5">
      <h3 className="text-sm font-bold text-white">Create Society</h3>
      <p className="text-[11px] text-gray-500 mb-3">New tenant. Devices and users are linked to it afterwards.</p>
      <div className="grid gap-2 md:grid-cols-3">
        <input data-testid="society-name-input" className={field} placeholder="Society name" value={name} onChange={(e) => setName(e.target.value)} required />
        <input data-testid="society-location-input" className={field} placeholder="Location" value={location} onChange={(e) => setLocation(e.target.value)} />
        <input data-testid="society-reset-day-input" className={field} type="number" min={1} max={28} value={resetDay} onChange={(e) => setResetDay(e.target.value)} title="Billing reset day (1-28)" />
      </div>
      <div className="mt-3 flex items-center gap-3">
        <button data-testid="create-society-submit" className={primaryBtn} disabled={busy || !name.trim()}>{busy ? "CREATING…" : "CREATE SOCIETY"}</button>
        {msg && <span data-testid="create-society-message" className={`text-xs ${msg.ok ? "text-emerald-400" : "text-red-400"}`}>{msg.text}</span>}
      </div>
    </form>
  );
}

export function CreateUserForm({ societies }: { societies: SocietyOption[] }) {
  const [societyId, setSocietyId] = useState(""); const [role, setRole] = useState("society_admin");
  const [email, setEmail] = useState(""); const [name, setName] = useState("");
  const [busy, setBusy] = useState(false); const [err, setErr] = useState("");
  const [issued, setIssued] = useState<{ email: string; password: string; role: string } | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(""); setIssued(null);
    try {
      const res = await api.post("/api/super-admin/users", { society_id: Number(societyId), role, email, name });
      setIssued({ email: res.data.email, password: res.data.temporary_password, role: res.data.role });
      setEmail(""); setName("");
    } catch (e2) { setErr(errorText(e2)); }
    setBusy(false);
  };
  return (
    <form onSubmit={submit} data-testid="create-user-form" className="rounded-xl border border-gray-800 bg-gray-900/80 p-5">
      <h3 className="text-sm font-bold text-white">Create User</h3>
      <p className="text-[11px] text-gray-500 mb-3">Society admin or member, bound to one society. A temporary password is generated server-side.</p>
      <div className="grid gap-2 md:grid-cols-4">
        <select data-testid="user-society-select" className={field} value={societyId} onChange={(e) => setSocietyId(e.target.value)} required>
          <option value="">Select society…</option>
          {societies.map((s) => <option key={s.id} value={s.id}>#{s.id} {s.name}</option>)}
        </select>
        <select data-testid="user-role-select" className={field} value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="society_admin">society_admin</option>
          <option value="member">member</option>
        </select>
        <input data-testid="user-email-input" className={field} type="email" placeholder="email@society.com" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <input data-testid="user-name-input" className={field} placeholder="Full name" value={name} onChange={(e) => setName(e.target.value)} required />
      </div>
      <div className="mt-3 flex items-center gap-3">
        <button data-testid="create-user-submit" className={primaryBtn} disabled={busy || !societyId || !email || !name}>{busy ? "CREATING…" : "CREATE USER"}</button>
        {err && <span data-testid="create-user-error" className="text-xs text-red-400">{err}</span>}
      </div>
      {issued && (
        <OneTimeSecret testId="user-temp-password" title={`Temporary password for ${issued.email} (${issued.role})`} onDismiss={() => setIssued(null)}
          rows={[{ label: "Email", value: issued.email }, { label: "Password", value: issued.password, secret: true }]} />
      )}
    </form>
  );
}
