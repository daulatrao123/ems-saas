"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import api from "@/lib/api";
import Sidebar from "@/components/Sidebar";

function generateKey() {
  const c = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  let k = "EMS-";
  for (let i = 0; i < 4; i++) { if (i > 0) k += "-"; for (let j = 0; j < 4; j++) k += c[Math.floor(Math.random() * c.length)]; }
  return k;
}

export default function SuperAdminDashboard() {
  const router = useRouter();
  const [tab, setTab] = useState<"societies" | "users" | "firmware">("societies");
  const [societies, setSocieties] = useState<any[]>([]);
  const [users, setUsers] = useState<any[]>([]);
  const [fwVersions, setFwVersions] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showSocModal, setShowSocModal] = useState(false);
  const [showUserModal, setShowUserModal] = useState(false);
  const [showKeyModal, setShowKeyModal] = useState(false);
  const [generatedKey, setGeneratedKey] = useState("");
  const [editSoc, setEditSoc] = useState<any>(null);
  const [socForm, setSocForm] = useState({ name: "", location: "", plan: "Basic", tailscale_ip: "", pi_port: "5000", api_key: "", society_code: "" });
  const [userForm, setUserForm] = useState({ email: "", name: "", password: "", role: "society_admin", society_id: "" });
  const [fwForm, setFwForm] = useState({ version: "", code: "", changelog: "", forced: false });
  const [toast, setToast] = useState<any>(null);

  useEffect(() => { if (localStorage.getItem("role") !== "super_admin") router.push("/login"); }, [router]);

  const fetchData = async () => {
    try {
      const [sRes, uRes, fRes] = await Promise.all([api.get("/api/super-admin/societies"), api.get("/api/super-admin/users"), api.get("/api/super-admin/firmware/versions")]);
      setSocieties(sRes.data); setUsers(uRes.data); setFwVersions(fRes.data);
    } catch {}
    setLoading(false);
  };

  useEffect(() => { fetchData(); }, []);
  const showToast = (msg: string, ok: boolean) => { setToast({ msg, ok }); setTimeout(() => setToast(null), 3000); };
  const handleGenerateKey = () => { const k = generateKey(); setSocForm({ ...socForm, api_key: k }); setGeneratedKey(k); setShowKeyModal(true); };
  const copyText = (t: string) => { navigator.clipboard.writeText(t); showToast("Copied!", true); };

  const saveSoc = async () => {
    try {
      const res = await api.post("/api/super-admin/societies/save", editSoc ? { id: editSoc.id, ...socForm } : socForm);
      if (res.data.message === "Saved") { setShowSocModal(false); setEditSoc(null); setSocForm({ name: "", location: "", plan: "Basic", tailscale_ip: "", pi_port: "5000", api_key: "", society_code: "" }); fetchData(); showToast("Society saved", true); }
    } catch { showToast("Failed", false); }
  };
  const deleteSoc = async (id: string) => { if (!confirm("Delete this society?")) return; try { await api.post("/api/super-admin/societies/delete", { id }); fetchData(); showToast("Deleted", true); } catch { showToast("Failed", false); } };
  const saveUser = async () => {
    try {
      const res = await api.post("/api/super-admin/users/save", userForm);
      if (res.data.message === "Saved") { setShowUserModal(false); setUserForm({ email: "", name: "", password: "", role: "society_admin", society_id: "" }); fetchData(); showToast("User saved", true); }
    } catch { showToast("Failed", false); }
  };
  const deleteUser = async (id: string) => { if (!confirm("Delete?")) return; try { await api.post("/api/super-admin/users/delete", { id }); fetchData(); showToast("Deleted", true); } catch { showToast("Failed", false); } };
  const openEditSoc = (s: any) => { setEditSoc(s); setSocForm({ name: s.name, location: s.location, plan: s.plan, tailscale_ip: s.tailscale_ip || "", pi_port: String(s.pi_port || 5000), api_key: s.api_key || "", society_code: s.society_code || "" }); setShowSocModal(true); };

  const saveFw = async () => {
    if (!fwForm.version.trim() || !fwForm.code.trim()) { showToast("Version and code required", false); return; }
    try { await api.post("/api/super-admin/firmware/save", fwForm); setFwForm({ version: "", code: "", changelog: "", forced: false }); fetchData(); showToast("Firmware saved", true); } catch { showToast("Failed", false); }
  };
  const deleteFw = async (v: string) => { if (!confirm("Delete version " + v + "?")) return; try { await api.post("/api/super-admin/firmware/delete", { version: v }); fetchData(); showToast("Deleted", true); } catch { showToast("Failed", false); } };
  const forceFw = async (v: string) => { try { await api.post("/api/super-admin/firmware/force", { version: v }); fetchData(); showToast(v + " forced", true); } catch { showToast("Failed", false); } };

  if (loading) return <div className="flex h-screen items-center justify-center text-gray-500">Loading...</div>;

  const tabs = [{ key: "societies", label: "Societies", count: societies.length }, { key: "users", label: "Users", count: users.length }, { key: "firmware", label: "Firmware", count: fwVersions.length }];

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar role="super_admin" />
      <main className="flex-1 overflow-y-auto p-6 pt-20" style={{ background: "#0a0e17" }}>
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-white">Super Admin</h1>
            <p className="text-xs text-gray-500">{societies.length} societies, {users.length} users</p>
          </div>
          <div className="flex gap-2">
            {tab === "societies" && <button onClick={() => { setEditSoc(null); setSocForm({ name: "", location: "", plan: "Basic", tailscale_ip: "", pi_port: "5000", api_key: "", society_code: "" }); setShowSocModal(true); }} className="px-4 py-2 bg-cyan-500 text-black text-xs font-bold rounded-lg hover:bg-cyan-600">+ Add Society</button>}
            {tab === "users" && <button onClick={() => { setUserForm({ email: "", name: "", password: "", role: "society_admin", society_id: societies[0]?.id || "" }); setShowUserModal(true); }} className="px-4 py-2 bg-emerald-500 text-black text-xs font-bold rounded-lg hover:bg-emerald-600">+ Add User</button>}
          </div>
        </div>

        <div className="flex gap-1 mb-6 bg-gray-900 p-1 rounded-lg w-fit">
          {tabs.map((t) => (
            <button key={t.key} onClick={() => setTab(t.key as any)} className={"px-4 py-2 rounded-md text-xs font-semibold transition-all " + (tab === t.key ? "bg-cyan-500/20 text-cyan-400 border border-cyan-500/30" : "text-gray-500 hover:text-gray-300 border border-transparent")}>{t.label} <span className="ml-1 text-[9px] opacity-60">{t.count}</span></button>
          ))}
        </div>

        {tab === "societies" && (
          <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead><tr className="text-gray-500 border-b border-gray-800">
                  <th className="text-left px-4 py-2">Name</th><th className="text-left px-4 py-2">Location</th><th className="text-left px-4 py-2">Plan</th><th className="text-left px-4 py-2">Code</th><th className="text-left px-4 py-2">API Key</th><th className="text-left px-4 py-2">Pi</th><th className="text-left px-4 py-2">FW</th><th className="text-left px-4 py-2">Wing</th><th className="text-right px-4 py-2">Actions</th>
                </tr></thead>
                <tbody>
                  {societies.length === 0 && <tr><td colSpan={9} className="text-center text-gray-600 py-8">No societies</td></tr>}
                  {societies.map((s) => (
                    <tr key={s.id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                      <td className="px-4 py-3 text-gray-200 font-semibold">{s.name}</td>
                      <td className="px-4 py-3 text-gray-400">{s.location}</td>
                      <td className="px-4 py-3"><span className={"px-2 py-0.5 rounded-full text-[9px] font-bold " + (s.plan === "Professional" ? "bg-cyan-500/15 text-cyan-400" : "bg-gray-700 text-gray-400")}>{s.plan}</span></td>
                      <td className="px-4 py-3 text-gray-500 font-mono">{s.society_code || "--"}</td>
                      <td className="px-4 py-3">
                        {s.api_key ? (
                          <button onClick={() => copyText(s.api_key)} className="flex items-center gap-1.5 text-amber-400 hover:text-amber-300 font-mono text-[10px]">
                            <span className="opacity-60">{s.api_key.slice(0, 7)}...</span>
                            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
                          </button>
                        ) : <span className="text-gray-700 text-[10px]">No key</span>}
                      </td>
                      <td className="px-4 py-3"><span className={"flex items-center gap-1 " + (s.pi_online ? "text-emerald-400" : "text-red-400")}><span className={"w-1.5 h-1.5 rounded-full " + (s.pi_online ? "bg-emerald-400" : "bg-red-400")} />{s.pi_online ? "Online" : "Offline"}</span></td>
                      <td className="px-4 py-3 text-gray-500 font-mono text-[10px]">{s.firmware_version || "--"}</td>
                      <td className="px-4 py-3 text-cyan-400 font-mono">{s.active_wing || "--"}</td>
                      <td className="px-4 py-3 text-right">
                        <button onClick={() => router.push("/society/" + s.id)} className="text-cyan-400 hover:underline mr-2 font-semibold">Details</button>
                        <button onClick={() => openEditSoc(s)} className="text-gray-400 hover:text-cyan-400 hover:underline mr-2">Edit</button>
                        <button onClick={() => deleteSoc(s.id)} className="text-red-400 hover:underline">Delete</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {tab === "users" && (
          <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead><tr className="text-gray-500 border-b border-gray-800"><th className="text-left px-4 py-2">Email</th><th className="text-left px-4 py-2">Name</th><th className="text-left px-4 py-2">Role</th><th className="text-left px-4 py-2">Society</th><th className="text-right px-4 py-2">Actions</th></tr></thead>
                <tbody>
                  {users.length === 0 && <tr><td colSpan={5} className="text-center text-gray-600 py-8">No users</td></tr>}
                  {users.map((u) => (
                    <tr key={u.id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                      <td className="px-4 py-3 text-gray-200">{u.email}</td>
                      <td className="px-4 py-3 text-gray-400">{u.name}</td>
                      <td className="px-4 py-3"><span className={"px-2 py-0.5 rounded-full text-[9px] font-bold " + (u.role === "super_admin" ? "bg-amber-500/15 text-amber-400" : u.role === "society_admin" ? "bg-cyan-500/15 text-cyan-400" : "bg-gray-700 text-gray-400")}>{u.role.replace("_", " ")}</span></td>
                      <td className="px-4 py-3 text-gray-500">{u.society_name}</td>
                      <td className="px-4 py-3 text-right"><button onClick={() => deleteUser(u.id)} className="text-red-400 hover:underline">Delete</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {tab === "firmware" && (
          <div className="space-y-6">
            <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-800 bg-gray-800/50"><h2 className="text-xs font-semibold text-gray-300">Upload Firmware</h2></div>
              <div className="p-5 space-y-4">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div><label className="text-[10px] text-gray-500 uppercase tracking-wider mb-1 block">Version</label><input className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500 font-mono" placeholder="e.g. 1.2.0" value={fwForm.version} onChange={(e) => setFwForm({ ...fwForm, version: e.target.value })} /></div>
                  <div className="flex items-end"><label className="flex items-center gap-2 cursor-pointer pb-2"><input type="checkbox" checked={fwForm.forced} onChange={(e) => setFwForm({ ...fwForm, forced: e.target.checked })} className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-red-500 focus:ring-red-500" /><span className="text-xs text-red-400 font-semibold">Force update all Pis</span></label></div>
                </div>
                <div><label className="text-[10px] text-gray-500 uppercase tracking-wider mb-1 block">Changelog</label><textarea className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500 h-16 resize-none" placeholder="What changed..." value={fwForm.changelog} onChange={(e) => setFwForm({ ...fwForm, changelog: e.target.value })} /></div>
                <div><label className="text-[10px] text-gray-500 uppercase tracking-wider mb-1 block">Python Code</label><textarea className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-emerald-400 font-mono focus:outline-none focus:border-cyan-500 h-64 resize-y" placeholder="Paste complete Pi firmware code..." value={fwForm.code} onChange={(e) => setFwForm({ ...fwForm, code: e.target.value })} /></div>
                <div className="flex gap-2"><button onClick={saveFw} className="px-6 py-2 bg-cyan-500 text-black text-xs font-bold rounded-lg hover:bg-cyan-600">Save Version</button><button onClick={() => setFwForm({ version: "", code: "", changelog: "", forced: false })} className="px-4 py-2 border border-gray-700 text-gray-500 text-xs rounded hover:border-gray-500">Clear</button></div>
              </div>
            </div>
            <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-800 bg-gray-800/50"><h2 className="text-xs font-semibold text-gray-300">All Versions</h2></div>
              <div className="divide-y divide-gray-800/50">
                {fwVersions.length === 0 && <div className="text-gray-600 text-xs text-center py-8">No versions</div>}
                {fwVersions.map((v) => (
                  <div key={v.version} className="px-5 py-4 flex items-center justify-between">
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-1"><span className="text-sm font-bold text-white font-mono">v{v.version}</span>{v.forced && <span className="px-2 py-0.5 rounded-full bg-red-500/15 text-red-400 text-[9px] font-bold border border-red-500/30">FORCED</span>}<span className="text-[9px] text-gray-600">{v.created_at ? v.created_at.replace("T", " ").split(".")[0] : ""}</span></div>
                      {v.changelog && <p className="text-[10px] text-gray-500">{v.changelog}</p>}
                    </div>
                    <div className="flex items-center gap-2 ml-4">
                      <button onClick={() => forceFw(v.version)} className={"px-3 py-1.5 text-[10px] font-semibold rounded " + (v.forced ? "bg-red-500/15 text-red-400 border border-red-500/30" : "border border-gray-700 text-gray-500 hover:border-amber-500 hover:text-amber-400")}>{v.forced ? "Forced" : "Force"}</button>
                      <button onClick={() => deleteFw(v.version)} className="px-3 py-1.5 border border-gray-700 text-gray-500 text-[10px] rounded hover:border-red-500 hover:text-red-400">Delete</button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Society Modal */}
        {showSocModal && (
          <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setShowSocModal(false)}>
            <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 w-full max-w-md max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
              <h3 className="text-sm font-bold text-white mb-4">{editSoc ? "Edit Society" : "Add Society"}</h3>
              <div className="space-y-3">
                <div><label className="text-[10px] text-gray-500 uppercase tracking-wider mb-1 block">Name</label><input className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" value={socForm.name} onChange={(e) => setSocForm({ ...socForm, name: e.target.value })} /></div>
                <div><label className="text-[10px] text-gray-500 uppercase tracking-wider mb-1 block">Location</label><input className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" value={socForm.location} onChange={(e) => setSocForm({ ...socForm, location: e.target.value })} /></div>
                <div><label className="text-[10px] text-gray-500 uppercase tracking-wider mb-1 block">Plan</label><select className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" value={socForm.plan} onChange={(e) => setSocForm({ ...socForm, plan: e.target.value })}><option>Basic</option><option>Professional</option><option>Enterprise</option></select></div>
                <div><label className="text-[10px] text-gray-500 uppercase tracking-wider mb-1 block">Society Code</label><input className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" value={socForm.society_code} onChange={(e) => setSocForm({ ...socForm, society_code: e.target.value })} /></div>
                <div><label className="text-[10px] text-gray-500 uppercase tracking-wider mb-1 block">Tailscale IP</label><input className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" value={socForm.tailscale_ip} onChange={(e) => setSocForm({ ...socForm, tailscale_ip: e.target.value })} /></div>
                <div>
                  <label className="text-[10px] text-gray-500 uppercase tracking-wider mb-1 block">Pi API Key</label>
                  <div className="flex gap-2">
                    <input
                      className="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-amber-400 font-mono focus:outline-none focus:border-amber-500"
                      value={socForm.api_key}
                      onChange={(e) => setSocForm({ ...socForm, api_key: e.target.value })}
                      placeholder="Paste existing key or generate new"
                    />
                    <button onClick={handleGenerateKey} className="px-3 py-2 bg-amber-500 hover:bg-amber-600 text-black text-[10px] font-bold rounded whitespace-nowrap">Generate</button>
                  </div>
                  <p className="text-[9px] text-gray-600 mt-1">Paste a key from an existing Pi device, or click Generate for a new one.</p>
                </div>
              </div>
              <div className="flex gap-2 mt-5"><button onClick={saveSoc} className="flex-1 py-2 bg-cyan-500 text-black text-xs font-bold rounded hover:bg-cyan-600">Save</button><button onClick={() => setShowSocModal(false)} className="flex-1 py-2 border border-gray-700 text-gray-400 text-xs rounded hover:border-gray-500">Cancel</button></div>
            </div>
          </div>
        )}

        {showKeyModal && (
          <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-[60]" onClick={() => setShowKeyModal(false)}>
            <div className="bg-gray-900 border border-amber-500/40 rounded-xl p-6 w-full max-w-sm text-center" onClick={(e) => e.stopPropagation()}>
              <div className="text-3xl mb-3">&#128273;</div>
              <h3 className="text-sm font-bold text-white mb-1">API Key Generated!</h3>
              <div className="bg-black border-2 border-amber-500/30 rounded-lg p-4 mb-4"><div className="text-amber-400 font-mono text-sm font-bold tracking-wider break-all">{generatedKey}</div></div>
              <button onClick={() => copyText(generatedKey)} className="w-full py-2.5 bg-amber-500 hover:bg-amber-600 text-black text-xs font-bold rounded-lg mb-2">Copy to Clipboard</button>
              <button onClick={() => setShowKeyModal(false)} className="w-full py-2 border border-gray-700 text-gray-400 text-xs rounded hover:border-gray-500">Close</button>
            </div>
          </div>
        )}

        {showUserModal && (
          <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setShowUserModal(false)}>
            <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
              <h3 className="text-sm font-bold text-white mb-4">Add User</h3>
              <div className="space-y-3">
                <input className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" placeholder="Email" value={userForm.email} onChange={(e) => setUserForm({ ...userForm, email: e.target.value })} />
                <input className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" placeholder="Name" value={userForm.name} onChange={(e) => setUserForm({ ...userForm, name: e.target.value })} />
                <input type="password" className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" placeholder="Password" value={userForm.password} onChange={(e) => setUserForm({ ...userForm, password: e.target.value })} />
                <select className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" value={userForm.role} onChange={(e) => setUserForm({ ...userForm, role: e.target.value })}><option value="society_admin">Society Admin</option><option value="member">Member</option></select>
                <select className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 focus:outline-none focus:border-cyan-500" value={userForm.society_id} onChange={(e) => setUserForm({ ...userForm, society_id: e.target.value })}>{societies.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
              </div>
              <div className="flex gap-2 mt-4"><button onClick={saveUser} className="flex-1 py-2 bg-emerald-500 text-black text-xs font-bold rounded hover:bg-emerald-600">Save</button><button onClick={() => setShowUserModal(false)} className="flex-1 py-2 border border-gray-700 text-gray-400 text-xs rounded hover:border-gray-500">Cancel</button></div>
            </div>
          </div>
        )}

        {toast && <div className={"fixed bottom-4 right-4 px-4 py-2 rounded-lg border z-50 " + (toast.ok ? "border-emerald-500/50 text-emerald-400" : "border-red-500/50 text-red-400") + " bg-gray-900"}>{toast.msg}</div>}
      </main>
    </div>
  );
}