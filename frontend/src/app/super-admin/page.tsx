"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import api from "@/lib/api";
import Sidebar from "@/components/Sidebar";

export default function SuperAdminDashboard() {
  const router = useRouter();
  const [societies, setSocieties] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const token = localStorage.getItem("token");
    const role = localStorage.getItem("role");
    if (!token || role !== "super_admin") { router.push("/login"); return; }
    api.get("/api/super-admin/societies")
      .then((res) => setSocieties(res.data || []))
      .catch(() => setError("Failed to load EMS inventory."))
      .finally(() => setLoading(false));
  }, [router]);

  if (loading) return <div className="flex h-screen items-center justify-center text-gray-500">Loading EMS inventory...</div>;
  return (
    <div className="flex h-screen overflow-hidden bg-[#0a0e17]">
      <Sidebar role="super_admin" />
      <main className="flex-1 overflow-y-auto p-6 pt-20">
        <h1 className="text-2xl font-bold text-white">Super Admin</h1>
        <p className="text-xs text-gray-500 mb-6">Society and Pi inventory overview</p>
        {error && <div className="text-red-400 mb-4">{error}</div>}
        <div className="space-y-6">
          {societies.map((society:any) => (
            <section key={society.id} className="bg-gray-900/80 border border-gray-800 rounded-xl p-6">
              <div className="flex justify-between items-center mb-4"><div><h2 className="text-lg font-bold text-white">{society.name}</h2><p className="text-xs text-gray-500">{society.location}</p></div><span className="text-xs text-gray-400">{society.pi_online ? "PI ONLINE" : "PI OFFLINE"}</span></div>
              {society.devices?.map((dev:any) => <div key={dev.id} className="border-t border-gray-800 pt-4 mt-4"><div className="flex justify-between"><b className="text-white">{dev.name}</b><span className="text-xs text-gray-500">{dev.online ? "ONLINE" : "OFFLINE"}</span></div><div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3 mt-3">{["A","B","C","D"].map((code)=>{const s=dev.slots?.[code];if(!s)return null;return <div key={code} className="rounded-lg border border-gray-800 p-3"><div className="text-sm text-white">{s.display_name || `Slot ${code}`}</div><div className="text-[11px] text-gray-400 mt-2">Target {s.target_days}d · Used {s.used_days}d · Physical {s.physical_toggle}</div><div className="text-[11px] text-gray-500 mt-1">Feedback {s.feedback_enabled ? "configured" : "not configured"}</div></div>})}</div></div>)}
            </section>
          ))}
          {!societies.length && <div className="text-gray-500">No societies configured.</div>}
        </div>
      </main>
    </div>
  );
}
