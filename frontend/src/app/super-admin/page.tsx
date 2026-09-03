"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import api from "@/lib/api";
import Sidebar from "@/components/Sidebar";

export default function SuperAdminDashboard() {
  const router = useRouter();
  const [tab, setTab] = useState<"societies" | "users" | "devices" | "firmware">("societies");
  const [societies, setSocieties] = useState<any[]>([]);
  const [users, setUsers] = useState<any[]>([]);
  const [devices, setDevices] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  
  const [showDeviceModal, setShowDeviceModal] = useState(false);
  const [showKeyModal, setShowKeyModal] = useState(false);
  const [newDeviceCreds, setNewDeviceCreds] = useState({ id: "", key: "" });
  const [deviceForm, setDeviceForm] = useState({ name: "", society_id: "" });
  const [toast, setToast] = useState<any>(null);

  useEffect(() => { if (localStorage.getItem("role") !== "super_admin") router.push("/login"); }, [router]);

  const fetchData = async () => {
    try {
      const [sRes, uRes, dRes] = await Promise.all([
        api.get("/api/super-admin/societies"), 
        api.get("/api/super-admin/users"),
        api.get("/api/super-admin/devices")
      ]);
      setSocieties(sRes.data); 
      setUsers(uRes.data); 
      setDevices(dRes.data);
    } catch {}
    setLoading(false);
  };

  useEffect(() => { fetchData(); }, []);
  
  const showToast = (msg: string, ok: boolean) => { setToast({ msg, ok }); setTimeout(() => setToast(null), 3000); };
  const copyText = (t: string) => { navigator.clipboard.writeText(t); showToast("Copied!", true); };

  const saveDevice = async () => {
    try {
      const res = await api.post("/api/super-admin/devices/save", deviceForm);
      setShowDeviceModal(false);
      setNewDeviceCreds({ id: res.data.device_id, key: res.data.api_key });
      setShowKeyModal(true);
      setDeviceForm({ name: "", society_id: "" });
      fetchData();
      showToast("Pi Device Provisioned", true);
    } catch { showToast("Failed", false); }
  };

  const deleteDevice = async (id: string) => {
    if (!confirm("Delete this Pi Device?")) return;
    try { await api.post("/api/super-admin/devices/delete", { id }); fetchData(); showToast("Deleted", true); } catch { showToast("Failed", false); }
  };

  if (loading) return <div className="flex h-screen items-center justify-center text-gray-500">Loading...</div>;

  const tabs = [
    { key: "societies", label: "Societies", count: societies.length },
    { key: "users", label: "Users", count: users.length },
    { key: "devices", label: "Devices", count: devices.length },
    { key: "firmware", label: "Firmware", count: 0 }
  ];

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar role="super_admin" />
      <main className="flex-1 overflow-y-auto p-6 pt-20" style={{ background: "#0a0e17" }}>
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-white">Super Admin</h1>
            <p className="text-xs text-gray-500">{societies.length} societies, {devices.length} pi devices</p>
          </div>
          {tab === "devices" && (
            <button onClick={() => { setDeviceForm({ name: "", society_id: societies[0]?.id || "" }); setShowDeviceModal(true); }} className="px-4 py-2 bg-emerald-500 text-black text-xs font-bold rounded-lg hover:bg-emerald-600">
              + Add Pi Device
            </button>
          )}
        </div>

        <div className="flex gap-1 mb-6 bg-gray-900 p-1 rounded-lg w-fit">
          {tabs.map((t) => (
            <button key={t.key} onClick={() => setTab(t.key as any)} className={`px-4 py-2 rounded-md text-xs font-semibold transition-all ${tab === t.key ? "bg-cyan-500/20 text-cyan-400 border border-cyan-500/30" : "text-gray-500 hover:text-gray-300 border border-transparent"}`}>
              {t.label} <span className="ml-1 text-[9px] opacity-60">{t.count}</span>
            </button>
          ))}
        </div>

        {tab === "devices" && (
          <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
            <table className="w-full text-xs">
              <thead><tr className="text-gray-500 border-b border-gray-800">
                <th className="text-left px-4 py-2">Device Name</th><th className="text-left px-4 py-2">Society</th><th className="text-left px-4 py-2">Device ID</th><th className="text-left px-4 py-2">Status</th><th className="text-right px-4 py-2">Actions</th>
              </tr></thead>
              <tbody>
                {devices.length === 0 && <tr><td colSpan={5} className="text-center text-gray-600 py-8">No devices provisioned</td></tr>}
                {devices.map((d) => (
                  <tr key={d.id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="px-4 py-3 text-gray-200 font-semibold">{d.name}</td>
                    <td className="px-4 py-3 text-gray-400">{d.society_name || "--"}</td>
                    <td className="px-4 py-3 text-gray-500 font-mono text-[10px]">{d.id.slice(0,8)}...</td>
                    <td className="px-4 py-3"><span className="px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 text-[9px] font-bold">{d.status}</span></td>
                    <td className="px-4 py-3 text-right"><button onClick={() => deleteDevice(d.id)} className="text-red-400 hover:underline">Delete</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Add Device Modal */}
        {showDeviceModal && (
          <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setShowDeviceModal(false)}>
            <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
              <h3 className="text-sm font-bold text-white mb-4">Provision New Pi Device</h3>
              <div className="space-y-3">
                <input className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" placeholder="Device Name (e.g., Pi Controller 1)" value={deviceForm.name} onChange={(e) => setDeviceForm({ ...deviceForm, name: e.target.value })} />
                <select className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" value={deviceForm.society_id} onChange={(e) => setDeviceForm({ ...deviceForm, society_id: e.target.value })}>
                  {societies.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              </div>
              <div className="flex gap-2 mt-5">
                <button onClick={saveDevice} className="flex-1 py-2 bg-emerald-500 text-black text-xs font-bold rounded hover:bg-emerald-600">Generate Credentials</button>
                <button onClick={() => setShowDeviceModal(false)} className="flex-1 py-2 border border-gray-700 text-gray-400 text-xs rounded hover:border-gray-500">Cancel</button>
              </div>
            </div>
          </div>
        )}

        {/* Credentials Display Modal */}
        {showKeyModal && (
          <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-[60]" onClick={() => setShowKeyModal(false)}>
            <div className="bg-gray-900 border border-emerald-500/40 rounded-xl p-6 w-full max-w-sm text-center" onClick={(e) => e.stopPropagation()}>
              <div className="text-3xl mb-3">🔑</div>
              <h3 className="text-sm font-bold text-white mb-1">Pi Credentials Generated!</h3>
              <p className="text-[10px] text-gray-500 mb-4">Copy these to the Pi's config.json. The API key will not be shown again.</p>
              
              <div className="text-left space-y-2 mb-4">
                <div>
                  <label className="text-[9px] text-gray-500 uppercase">Device ID</label>
                  <div className="bg-black border border-gray-700 rounded p-2 flex items-center justify-between">
                    <span className="text-cyan-400 font-mono text-[10px] break-all">{newDeviceCreds.id}</span>
                    <button onClick={() => copyText(newDeviceCreds.id)} className="text-gray-500 hover:text-white text-[10px] ml-2">Copy</button>
                  </div>
                </div>
                <div>
                  <label className="text-[9px] text-gray-500 uppercase">API Key</label>
                  <div className="bg-black border border-amber-500/30 rounded p-2 flex items-center justify-between">
                    <span className="text-amber-400 font-mono text-[10px] break-all">{newDeviceCreds.key}</span>
                    <button onClick={() => copyText(newDeviceCreds.key)} className="text-gray-500 hover:text-white text-[10px] ml-2">Copy</button>
                  </div>
                </div>
              </div>
              <button onClick={() => setShowKeyModal(false)} className="w-full py-2 bg-gray-800 text-gray-400 text-xs rounded hover:bg-gray-700 border border-gray-700">Close</button>
            </div>
          </div>
        )}

        {toast && <div className={`fixed bottom-4 right-4 px-4 py-2 rounded-lg border z-[100] ${toast.ok ? "border-emerald-500/50 text-emerald-400" : "border-red-500/50 text-red-400"} bg-gray-900`}>{toast.msg}</div>}
      </main>
    </div>
  );
}