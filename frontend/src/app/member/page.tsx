"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import api from "@/lib/api";
import Sidebar from "@/components/Sidebar";

export default function MemberDashboard() {
  const router = useRouter();
  const [devices, setDevices] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem("token");
    const role = localStorage.getItem("role");
    const sid = localStorage.getItem("society_id");
    if (!token || !sid || role !== "member") { router.push("/login"); return; }
    api.get(`/api/admin/dashboard?society_id=${encodeURIComponent(sid)}`)
      .then((res) => setDevices(res.data.devices || []))
      .catch(() => setDevices([]))
      .finally(() => setLoading(false));
  }, [router]);

  if (loading) return <div className="flex h-screen items-center justify-center text-gray-500">Loading Dashboard...</div>;
  return (
    <div className="flex h-screen overflow-hidden bg-[#0a0e17]">
      <Sidebar role="member" />
      <main className="flex-1 overflow-y-auto p-6 pt-20">
        <h1 className="text-2xl font-bold text-white mb-2">EMS Status</h1>
        <p className="text-xs text-gray-500 mb-6">Read-only access</p>
        {devices.map((dev: any) => (
          <section key={dev.id} className="bg-gray-900/80 border border-gray-800 rounded-xl p-6 mb-6">
            <div className="flex justify-between mb-4"><h2 className="text-lg font-bold text-white">{dev.name}</h2><span className="text-xs text-gray-400">{dev.connected ? "ONLINE" : "OFFLINE"}</span></div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              {["A","B","C","D"].map((code) => { const slot=dev.slots?.[code]; if(!slot) return null; return (
                <div key={code} className="border border-gray-800 bg-gray-800/30 rounded-lg p-4">
                  <div className="flex justify-between"><b className="text-white">{slot.display_name || `Slot ${code}`}</b><span className="text-xs text-gray-500">{code}</span></div>
                  <div className="text-xs text-gray-400 mt-3 space-y-1"><div>Target: {slot.target_days} days</div><div>Used: {slot.used_days} days</div><div>Physical: {slot.physical_toggle}</div><div>Status: {slot.status}</div></div>
                </div>
              ); })}
            </div>
          </section>
        ))}
        {!devices.length && <div className="text-gray-500">No devices assigned.</div>}
      </main>
    </div>
  );
}
