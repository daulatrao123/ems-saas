"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import api from "@/lib/api";
import { OpsShell, useRoleSession } from "@/components/ops/OpsShell";
import { Dot, btn, label, panel, tone } from "@/components/ops/DashboardHeader";
import { DeviceStateBadges } from "@/components/StateBadges";
import { CreateSocietyForm, CreateUserForm } from "@/components/provisioning/SuperAdminForms";

type Device = { id: string; name: string; online: boolean; config_state?: string | null; ota_state?: string | null; storage_state?: string | null };
type Society = { id: number; name: string; location: string; status?: string; pi_online: boolean; devices?: Device[] };

function SocietyCard({ s, retired }: { s: Society; retired: boolean }) {
  return (
    <article data-testid={`society-card-${s.id}`} className={`${panel} p-4 ${retired ? "opacity-60" : ""}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2"><h3 className="text-base font-bold text-white truncate">{s.name}</h3><span data-testid={`society-status-${s.id}`} className={`px-1.5 py-0.5 text-[9px] font-bold border ${retired ? "border-red-500/50 text-red-400" : "border-emerald-500/40 text-emerald-300"}`}>{retired ? "RETIRED" : "ACTIVE"}</span></div>
          <div className="font-mono text-[11px] text-gray-500">#{s.id} · {s.location || "—"} · {s.devices?.length || 0} device(s)</div>
        </div>
        {!retired && <span className={`flex items-center gap-2 font-mono text-[11px] font-bold ${s.pi_online ? "text-emerald-400" : "text-red-400"}`}><Dot on={s.pi_online} />{s.pi_online ? "PI ONLINE" : "PI OFFLINE"}</span>}
      </div>
      <ul className="mt-3 space-y-1">
        {s.devices?.map((d) => <li key={d.id} className="flex items-center justify-between font-mono text-[11px] text-gray-300"><span>{d.name}</span><span className="flex items-center gap-2"><DeviceStateBadges dev={d} /><span className={d.online ? "text-emerald-400" : "text-gray-500"}>{d.online ? "ONLINE" : "OFFLINE"}</span></span></li>)}
        {!s.devices?.length && <li className="text-[11px] text-gray-600">No devices linked.</li>}
      </ul>
      {!retired && <Link data-testid={`open-ops-${s.id}`} href={`/society/${s.id}`} className={`${btn} ${tone.cyan} mt-3 inline-block`}>OPEN OPERATIONS →</Link>}
    </article>
  );
}

export default function SuperAdminDashboard() {
  const { session, ready } = useRoleSession(["super_admin"]);
  const [societies, setSocieties] = useState<Society[]>([]);
  const [error, setError] = useState("");
  const load = useCallback(() => api.get("/api/super-admin/societies").then((r) => setSocieties(r.data || [])).catch(() => setError("Failed to load EMS inventory.")), []);
  useEffect(() => { if (ready) load(); }, [ready, load]);
  if (!ready || !session) return <div className="flex h-screen items-center justify-center text-gray-500">Loading EMS inventory...</div>;
  const active = societies.filter((s) => s.status !== "RETIRED"), retired = societies.filter((s) => s.status === "RETIRED");
  return (
    <OpsShell session={session}>
      <div className="mb-4"><h1 className="text-2xl font-bold text-white">Super Admin · Fleet</h1><p className="font-mono text-xs text-gray-500">{active.length} active tenant(s) · {retired.length} retired</p></div>
      {error && <div className="mb-3 text-red-400">{error}</div>}
      <div className="grid gap-3 lg:grid-cols-2 mb-6">
        <CreateSocietyForm onCreated={() => load()} />
        <CreateUserForm societies={active.map((s) => ({ id: s.id, name: s.name }))} />
      </div>
      <div className={`${label} mb-2`}>Active societies</div>
      <div data-testid="active-societies" className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{active.map((s) => <SocietyCard key={s.id} s={s} retired={false} />)}{!active.length && <div className="text-gray-500">No active societies.</div>}</div>
      {retired.length > 0 && (
        <details data-testid="retired-societies" className="mt-6">
          <summary className={`${label} cursor-pointer`}>Retired societies ({retired.length}) — lifecycle records, not operational tenants</summary>
          <div className="mt-2 grid gap-3 md:grid-cols-2 xl:grid-cols-3">{retired.map((s) => <SocietyCard key={s.id} s={s} retired />)}</div>
        </details>
      )}
    </OpsShell>
  );
}
