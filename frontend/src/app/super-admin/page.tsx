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
  
  const [showSocModal, setShowSocModal] = useState(false);
  const [showUserModal, setShowUserModal] = useState(false);
  const [showDeviceModal, setShowDeviceModal] = useState(false);
  const [showKeyModal, setShowKeyModal] = useState(false);
  
  const [editSoc, setEditSoc] = useState<any>(null);
  const [socForm, setSocForm] = useState({ name: "", location: "", device_id: "", wings: {} as Record<string, { name: string; disabled: boolean; targetDays: number }> });
  const [userForm, setUserForm] = useState({ email: "", name: "", password: "", role: "society_admin", society_id: "" });
  
  const [editDeviceId, setEditDeviceId] = useState<string | null>(null);
  const [deviceForm, setDeviceForm] = useState({ name: "", society_id: "" });
  const [newDeviceCreds, setNewDeviceCreds] = useState({ id: "", key: "" });

  const [toast, setToast] = useState<any>(null);

  const WING_CODES = ["A", "B", "C", "D", "E", "F", "G", "H"];

  useEffect(() => { if (localStorage.getItem("role") !== "super_admin") router.push("/login"); }, [router]);

  const fetchData = async () => {
    // PRODUCTION FIX: Use Promise.allSettled so one failed API doesn't wipe out the whole dashboard
    const [sRes, uRes, dRes, fRes] = await Promise.allSettled([
      api.get("/api/super-admin/societies"), 
      api.get("/api/super-admin/users"),
      api.get("/api/super-admin/devices"),
      api.get("/api/super-admin/firmware/versions")
    ]);
    
    if (sRes.status === 'fulfilled') setSocieties(sRes.value.data); 
    if (uRes.status === 'fulfilled') setUsers(uRes.value.data); 
    if (dRes.status === 'fulfilled') setDevices(dRes.value.data);
    if (fRes.status === 'fulfilled') setFwVersions(fRes.value.data || []);
    
    setLoading(false);
  };

  useEffect(() => { fetchData(); }, []);
  
  const showToast = (msg: string, ok: boolean) => { setToast({ msg, ok }); setTimeout(() => setToast(null), 3000); };
  const copyText = (t: string) => { navigator.clipboard.writeText(t); showToast("Copied!", true); };

  const initWings = () => {
    const wings: Record<string, { name: string; disabled: boolean; targetDays: number }> = {};
    // PRODUCTION FIX: Initialize all 8 wings. Enabled by default, 10 target days.
    WING_CODES.forEach(code => {
      wings[code] = { name: `Wing ${code}`, disabled: false, targetDays: 10 };
    });
    return wings;
  };

  const saveSoc = async () => {
    try {
      const wing_names: Record<string, string> = {};
      const wing_disabled: Record<string, boolean> = {};
      const wing_target_days: Record<string, number> = {};
      
      Object.entries(socForm.wings).forEach(([code, data]) => {
        wing_names[code] = data.name;
        wing_disabled[code] = data.disabled;
        wing_target_days[code] = data.targetDays;
      });

      const payload = { ...socForm, wing_names, wing_disabled, wing_target_days };
      await api.post("/api/super-admin/societies/save", editSoc ? { id: editSoc.id, ...payload } : payload);
      setShowSocModal(false); setEditSoc(null);
      setSocForm({ name: "", location: "", device_id: "", wings: initWings() });
      fetchData(); showToast("Society saved", true);
    } catch { showToast("Failed", false); }
  };
  
  const deleteSoc = async (id: string) => { if (!confirm("Delete this society?")) return; try { await api.post("/api/super-admin/societies/delete", { id }); fetchData(); showToast("Deleted", true); } catch { showToast("Failed", false); } };
  const openEditSoc = (s: any) => { 
    const currentWings = initWings();
    if (s.wings) {
      Object.keys(s.wings).forEach(code => {
        currentWings[code] = { 
          name: s.wings[code].name, 
          disabled: s.wings[code].disabled,
          targetDays: s.wings[code].target_days || 10
        };
      });
    }
    setEditSoc(s); 
    setSocForm({ name: s.name, location: s.location, device_id: s.device_id || "", wings: currentWings }); 
    setShowSocModal(true); 
  };

  const saveUser = async () => {
    try {
      await api.post("/api/super-admin/users/save", userForm);
      setShowUserModal(false); setUserForm({ email: "", name: "", password: "", role: "society_admin", society_id: "" });
      fetchData(); showToast("User saved", true);
    } catch { showToast("Failed", false); }
  };
  const deleteUser = async (id: string) => { if (!confirm("Delete?")) return; try { await api.post("/api/super-admin/users/delete", { id }); fetchData(); showToast("Deleted", true); } catch { showToast("Failed", false); } };

  const saveDevice = async () => {
    try {
      const payload = { ...deviceForm, id: editDeviceId };
      const res = await api.post("/api/super-admin/devices/save", payload);
      setShowDeviceModal(false);
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
    setDeviceForm({ name: d.name, society_id: d.society_id || "" });
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

  const availableDevices = devices.filter(d => !d.society_id || (editSoc && d.society_id === editSoc.id));

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar role="super_admin" />
      <main className="flex-1 overflow-y-auto p-6 pt-20" style={{ background: "#0a0e17" }}>
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-white">Super Admin</h1>
            <p className="text-xs text-gray-500">{societies.length} societies, {users.length} users, {devices.length} pi devices</p>
          </div>
          {tab === "societies" && <button onClick={() => { setEditSoc(null); setSocForm({ name: "", location: "", device_id: "", wings: initWings() }); setShowSocModal(true); }} className="px-4 py-2 bg-cyan-500 text-black text-xs font-bold rounded-lg hover:bg-cyan-600">+ Add Society</button>}
          {tab === "users" && <button onClick={() => { setUserForm({ email: "", name: "", password: "", role: "society_admin", society_id: societies[0]?.id || "" }); setShowUserModal(true); }} className="px-4 py-2 bg-emerald-500 text-black text-xs font-bold rounded-lg hover:bg-emerald-600">+ Add User</button>}
          {tab === "devices" && <button onClick={() => { setEditDeviceId(null); setDeviceForm({ name: "", society_id: "" }); setShowDeviceModal(true); }} className="px-4 py-2 bg-emerald-500 text-black text-xs font-bold rounded-lg hover:bg-emerald-600">+ Add Pi Device</button>}
        </div>

        <div className="flex gap-1 mb-6 bg-gray-900 p-1 rounded-lg w-fit">
          {tabs.map((t) => (
            <button key={t.key} onClick={() => setTab(t.key as any)} className={`px-4 py-2 rounded-md text-xs font-semibold transition-all ${tab === t.key ? "bg-cyan-500/20 text-cyan-400 border border-cyan-500/30" : "text-gray-500 hover:text-gray-300 border border-transparent"}`}>
              {t.label} <span className="ml-1 text-[9px] opacity-60">{t.count}</span>
            </button>
          ))}
        </div>

        {tab === "societies" && (
          <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
            <table className="w-full text-xs">
              <thead><tr className="text-gray-500 border-b border-gray-800"><th className="text-left px-4 py-2">Name</th><th className="text-left px-4 py-2">Location</th><th className="text-left px-4 py-2">Pi</th><th className="text-left px-4 py-2">FW</th><th className="text-left px-4 py-2">Wing</th><th className="text-right px-4 py-2">Actions</th></tr></thead>
              <tbody>
                {societies.map((s) => (
                  <tr key={s.id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="px-4 py-3 text-gray-200 font-semibold">{s.name}</td>
                    <td className="px-4 py-3 text-gray-400">{s.location}</td>
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
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 rounded-full text-[9px] font-bold ${d.status === 'ASSIGNED' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-gray-700 text-gray-400'}`}>{d.status}</span>
                    </td>
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
            <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 w-full max-w-md max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
              <h3 className="text-sm font-bold text-white mb-4">{editSoc ? "Edit Society" : "Add Society"}</h3>
              <div className="space-y-3">
                <input className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" placeholder="Society Name" value={socForm.name} onChange={(e) => setSocForm({ ...socForm, name: e.target.value })} />
                <input className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" placeholder="Location" value={socForm.location} onChange={(e) => setSocForm({ ...socForm, location: e.target.value })} />
                
                <div>
                  <label className="text-[9px] text-gray-500 uppercase">Assign Pi Device</label>
                  <select className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" value={socForm.device_id} onChange={(e) => setSocForm({ ...socForm, device_id: e.target.value })}>
                    <option value="">Unassigned (Inventory)</option>
                    {availableDevices.map((d) => <option key={d.id} value={d.id}>{d.name} ({d.id.slice(0,8)}...)</option>)}
                  </select>
                </div>

                <div className="pt-2 border-t border-gray-800 mt-2">
                  <label className="text-[9px] text-gray-500 uppercase block mb-2">Wing Configuration (A-H)</label>
                  <div className="space-y-2">
                    {WING_CODES.map(code => (
                      <div key={code} className="flex items-center gap-2">
                        <span className="text-gray-500 font-mono text-[10px] w-4">{code}</span>
                        <input 
                          className="flex-1 px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-[10px] text-gray-200 focus:outline-none focus:border-cyan-500" 
                          placeholder={`Wing ${code} Name`} 
                          value={socForm.wings[code]?.name || ""} 
                          onChange={(e) => setSocForm({ ...socForm, wings: { ...socForm.wings, [code]: { ...socForm.wings[code], name: e.target.value } }})} 
                        />
                        <input 
                          type="number"
                          className="w-16 px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-[10px] text-gray-200 focus:outline-none focus:border-cyan-500" 
                          placeholder="Days" 
                          value={socForm.wings[code]?.targetDays ?? 10} 
                          onChange={(e) => setSocForm({ ...socForm, wings: { ...socForm.wings, [code]: { ...socForm.wings[code], targetDays: parseInt(e.target.value) || 0 } }})} 
                        />
                        <button 
                          className={`px-2 py-1 text-[9px] font-bold rounded ${socForm.wings[code]?.disabled ? 'bg-gray-700 text-gray-400' : 'bg-emerald-500/15 text-emerald-400'}`}
                          onClick={() => setSocForm({ ...socForm, wings: { ...socForm.wings, [code]: { ...socForm.wings[code], disabled: !socForm.wings[code]?.disabled } }})}
                        >
                          {socForm.wings[code]?.disabled ? "DISABLED" : "ENABLED"}
                        </button>
                      </div>
                    ))}
                  </div>
                </div>

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
                <input className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" placeholder="Device Name (e.g., Pi-001)" value={deviceForm.name} onChange={(e) => setDeviceForm({ ...deviceForm, name: e.target.value })} />
                <select className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" value={deviceForm.society_id} onChange={(e) => setDeviceForm({ ...deviceForm, society_id: e.target.value })}>
                  <option value="">Unassigned (Inventory)</option>
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