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
    api.get("/api/auth/me").then((res) => {
      if (res.data.role !== "super_admin") { router.push("/login"); return; }
      api.get("/api/super-admin/societies").then((r) => setSocieties(r.data || [])).catch(() => setError("Failed to load societies")).finally(() => setLoading(false));
    }).catch(() => router.push("/login"));
  }, [router]);

  if (loading) return <div className="flex h-screen items-center justify-center text-gray-500">Loading Super Admin...</div>;
  return <div className="flex h-screen overflow-hidden bg-[#0a0e17]"><Sidebar role="super_admin" /><main className="flex-1 overflow-y-auto p-6 pt-20"><div className="mb-6"><h1 className="text-2xl font-bold text-white">Super Admin</h1><p className="text-xs text-gray-500">Society and Pi inventory overview</p></div>{error && <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-400">{error}</div>}<div className="space-y-4">{societies.map((s:any)=><div key={s.id} className="rounded-xl border border-gray-800 bg-gray-900/80 p-5"><div className="flex items-center justify-between"><div><h2 className="font-bold text-white">{s.name}</h2><p className="text-xs text-gray-500">{s.location || "—"} · {s.status}</p></div><span className="text-xs text-gray-400">Reset day: {s.reset_day}</span></div><div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">{(s.devices || []).map((d:any)=><div key={d.id} className="rounded-lg border border-gray-800 bg-gray-800/30 p-3"><div className="text-sm font-semibold text-white">{d.name}</div><div className="mt-1 text-[10px] text-gray-500">{d.id}</div><div className="mt-2 text-[10px] text-gray-400">{d.connected ? "ONLINE" : "OFFLINE"} · {d.status || "ASSIGNED"}</div></div>)}</div></div>)}</div></main></div>;
}
