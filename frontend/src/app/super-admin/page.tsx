"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import api from "@/lib/api";
import Sidebar from "@/components/Sidebar";

export default function SocietyDashboard() {
  const router = useRouter();
  const [devices, setDevices] = useState<any[]>([]);
  const [societyId, setSocietyId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<any>(null);

  useEffect(() => {
    const sid = localStorage.getItem("society_id");
    const role = localStorage.getItem("role");
    if (!sid || role === "super_admin") {
      router.push("/login");
    } else {
      setSocietyId(sid);
      fetchDashboard(sid);
    }
  }, [router]);

  const fetchDashboard = async (sid: string) => {
    try {
      const res = await api.get(`/api/admin/dashboard?society_id=${sid}`);
      setDevices(res.data.devices || []);
    } catch (err) {
      showToast("Failed to load dashboard", false);
    }
    setLoading(false);
  };

  const showToast = (msg: string, ok: boolean) => { setToast({ msg, ok }); setTimeout(() => setToast(null), 3000); };

  const sendCommand = async (deviceId: string, slot: string, command: string) => {
    if (!societyId) return;
    try {
      await api.post("/api/admin/pi-command", {
        society_id: societyId,
        device_id: deviceId,
        slot: slot,
        command: command
      });
      showToast(`Command ${command} sent to Slot ${slot}`, true);
      setTimeout(() => fetchDashboard(societyId), 3000);
    } catch {
      showToast("Command failed", false);
    }
  };

  if (loading) return <div className="flex h-screen items-center justify-center text-gray-500">Loading Dashboard...</div>;

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar role="society_admin" />
      <main className="flex-1 overflow-y-auto p-6 pt-20" style={{ background: "#0a0e17" }}>
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-white">Society Dashboard</h1>
          <p className="text-xs text-gray-500">{devices.length} Pi devices connected</p>
        </div>

        {devices.length === 0 && (
          <div className="bg-gray-900/80 border border-gray-800 rounded-xl p-8 text-center text-gray-500">
            No Pi devices assigned to your society yet.
          </div>
        )}

        {devices.map((dev: any) => (
          <div key={dev.id} className="bg-gray-900/80 border border-gray-800 rounded-xl p-6 mb-6">
            <div className="flex justify-between items-center mb-4 border-b border-gray-800 pb-3">
              <div>
                <h2 className="text-lg font-bold text-white">{dev.name}</h2>
                <p className="text-[10px] text-gray-500 font-mono">{dev.id}</p>
              </div>
              <div className={`px-3 py-1 rounded-full text-[10px] font-bold ${dev.connected ? "bg-emerald-500/15 text-emerald-400" : "bg-red-500/15 text-red-400"}`}>
                {dev.connected ? "ONLINE" : "OFFLINE"}
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              {["A", "B", "C", "D"].map(slotCode => {
                const slot = dev.slots[slotCode];
                if (!slot) return null;
                const isActive = dev.active_slot === slotCode;
                const isDisabled = slot.disabled;
                
                return (
                  <div key={slotCode} className={`border p-4 rounded-lg ${isActive ? "border-cyan-500 bg-cyan-500/5" : "border-gray-800 bg-gray-800/30"}`}>
                    <div className="flex justify-between items-center mb-2">
                      <h3 className="text-sm font-bold text-white">{slot.display_name || `Slot ${slotCode}`}</h3>
                      <span className="text-[9px] font-mono bg-gray-700 text-gray-300 px-1.5 py-0.5 rounded">{slotCode}</span>
                    </div>
                    
                    <div className="text-[10px] text-gray-400 mb-3 space-y-1">
                        <div>Target: <span className="text-gray-200 font-mono">{slot.target_days} days</span></div>
                        <div>Used: <span className="text-gray-200 font-mono">{slot.used_days} days</span></div>
                        <div>Physical: <span className={`font-mono ${slot.physical_toggle === "ON" ? "text-emerald-400" : "text-gray-500"}`}>{slot.physical_toggle}</span></div>
                    </div>

                    {!isDisabled ? (
                      <button 
                        onClick={() => sendCommand(dev.id, slotCode, isActive ? "off_slot" : "set_active_slot")}
                        className={`w-full py-1.5 text-[10px] font-bold rounded transition-colors ${
                          isActive ? "bg-red-500/20 text-red-400 hover:bg-red-500/30 border border-red-500/30" : "bg-cyan-500/20 text-cyan-400 hover:bg-cyan-500/30 border border-cyan-500/30"
                        }`}
                      >
                        {isActive ? "DEACTIVATE" : "ACTIVATE"}
                      </button>
                    ) : (
                      <div className="w-full py-1.5 text-[10px] font-bold rounded bg-gray-700/50 text-gray-500 text-center">
                        DISABLED
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ))}

        {toast && <div className={`fixed bottom-4 right-4 px-4 py-2 rounded-lg border z-[100] ${toast.ok ? "border-emerald-500/50 text-emerald-400" : "border-red-500/50 text-red-400"} bg-gray-900`}>{toast.msg}</div>}
      </main>
    </div>
  );
}