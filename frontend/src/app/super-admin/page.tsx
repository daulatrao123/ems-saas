"use client";
import { DeviceStateBadges } from "../../components/StateBadges";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import api from "@/lib/api";
import Sidebar from "@/components/Sidebar";
import { getSession, Session } from "@/lib/auth";
import { CreateSocietyForm, CreateUserForm } from "@/components/provisioning/SuperAdminForms";

type Slot = { display_name: string; target_days: number; used_days: number; physical_toggle: string; feedback_enabled: boolean };
type Device = { id: string; name: string; online: boolean; slots?: Record<string, Slot>; config_state?: string | null; ota_state?: string | null; storage_state?: string | null };
type Society = { id: number; name: string; location: string; status?: string; pi_online: boolean; devices?: Device[] };

export default function SuperAdminDashboard() {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [societies, setSocieties] = useState<Society[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadSocieties = useCallback(() => {
    return api.get("/api/super-admin/societies")
      .then((res) => setSocieties(res.data || []))
      .catch(() => setError("Failed to load EMS inventory."));
  }, []);

  useEffect(() => {
    getSession().then((s) => {
      const role = s?.role;
      if (!s || role !== "super_admin") { router.push("/login"); return; }
      setSession(s);
      loadSocieties().finally(() => setLoading(false));
    });
  }, [router, loadSocieties]);

  if (loading) return <div className="flex h-screen items-center justify-center text-gray-500">Loading EMS inventory...</div>;
  return (
    <div className="flex h-screen overflow-hidden bg-[#0a0e17]">
      <Sidebar role="super_admin" name={session?.name || ""} />
      <main className="flex-1 overflow-y-auto p-6 pt-20">
        <h1 className="text-2xl font-bold text-white">Super Admin</h1>
        <p className="text-xs text-gray-500 mb-6">Society and Pi inventory overview</p>
        {error && <div className="text-red-400 mb-4">{error}</div>}
        <div className="grid gap-4 lg:grid-cols-2 mb-8">
          <CreateSocietyForm onCreated={() => loadSocieties()} />
          <CreateUserForm societies={societies.filter((s) => s.status !== "RETIRED").map((s) => ({ id: s.id, name: s.name }))} />
        </div>
        <div className="space-y-6">
          {societies.map((society) => (
            <section key={society.id} data-testid={`society-card-${society.id}`} className="bg-gray-900/80 border border-gray-800 rounded-xl p-6">
              <div className="flex justify-between items-center mb-4"><div><h2 className="text-lg font-bold text-white">{society.name}</h2><p className="text-xs text-gray-500">{society.location}</p></div><span className="text-xs text-gray-400">{society.pi_online ? "PI ONLINE" : "PI OFFLINE"}</span></div>
              {society.devices?.map((dev) => <div key={dev.id} className="border-t border-gray-800 pt-4 mt-4"><div className="flex justify-between"><b className="text-white">{dev.name}</b><span className="flex items-center gap-2"><DeviceStateBadges dev={dev} /><span className="text-xs text-gray-500">{dev.online ? "ONLINE" : "OFFLINE"}</span></span></div><div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3 mt-3">{["A","B","C","D"].map((code)=>{const s=dev.slots?.[code];if(!s)return null;return <div key={code} className="rounded-lg border border-gray-800 p-3"><div className="text-sm text-white">{s.display_name || `Slot ${code}`}</div><div className="text-[11px] text-gray-400 mt-2">Target {s.target_days}d · Used {s.used_days}d · Physical {s.physical_toggle}</div><div className="text-[11px] text-gray-500 mt-1">Feedback {s.feedback_enabled ? "configured" : "not configured"}</div></div>})}</div></div>)}
              {!society.devices?.length && <div className="text-xs text-gray-500">No devices linked yet.</div>}
            </section>
          ))}
          {!societies.length && <div className="text-gray-500">No societies configured.</div>}
        </div>
      </main>
    </div>
  );
}
