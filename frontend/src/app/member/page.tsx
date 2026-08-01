"use client";
import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import api from "@/lib/api";
import Sidebar from "@/components/Sidebar";

interface WingData { name: string; used_days: number; target_days: number; clicks: number; }
interface PiEvent { id: number; ts: string; level: string; msg: string; }

export default function MemberDashboard() {
  const router = useRouter();
  const [piState, setPiState] = useState<any>(null);
  const [events, setEvents] = useState<PiEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [eventSince, setEventSince] = useState(0);
  const eventsEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => { if (localStorage.getItem("role") !== "member") router.push("/login"); }, [router]);

  const societyId = typeof window !== "undefined" ? (() => { try { const t = localStorage.getItem("token") || ""; if (t && t.includes(".")) return JSON.parse(atob(t.split(".")[1])).society_id || ""; return JSON.parse(t).society_id || ""; } catch(e) { return ""; } })() : "";

  const fetchPiState = useCallback(async () => {
    if (!societyId) return;
    try { const res = await api.get("/api/admin/pi-state?society_id=" + societyId); if (res.data.connected) setPiState(res.data); } catch {}
  }, [societyId]);

  const fetchEvents = useCallback(async () => {
    if (!societyId) return;
    try { const res = await api.get("/api/admin/pi-events?society_id=" + societyId + "&since=" + eventSince); if (res.data.events.length > 0) { setEvents((prev) => [...prev, ...res.data.events]); setEventSince(res.data.next); } } catch {}
  }, [societyId, eventSince]);

  useEffect(() => { fetchPiState(); fetchEvents(); setLoading(false); }, [fetchPiState, fetchEvents]);
  useEffect(() => { const i = setInterval(fetchPiState, 10000); return () => clearInterval(i); }, [fetchPiState]);
  useEffect(() => { if (eventsEndRef.current) eventsEndRef.current.scrollTop = eventsEndRef.current.scrollHeight; }, [events]);

  const wings = piState ? Object.entries(piState.wings as Record<string, WingData>) : [];
  const activeWing = piState?.active_wing || null;
  const isOnline = piState && (Date.now() - new Date(piState.last_sync).getTime()) < 360000;

  if (loading) return <div className="flex h-screen items-center justify-center text-gray-500">Loading...</div>;

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar role="member" />
      <main className="flex-1 overflow-y-auto p-6 pt-20" style={{ background: "#0a0e17" }}>
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-white">Member View</h1>
            <p className="text-xs text-gray-500">Read-only electricity status</p>
          </div>
          <div className={"flex items-center gap-2 px-4 py-2 rounded-full text-xs font-semibold " + (isOnline ? "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30" : "bg-red-500/15 text-red-400 border border-red-500/30")}>
            <span className={"w-2 h-2 rounded-full " + (isOnline ? "bg-emerald-400 animate-pulse" : "bg-red-400")} />
            {isOnline ? "Online" : "Offline"}
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
          {wings.length === 0 && <div className="text-gray-600 text-sm text-center py-12 col-span-full">No wing data available.</div>}
          {wings.map(([id, w]) => {
            const isActive = activeWing === id;
            const pct = w.target_days > 0 ? Math.min(100, Math.round((w.used_days / w.target_days) * 100)) : 0;
            const barColor = pct > 80 ? "bg-red-500" : pct > 50 ? "bg-amber-500" : "bg-emerald-500";
            const textColor = pct > 80 ? "text-red-400" : pct > 50 ? "text-amber-400" : "text-emerald-400";
            return (
              <div key={id} className={"rounded-xl p-5 border transition-all " + (isActive ? "border-emerald-500/40 bg-emerald-500/5" : "border-gray-800 bg-gray-900/80")}>
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-3">
                    <span className="text-2xl font-black text-cyan-400 font-mono">{id}</span>
                    <span className="text-sm font-semibold text-gray-200">{w.name}</span>
                  </div>
                  {isActive && <span className="text-[10px] px-2 py-0.5 rounded-full bg-cyan-500/15 text-cyan-400 border border-cyan-500/25 font-bold">ACTIVE</span>}
                </div>
                <div className="flex justify-between text-xs mb-2">
                  <span className="text-gray-500">Days: {w.used_days} / {w.target_days}</span>
                  <span className={"font-bold " + textColor}>{pct}%</span>
                </div>
                <div className="w-full h-2 bg-gray-800 rounded-full overflow-hidden">
                  <div className={"h-full rounded-full transition-all duration-500 " + barColor} style={{ width: pct + "%" }} />
                </div>
                <div className="text-[10px] text-gray-600 mt-2">{w.clicks} clicks</div>
              </div>
            );
          })}
        </div>

        {piState && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
            {[
              { label: "Reset Day", value: piState.reset_day + "th", color: "text-amber-400" },
              { label: "Firmware", value: piState.firmware_version, color: "text-purple-400" },
              { label: "CPU Temp", value: piState.cpu_temp + "\u00B0C", color: piState.cpu_temp > 70 ? "text-red-400" : "text-blue-400" },
              { label: "Uptime", value: Math.floor(piState.uptime_seconds / 3600) + "h " + Math.floor((piState.uptime_seconds % 3600) / 60) + "m", color: "text-emerald-400" },
            ].map((s) => (
              <div key={s.label} className="bg-gray-900/80 border border-gray-800 rounded-lg p-3">
                <div className="text-[9px] text-gray-500 uppercase tracking-wider">{s.label}</div>
                <div className={"text-sm font-bold mt-0.5 " + s.color}>{s.value}</div>
              </div>
            ))}
          </div>
        )}

        <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-800 bg-gray-800/50 flex items-center justify-between">
            <h2 className="text-xs font-semibold text-gray-300">System Events</h2>
            <span className="text-[9px] text-gray-600">{events.length} events</span>
          </div>
          <div ref={eventsEndRef} className="max-h-[250px] overflow-y-auto p-2 space-y-0.5 font-mono">
            {events.length === 0 && <div className="text-gray-600 text-[10px] text-center py-4">No events yet</div>}
            {[...events].reverse().map((ev, i) => (
              <div key={ev.id + "-" + i} className={"text-[9px] px-2 py-0.5 rounded " + (ev.level === "ERROR" ? "text-red-400 bg-red-500/5" : ev.level === "WARNING" ? "text-amber-400 bg-amber-500/5" : "text-gray-500")}>
                <span className="text-gray-700">{ev.ts ? ev.ts.replace("T", " ").split(".")[0].slice(11) : "--"}</span>{" "}
                <span className="text-gray-600">[{ev.level}]</span>{" "}
                {ev.msg}
              </div>
            ))}
          </div>
        </div>
      </main>
    </div>
  );
}
