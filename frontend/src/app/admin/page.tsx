"use client";
import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import api from "@/lib/api";
import Sidebar from "@/components/Sidebar";

interface WingData { name: string; used_days: number; target_days: number; clicks: number; }
interface PiState { active_wing: string | null; wings: Record<string, WingData>; reset_day: number; emergency_stop: boolean; firmware_version: string; uptime_seconds: number; cpu_temp: number; disk_free_mb: number; last_sync: string; boot_count: number; last_shutdown_reason: string; locked: boolean; pending_start: boolean; quota_lock_until: string; reset_day_lock_until: string; }
interface PiEvent { id: number; ts: string; level: string; msg: string; }

export default function AdminDashboard() {
  const router = useRouter();
  const [piState, setPiState] = useState<PiState | null>(null);
  const [events, setEvents] = useState<PiEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [respLabel, setRespLabel] = useState("Waiting...");
  const [respBody, setRespBody] = useState("Pi will auto-connect when firmware is running.");
  const [respOk, setRespOk] = useState(true);
  const [cmdLoading, setCmdLoading] = useState<string | null>(null);
  const [lcd1, setLcd1] = useState("");
  const [lcd2, setLcd2] = useState("");
  const [lcdTime, setLcdTime] = useState("10");
  const [eventSince, setEventSince] = useState(0);
  const [daysInput, setDaysInput] = useState<Record<string, string>>({});
  const [settingDays, setSettingDays] = useState<string | null>(null);
  const [resetDayInput, setResetDayInput] = useState("");
  const [settingResetDay, setSettingResetDay] = useState(false);
  const [editSocName, setEditSocName] = useState(false);
  const [socDisplayName, setSocDisplayName] = useState("");
  const [editWingName, setEditWingName] = useState<string | null>(null);
  const [wingDisplayNames, setWingDisplayNames] = useState<Record<string, string>>({});
  const eventsEndRef = useRef<HTMLDivElement>(null);

  const _r = typeof window !== "undefined" ? localStorage.getItem("role") : "";
  useEffect(() => { if (_r === "member") { router.push("/member"); return; } if (_r !== "society_admin" && _r !== "super_admin") { router.push("/login"); return; } }, [router]);

  const societyId = typeof window !== "undefined" ? (() => { try { const t = localStorage.getItem("token") || ""; if (t && t.includes(".")) return JSON.parse(atob(t.split(".")[1])).society_id || ""; return JSON.parse(t).society_id || ""; } catch(e) { return ""; } })() : "";

  useEffect(() => {
    const saved = localStorage.getItem("socDisplayName_" + societyId);
    if (saved) setSocDisplayName(saved);
    const savedWing = localStorage.getItem("wingNames_" + societyId);
    if (savedWing) setWingDisplayNames(JSON.parse(savedWing));
  }, [societyId]);

  const fetchPiState = useCallback(async () => {
    if (!societyId) return;
    try {
      const res = await api.get("/api/admin/pi-state?society_id=" + societyId);
      if (res.data.connected) { setPiState(res.data); if (!resetDayInput && res.data.reset_day) setResetDayInput(String(res.data.reset_day)); }
    } catch {}
  }, [societyId, resetDayInput]);

  const fetchEvents = useCallback(async () => {
    if (!societyId) return;
    try {
      const res = await api.get("/api/admin/pi-events?society_id=" + societyId + "&since=" + eventSince);
      if (res.data.events.length > 0) { setEvents((prev) => [...prev, ...res.data.events]); setEventSince(res.data.next); }
    } catch {}
  }, [societyId, eventSince]);

  useEffect(() => { fetchPiState(); fetchEvents(); setLoading(false); }, [fetchPiState, fetchEvents]);
  useEffect(() => { const i = setInterval(fetchPiState, 5000); return () => clearInterval(i); }, [fetchPiState]);
  useEffect(() => { const i = setInterval(fetchEvents, 3000); return () => clearInterval(i); }, [fetchEvents]);
  useEffect(() => { if (eventsEndRef.current) { eventsEndRef.current.scrollTop = eventsEndRef.current.scrollHeight; } }, [events]);

  const sendCmd = async (command: string, wing: string = "", label: string = "", params: Record<string, any> = {}) => {
    if (!societyId) return;
    setCmdLoading(command);
    setRespLabel(label || command);
    setRespBody("Queuing command...");
    setRespOk(true);
    try {
      const res = await api.post("/api/admin/pi-command", { society_id: societyId, command, wing, params });
      if (res.data.success) { setRespBody("Command queued. Pi will execute within 30s."); setRespOk(true); setTimeout(fetchPiState, 5000); }
      else { setRespBody("Failed: " + (res.data.message || "Unknown error")); setRespOk(false); }
    } catch (e: any) { setRespBody("Error: " + (e.message || "Network error")); setRespOk(false); }
    setCmdLoading(null);
  };

  const sendWingDays = async (wing: string) => {
    const days = parseInt(daysInput[wing] || "0");
    if (!days || days < 1 || !societyId) return;
    setSettingDays(wing);
    setRespLabel("Set Days: Wing " + wing);
    setRespBody("Queuing set_days command with 30-day lock...");
    setRespOk(true);
    const lockUntil = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString();
    try {
      const res = await api.post("/api/admin/pi-command", { society_id: societyId, command: "set_days", wing, params: { days, lock_until: lockUntil } });
      if (res.data.success) {
        setRespBody("Set " + days + " days for Wing " + wing + ". Locked for 30 days until " + new Date(lockUntil).toLocaleDateString() + ".");
        setRespOk(true);
        setDaysInput((prev) => { const n = { ...prev }; delete n[wing]; return n; });
        setTimeout(fetchPiState, 5000);
      } else { setRespBody("Failed: " + (res.data.message || "Unknown error")); setRespOk(false); }
    } catch (e: any) { setRespBody("Error: " + (e.message || "Network error")); setRespOk(false); }
    setSettingDays(null);
  };

  const sendResetDay = async () => {
    const day = parseInt(resetDayInput);
    if (!day || day < 1 || day > 28 || !societyId) return;
    setSettingResetDay(true);
    setRespLabel("Set Reset Day");
    setRespBody("Queuing set_reset_day command...");
    setRespOk(true);
    try {
      const res = await api.post("/api/admin/pi-command", { society_id: societyId, command: "set_reset_day", params: { day } });
      if (res.data.success) { setRespBody("Reset day set to " + day + ". Pi will auto-reset days on that date each month."); setRespOk(true); setTimeout(fetchPiState, 5000); }
      else { setRespBody("Failed: " + (res.data.message || "Unknown error")); setRespOk(false); }
    } catch (e: any) { setRespBody("Error: " + (e.message || "Network error")); setRespOk(false); }
    setSettingResetDay(false);
  };

  const saveSocDisplayName = () => { if (societyId && socDisplayName.trim()) { localStorage.setItem("socDisplayName_" + societyId, socDisplayName.trim()); setEditSocName(false); } };
  const saveWingName = (wingId: string) => { const names = { ...wingDisplayNames }; if (!names[wingId]?.trim()) delete names[wingId]; else names[wingId] = names[wingId].trim(); setWingDisplayNames(names); setEditWingName(null); localStorage.setItem("wingNames_" + societyId, JSON.stringify(names)); };

  const wings = piState ? Object.entries(piState.wings) : [];
  const activeWing = piState?.active_wing || null;
  const isOnline = piState && (Date.now() - new Date(piState.last_sync).getTime()) < 360000;
  const uptime = piState ? Math.floor(piState.uptime_seconds / 3600) + "h " + Math.floor((piState.uptime_seconds % 3600) / 60) + "m" : "--";
  const quotaLocked = piState?.quota_lock_until ? new Date(piState.quota_lock_until) > new Date() : false;
  const resetDayLocked = piState?.reset_day_lock_until ? new Date(piState.reset_day_lock_until) > new Date() : false;

  if (loading) return <div className="flex h-screen items-center justify-center text-gray-500">Loading...</div>;

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar role={_r || "society_admin"} />
      <main className="flex-1 overflow-y-auto p-6 pt-20" style={{ background: "#0a0e17" }}>
        <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
          <div className="flex items-center gap-3">
            {editSocName ? (
              <div className="flex items-center gap-2">
                <input className="px-3 py-1.5 bg-gray-800 border border-cyan-500 rounded text-sm text-white focus:outline-none" value={socDisplayName} onChange={(e) => setSocDisplayName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && saveSocDisplayName()} autoFocus />
              </div>
            ) : null}

            <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-800 bg-gray-800/50">
                <h2 className="text-xs font-semibold text-gray-300">&#128247; LCD Display</h2>
              </div>
              <div className="p-4">
                <div className="bg-black border-2 border-gray-700 rounded-lg p-3 font-mono text-emerald-400 text-sm text-center mb-3 min-h-[44px] flex flex-col items-center justify-center" style={{ textShadow: "0 0 10px rgba(16,185,129,0.5)" }}>
                  <div>{lcd1 || "EMS READY"}</div>
                  <div>{lcd2 || ""}</div>
                </div>
                <div className="flex gap-2 mb-2">
                  <input className="flex-1 px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs font-mono text-gray-200 focus:outline-none focus:border-cyan-500" placeholder="Line 1 (16ch)" maxLength={16} value={lcd1} onChange={(e) => setLcd1(e.target.value)} />
                  <input className="flex-1 px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs font-mono text-gray-200 focus:outline-none focus:border-cyan-500" placeholder="Line 2 (16ch)" maxLength={16} value={lcd2} onChange={(e) => setLcd2(e.target.value)} />
                  <input type="number" className="w-14 px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs text-center text-gray-200 focus:outline-none focus:border-cyan-500" value={lcdTime} onChange={(e) => setLcdTime(e.target.value)} min="1" max="300" />
                </div>
                <div className="flex gap-2">
                  <button onClick={() => { if (!lcd1 && !lcd2) return; sendCmd("lcd", "", "LCD: " + lcd1 + " | " + lcd2, { l1: lcd1, l2: lcd2, t: parseInt(lcdTime) || 10 }); }} disabled={cmdLoading !== null || !isOnline || (!lcd1 && !lcd2)} className="flex-1 py-1.5 bg-cyan-500 text-black text-[10px] font-bold rounded disabled:opacity-30">SEND</button>
                  <button onClick={() => { setLcd1(""); setLcd2(""); }} className="px-3 py-1.5 border border-gray-700 text-gray-500 text-[10px] rounded hover:border-red-500 hover:text-red-400">CLEAR</button>
                </div>
              </div>
            </div>
          </div>
        </div>


        <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-800 bg-gray-800/50 flex items-center justify-between">
            <h2 className="text-xs font-semibold text-gray-300">&#128221; Pi Events</h2>
            <span className="text-[9px] text-gray-600">{events.length} events</span>
          </div>
          <div ref={eventsEndRef} className="max-h-[200px] overflow-y-auto p-2 space-y-0.5 font-mono">
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