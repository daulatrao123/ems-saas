"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import api from "@/lib/api";

export default function MemberDashboard() {
  const router = useRouter();
  const [devices, setDevices] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const role = localStorage.getItem("role");
    const token = localStorage.getItem("token");
    if (!token || role !== "member") {
      router.push("/login");
      return;
    }
    fetchDashboard();
  }, [router]);

  const fetchDashboard = async () => {
    try {
      const res = await api.get(`/api/member/dashboard`);
      setDevices(res.data.devices || []);
    } catch (err) {
      console.error("Failed to load dashboard");
    }
    setLoading(false);
  };

  if (loading) return <div className="flex h-screen items-center justify-center text-gray-500">Loading Dashboard...</div>;

  return (
    <div className="flex h-screen overflow-hidden bg-[#0a0e17]">
      <main className="flex-1 overflow-y-auto p-6 pt-20">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-white">Member Dashboard</h1>
          <p className="text-xs text-gray-500">Energy Distribution Status</p>
        </div>

        {devices.length === 0 && (
          <div className="bg-gray-900/80 border border-gray-800 rounded-xl p-8 text-center text-gray-500">
            No devices available.
          </div>
        )}

        {devices.map((dev: any) => (
          <div key={dev.id} className="bg-gray-900/80 border border-gray-800 rounded-xl p-6 mb-6">
            <div className="flex justify-between items-center mb-4 border-b border-gray-800 pb-3">
              <h2 className="text-lg font-bold text-white">{dev.name}</h2>
              <div className={`px-3 py-1 rounded-full text-[10px] font-bold ${dev.connected ? "bg-emerald-500/15 text-emerald-400" : "bg-red-500/15 text-red-400"}`}>
                {dev.connected ? "ONLINE" : "OFFLINE"}
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              {["A", "B", "C", "D"].map(slotCode => {
                const slot = dev.slots[slotCode];
                if (!slot) return null;
                const isActive = dev.active_slot === slotCode;
                
                return (
                  <div key={slotCode} className={`border p-4 rounded-lg ${isActive ? "border-cyan-500 bg-cyan-500/5" : "border-gray-800 bg-gray-800/30"}`}>
                    <div className="flex justify-between items-center mb-2">
                      <h3 className="text-sm font-bold text-white">{slot.display_name || `Slot ${slotCode}`}</h3>
                      <span className="text-[9px] font-mono bg-gray-700 text-gray-300 px-1.5 py-0.5 rounded">{slotCode}</span>
                    </div>
                    
                    <div className="text-[10px] text-gray-400 space-y-1">
                        <div>Target: <span className="text-gray-200 font-mono">{slot.target_days} days</span></div>
                        <div>Used: <span className="text-gray-200 font-mono">{slot.used_days} days</span></div>
                        <div>Status: <span className={`font-mono ${slot.physical_toggle === "ON" ? "text-emerald-400" : "text-gray-500"}`}>{slot.physical_toggle}</span></div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </main>
    </div>
  );
}