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
  const [fwVersions, setFwVersions] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  
  // Modals
  const [showSocModal, setShowSocModal] = useState(false);
  const [showUserModal, setShowUserModal] = useState(false);
  const [showDeviceModal, setShowDeviceModal] = useState(false);
  const [showKeyModal, setShowKeyModal] = useState(false);
  
  // Forms
  const [editSoc, setEditSoc] = useState<any>(null);
  const [socForm, setSocForm] = useState({ name: "", location: "", plan: "Basic", tailscale_ip: "", pi_port: "5000", society_code: "" });
  const [userForm, setUserForm] = useState({ email: "", name: "", password: "", role: "society_admin", society_id: "" });
  
  const [editDeviceId, setEditDeviceId] = useState<string | null>(null);
  const [deviceForm, setDeviceForm] = useState({ name: "", society_id: "" });
  const [newDeviceCreds, setNewDeviceCreds] = useState({ id: "", key: "" });

  const [toast, setToast] = useState<any>(null);

  useEffect(() => { if (localStorage.getItem("role") !== "super_admin") router.push("/login"); }, [router]);

  const fetchData = async () => {
    try {
      const [sRes, uRes, dRes, fRes] = await Promise.all([
        api.get("/api/super-admin/societies"), 
        api.get("/api/super-admin/users"),
        api.get("/api/super-admin/devices"),
        api.get("/api/super-admin/firmware/versions")
      ]);
      setSocieties(sRes.data); 
      setUsers(uRes.data); 
      setDevices(dRes.data);
      setFwVersions(fRes.data || []);
    } catch {}
    setLoading(false);
  };

  useEffect(() => { fetchData(); }, []);
  
  const showToast = (msg: string, ok: boolean) => { setToast({ msg, ok }); setTimeout(() => setToast(null), 3000); };
  const copyText = (t: string) => { navigator.clipboard.writeText(t); showToast("Copied!", true); };

  // --- Society Actions ---
  const saveSoc = async () => {
    try {
      await api.post("/api/super-admin/societies/save", editSoc ? { id: editSoc.id, ...socForm } : socForm);
      setShowSocModal(false); setEditSoc(null);
      setSocForm({ name: "", location: "", plan: "Basic", tailscale_ip: "", pi_port: "5000", society_code: "" });
      fetchData(); showToast("Society saved", true);
    } catch { showToast("Failed", false); }
  };
  const deleteSoc = async (id: string) => { if (!confirm("Delete this society?")) return; try { await api.post("/api/super-admin/societies/delete", { id }); fetchData(); showToast("Deleted", true); } catch { showToast("Failed", false); } };
  const openEditSoc = (s: any) => { setEditSoc(s); setSocForm({ name: s.name, location: s.location, plan: s.plan, tailscale_ip: s.tailscale_ip || "", pi_port: String(s.pi_port || 5000), society_code: s.society_code || "" }); setShowSocModal(true); };

  // --- User Actions ---
  const saveUser = async () => {
    try {
      await api.post("/api/super-admin/users/save", userForm);
      setShowUserModal(false); setUserForm({ email: "", name: "", password: "", role: "society_admin", society_id: "" });
      fetchData(); showToast("User saved", true);
    } catch { showToast("Failed", false); }
  };
  const deleteUser = async (id: string) => { if (!confirm("Delete?")) return; try { await api.post("/api/super-admin/users/delete", { id }); fetchData(); showToast("Deleted", true); } catch { showToast("Failed", false); } };

  // --- Device Actions ---
  const saveDevice = async () => {
    try {
      const payload = { ...deviceForm, id: editDeviceId };
      const res = await api.post("/api/super-admin/devices/save", payload);
      setShowDeviceModal(false);
      
      // If it was a new device, show the credentials
      if (!editDeviceId && res.data.api_key) {
        setNewDeviceCreds({ id: res.data.device_id, key: res.data.api_key });
        setShowKeyModal(true);
      }
      
      setEditDeviceId(null);
      setDeviceForm({ name: "", society_id: "" });
      fetchData(); showToast("Pi Device Saved", true);
    } catch { showToast("Failed", false); }
  };

  const editDevice = (d: any) => {
    setEditDeviceId(d.id);
    setDeviceForm({ name: d.name, society_id: d.society_id });
    setShowDeviceModal(true);
  };

  const deleteDevice = async (id: string) => { if (!confirm("Delete this Pi Device?")) return; try { await api.post("/api/super-admin/devices/delete", { id }); fetchData(); showToast("Deleted", true); } catch { showToast("Failed", false); } };

  if (loading) return <div className="flex h-screen items-center justify-center text-gray-500">Loading...</div>;

  const tabs = [
    { key: "societies", label: "Societies", count: societies.length },
    { key: "users", label: "Users", count: users.length },
    { key: "devices", label: "Devices", count: devices.length },
    { key: "firmware", label: "Firmware", count: fwVersions.length }
  ];

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar role="super_admin" />
      <main className="flex-1 overflow-y-auto p-6 pt-20" style={{ background: "#0a0e17" }}>
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-white">Super Admin</h1>
            <p className="text-xs text-gray-500">{societies.length} societies, {users.length} users, {devices.length} pi devices</p>
          </div>
          {tab === "societies" && <button onClick={() => { setEditSoc(null); setSocForm({ name: "", location: "", plan: "Basic", tailscale_ip: "", pi_port: "5000", society_code: "" }); setShowSocModal(true); }} className="px-4 py-2 bg-cyan-500 text-black text-xs font-bold rounded-lg hover:bg-cyan-600">+ Add Society</button>}
          {tab === "users" && <button onClick={() => { setUserForm({ email: "", name: "", password: "", role: "society_admin", society_id: societies[0]?.id || "" }); setShowUserModal(true); }} className="px-4 py-2 bg-emerald-500 text-black text-xs font-bold rounded-lg hover:bg-emerald-600">+ Add User</button>}
          {tab === "devices" && <button onClick={() => { setEditDeviceId(null); setDeviceForm({ name: "", society_id: societies[0]?.id || "" }); setShowDeviceModal(true); }} className="px-4 py-2 bg-emerald-500 text-black text-xs font-bold rounded-lg hover:bg-emerald-600">+ Add Pi Device</button>}
        </div>

        <div className="flex gap-1 mb-6 bg-gray-900 p-1 rounded-lg w-fit">
          {tabs.map((t) => (
            <button key={t.key} onClick={() => setTab(t.key as any)} className={`px-4 py-2 rounded-md text-xs font-semibold transition-all ${tab === t.key ? "bg-cyan-500/20 text-cyan-400 border border-cyan-500/30" : "text-gray-500 hover:text-gray-300 border border-transparent"}`}>
              {t.label} <span className="ml-1 text-[9px] opacity-60">{t.count}</span>
            </button>
          ))}
        </div>

        {/* Societies Tab */}
        {tab === "societies" && (
          <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
            <table className="w-full text-xs">
              <thead><tr className="text-gray-500 border-b border-gray-800"><th className="text-left px-4 py-2">Name</th><th className="text-left px-4 py-2">Location</th><th className="text-left px-4 py-2">Plan</th><th className="text-left px-4 py-2">Code</th><th className="text-left px-4 py-2">Pi</th><th className="text-left px-4 py-2">FW</th><th className="text-left px-4 py-2">Wing</th><th className="text-right px-4 py-2">Actions</th></tr></thead>
              <tbody>
                {societies.map((s) => (
                  <tr key={s.id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="px-4 py-3 text-gray-200 font-semibold">{s.name}</td>
                    <td className="px-4 py-3 text-gray-400">{s.location}</td>
                    <td className="px-4 py-3"><span className={`px-2 py-0.5 rounded-full text-[9px] font-bold ${s.plan === "Professional" ? "bg-cyan-500/15 text-cyan-400" : "bg-gray-700 text-gray-400"}`}>{s.plan}</span></td>
                    <td className="px-4 py-3 text-gray-500 font-mono">{s.society_code || "--"}</td>
                    <td className="px-4 py-3"><span className={`flex items-center gap-1 ${s.pi_online ? "text-emerald-400" : "text-red-400"}`}><span className={`w-1.5 h-1.5 rounded-full ${s.pi_online ? "bg-emerald-400" : "bg-red-400"}`} />{s.pi_online ? "Online" : "Offline"}</span></td>
                    <td className="px-4 py-3 text-gray-500 font-mono text-[10px]">{s.firmware_version || "--"}</td>
                    <td className="px-4 py-3 text-cyan-400 font-mono">{s.active_wing || "--"}</td>
                    <td className="px-4 py-3 text-right">
                      <button onClick={() => router.push(`/society/${s.id}`)} className="text-cyan-400 hover:underline mr-2 font-semibold">Details</button>
                      <button onClick={() => openEditSoc(s)} className="text-gray-400 hover:text-cyan-400 hover:underline mr-2">Edit</button>
                      <button onClick={() => deleteSoc(s.id)} className="text-red-400 hover:underline">Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Users Tab */}
        {tab === "users" && (
          <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
            <table className="w-full text-xs">
              <thead><tr className="text-gray-500 border-b border-gray-800"><th className="text-left px-4 py-2">Email</th><th className="text-left px-4 py-2">Name</th><th className="text-left px-4 py-2">Role</th><th className="text-left px-4 py-2">Society</th><th className="text-right px-4 py-2">Actions</th></tr></thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="px-4 py-3 text-gray-200">{u.email}</td>
                    <td className="px-4 py-3 text-gray-400">{u.name}</td>
                    <td className="px-4 py-3"><span className={`px-2 py-0.5 rounded-full text-[9px] font-bold ${u.role === "super_admin" ? "bg-amber-500/15 text-amber-400" : u.role === "society_admin" ? "bg-cyan-500/15 text-cyan-400" : "bg-gray-700 text-gray-400"}`}>{u.role.replace("_", " ")}</span></td>
                    <td className="px-4 py-3 text-gray-500">{u.society_name}</td>
                    <td className="px-4 py-3 text-right"><button onClick={() => deleteUser(u.id)} className="text-red-400 hover:underline">Delete</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Devices Tab */}
        {tab === "devices" && (
          <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
            <table className="w-full text-xs">
              <thead><tr className="text-gray-500 border-b border-gray-800">
                <th className="text-left px-4 py-2">Device Name</th><th className="text-left px-4 py-2">Device ID</th><th className="text-left px-4 py-2">Status</th><th className="text-right px-4 py-2">Actions</th>
              </tr></thead>
              <tbody>
                {devices.map((d) => (
                  <tr key={d.id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="px-4 py-3 text-gray-200 font-semibold">{d.name}</td>
                    <td className="px-4 py-3 text-gray-500 font-mono text-[10px]">{d.id.slice(0,8)}...</td>
                    <td className="px-4 py-3"><span className="px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 text-[9px] font-bold">{d.status}</span></td>
                    <td className="px-4 py-3 text-right">
                      <button onClick={() => editDevice(d)} className="text-gray-400 hover:text-cyan-400 hover:underline mr-2">Edit</button>
                      <button onClick={() => deleteDevice(d.id)} className="text-red-400 hover:underline">Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Society Modal */}
        {showSocModal && (
          <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setShowSocModal(false)}>
            <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
              <h3 className="text-sm font-bold text-white mb-4">{editSoc ? "Edit Society" : "Add Society"}</h3>
              <div className="space-y-3">
                <input className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" placeholder="Name" value={socForm.name} onChange={(e) => setSocForm({ ...socForm, name: e.target.value })} />
                <input className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" placeholder="Location" value={socForm.location} onChange={(e) => setSocForm({ ...socForm, location: e.target.value })} />
                <select className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" value={socForm.society_code} onChange={(e) => setSocForm({ ...socForm, society_code: e.target.value })}>
                  <option value="">Select Society Code</option>
                  <option value="prestine">prestine</option>
                  <option value="green_heights">green_heights</option>
                  <option value="sunshine">sunshine</option>
                  <option value="sai_residency">sai_residency</option>
                </select>
                <input className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" placeholder="Tailscale IP" value={socForm.tailscale_ip} onChange={(e) => setSocForm({ ...socForm, tailscale_ip: e.target.value })} />
              </div>
              <div className="flex gap-2 mt-5">
                <button onClick={saveSoc} className="flex-1 py-2 bg-cyan-500 text-black text-xs font-bold rounded hover:bg-cyan-600">Save</button>
                <button onClick={() => setShowSocModal(false)} className="flex-1 py-2 border border-gray-700 text-gray-400 text-xs rounded hover:border-gray-500">Cancel</button>
              </div>
            </div>
          </div>
        )}

        {/* User Modal */}
        {showUserModal && (
          <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setShowUserModal(false)}>
            <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
              <h3 className="text-sm font-bold text-white mb-4">Add User</h3>
              <div className="space-y-3">
                <input className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" placeholder="Email" value={userForm.email} onChange={(e) => setUserForm({ ...userForm, email: e.target.value })} />
                <input className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" placeholder="Name" value={userForm.name} onChange={(e) => setUserForm({ ...userForm, name: e.target.value })} />
                <input type="password" className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" placeholder="Password" value={userForm.password} onChange={(e) => setUserForm({ ...userForm, password: e.target.value })} />
                <select className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" value={userForm.role} onChange={(e) => setUserForm({ ...userForm, role: e.target.value })}>
                  <option value="society_admin">Society Admin</option>
                  <option value="member">Member</option>
                </select>
                <select className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" value={userForm.society_id} onChange={(e) => setUserForm({ ...userForm, society_id: e.target.value })}>
                  {societies.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              </div>
              <div className="flex gap-2 mt-4">
                <button onClick={saveUser} className="flex-1 py-2 bg-emerald-500 text-black text-xs font-bold rounded hover:bg-emerald-600">Save</button>
                <button onClick={() => setShowUserModal(false)} className="flex-1 py-2 border border-gray-700 text-gray-400 text-xs rounded hover:border-gray-500">Cancel</button>
              </div>
            </div>
          </div>
        )}

        {/* Device Modal */}
        {showDeviceModal && (
          <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setShowDeviceModal(false)}>
            <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
              <h3 className="text-sm font-bold text-white mb-4">{editDeviceId ? "Edit Pi Device" : "Provision New Pi Device"}</h3>
              <div className="space-y-3">
                <input className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" placeholder="Device Name (e.g., Pi Controller 1)" value={deviceForm.name} onChange={(e) => setDeviceForm({ ...deviceForm, name: e.target.value })} />
                <select className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" value={deviceForm.society_id} onChange={(e) => setDeviceForm({ ...deviceForm, society_id: e.target.value })}>
                  {societies.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              </div>
              <div className="flex gap-2 mt-5">
                <button onClick={saveDevice} className="flex-1 py-2 bg-emerald-500 text-black text-xs font-bold rounded hover:bg-emerald-600">{editDeviceId ? "Update Device" : "Generate Credentials"}</button>
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