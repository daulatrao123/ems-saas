"use client";
import { useCallback, useEffect, useState } from "react";
import api from "@/lib/api";
import { OperationalDashboard } from "@/components/ops/OperationalDashboard";
import { OpsShell, useRoleSession } from "@/components/ops/OpsShell";
import { RegisterPiForm, DeviceTable, SocietyDevice } from "@/components/provisioning/AdminDevices";
import { panel } from "@/components/ops/DashboardHeader";

export default function AdminDashboard() {
  const { session, ready } = useRoleSession(["society_admin"]);
  const [registry, setRegistry] = useState<SocietyDevice[]>([]);
  const [showProvisioning, setShowProvisioning] = useState(false);
  const [dashKey, setDashKey] = useState(0);
  // Tenant is derived server-side from the session; no society_id is sent.
  const fetchRegistry = useCallback(async () => { try { setRegistry((await api.get("/api/admin/devices")).data.devices || []); } catch { /* shown empty */ } }, []);
  useEffect(() => { if (ready) Promise.resolve().then(fetchRegistry); }, [ready, fetchRegistry]);
  if (!ready || !session) return <div className="flex h-screen items-center justify-center text-gray-500">Loading Dashboard...</div>;
  const sid = session.society_id != null ? String(session.society_id) : null;
  return (
    <OpsShell session={session}>
      <OperationalDashboard key={dashKey} societyId={sid} readOnly={false} backHref="/admin" />
      <section className={`${panel} mt-3 p-4`}>
        <button data-testid="toggle-provisioning" onClick={() => setShowProvisioning(!showProvisioning)} className="text-[10px] uppercase tracking-[0.14em] text-gray-400 hover:text-white">
          {showProvisioning ? "▾" : "▸"} Device Provisioning · {registry.length} registered
        </button>
        {showProvisioning && (
          <div className="mt-3">
            <RegisterPiForm onRegistered={() => { fetchRegistry(); setDashKey((k) => k + 1); }} />
            <DeviceTable devices={registry} />
          </div>
        )}
      </section>
    </OpsShell>
  );
}
