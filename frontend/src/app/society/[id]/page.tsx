"use client";
import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter, useParams } from "next/navigation";
import api from "@/lib/api";
import Sidebar from "@/components/Sidebar";

interface WingData { name: string; used_days: number; target_days: number; clicks: number; }
interface PiEvent { id: number; ts: string; level: string; msg: string; }

export default function SocietyDetail() {
  const router = useRouter();
  const params = useParams();
  const societyId = params.id as string;
  const [society, setSociety] = useState<any>(null);
  const [piState, setPiState] = useState<any>(null);
  const [events, setEvents] = useState<PiEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [respLabel, setRespLabel] = useState("Waiting...");
  const [respBody, setRespBody] = useState("Select a command to execute.");
  const [respOk, setRespOk] = useState(true);
  const [cmdLoading, setCmdLoading] = useState<string | null>(null);
  const [lcd1, setLcd1] = useState("");
  const [lcd2, setLcd2] = useState("");
  const [lcdTime, setLcdTime] = useState("10");
  const [eventSince, setEventSince] = useState(0);
  const [showCalc, setShowCalc] = useState(false);
  const [calcMode, setCalcMode] = useState<"units" | "days">("units");
  const [calcTotal, setCalcTotal] = useState("");
  const [calcCycleDays, setCalcCycleDays] = useState("30");
  const [calcWingUnits, setCalcWingUnits] = useState<Record<string, string>>({});
  const [calcResult, setCalcResult] = useState<Record<string, number>>({});
  const [sendingDays, setSendingDays] = useState(false);
  const [resetDayInput, setResetDayInput] = useState("");
  const [settingResetDay, setSettingResetDay] = useState(false);
  const eventsEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => { const role = localStorage.getItem("role"); if (!role || (role !== "super_admin" && role !== "society_admin" && role !== "member")) router.push("/login"); }, [router]);

  const fetchSociety = useCallback(async () => { try { const res = await api.get("/api/super-admin/societies"); setSociety(res.data.find((s: any) => s.id === societyId) || null); } catch {} }, [societyId]);
  const fetchPiState = useCallback(async () => { if (!societyId) return; try { const res = await api.get("/api/admin/pi-state?society_id=" + societyId); setPiState(res.data.connected ? res.data : null); } catch { setPiState(null); } }, [societyId]);
  const fetchEvents = useCallback(async () => { if (!societyId) return; try { const res = await api.get("/api/admin/pi-events?society_id=" + societyId + "&since=" + eventSince); if (res.data.events.length > 0) { setEvents((p) => [...p, ...res.data.events]); setEventSince(res.data.next); } } catch {} }, [societyId, eventSince]);

  useEffect(() => { fetchSociety(); fetchPiState(); fetchEvents(); setLoading(false); }, [fetchSociety, fetchPiState, fetchEvents]);
  useEffect(() => { const i = setInterval(fetchPiState, 20000); return () => clearInterval(i); }, [fetchPiState]);
  useEffect(() => { const i = setInterval(fetchEvents, 25000); return () => clearInterval(i); }, [fetchEvents]);
  useEffect(() => { eventsEndRef.current && (eventsEndRef.current.scrollTop = eventsEndRef.current.scrollHeight); }, [events]);

  const sendCmd = async (command: string, wing: string = "", label: string = "", params: Record<string, any> = {}) => {
    setCmdLoading(command); setRespLabel(label || command); setRespBody("Queuing..."); setRespOk(true);
    try {
      const res = await api.post("/api/admin/pi-command", { society_id: societyId, command, wing, params });
      if (res.data.success) { setRespBody("Command queued. Pi will execute on next sync."); setRespOk(true); setTimeout(fetchPiState, 8000); }
      else { setRespBody("Failed: " + (res.data.message || "Unknown")); setRespOk(false); }
    } catch (e: any) { setRespBody("Error: " + (e.message || "Network error")); setRespOk(false); }
    setCmdLoading(null);
  };

  const doCalcUnits = () => {
    const days = parseInt(calcCycleDays) || 30;
    const wk = piState ? Object.keys(piState.wings) : [];
    let total = 0;
    wk.forEach((w) => { total += parseFloat(calcWingUnits[w] || "0") || 0; });
    if (total <= 0) { setCalcResult({}); return; }
    const avg = total / days;
    const exact: Record<string, number> = {};
    const rounded: Record<string, number> = {};
    let sum = 0;
    wk.forEach((w) => {
      const u = parseFloat(calcWingUnits[w] || "0") || 0;
      const ed = u / avg;
      exact[w] = ed;
      rounded[w] = Math.round(ed);
      sum += rounded[w];
    });
    const diff = days - sum;
    if (diff !== 0) {
      const sorted = wk.slice().sort((a, b) => {
        const fa = exact[a] - Math.floor(exact[a]);
        const fb = exact[b] - Math.floor(exact[b]);
        return diff > 0 ? fb - fa : fa - fb;
      });
      for (let i = 0; i < Math.abs(diff); i++) { rounded[sorted[i]] += diff > 0 ? 1 : -1; }
    }
    wk.forEach((w) => { const u = parseFloat(calcWingUnits[w] || "0") || 0; if (u > 0 && rounded[w] < 1) rounded[w] = 1; });
    setCalcResult(rounded);
  };

  const wings = piState ? Object.entries(piState.wings as Record<string, WingData>) : [];
  const activeWing = piState?.active_wing || null;
  const isOnline = piState && (Date.now() - new Date(piState.last_sync).getTime()) < 600000;
  const uptime = piState ? Math.floor(piState.uptime_seconds / 3600) + "h " + Math.floor((piState.uptime_seconds % 3600) / 60) + "m" : "--";
  const sinceSync = piState ? Math.floor((Date.now() - new Date(piState.last_sync).getTime()) / 1000) + "s ago" : "--";
  const nextResetDate = piState ? (() => { const d = new Date(); let m = d.getMonth(), y = d.getFullYear(); if (d.getDate() >= piState.reset_day) { m++; if (m > 11) { m = 0; y++; } } return new Date(y, m, piState.reset_day).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }); })() : "--";
  const role = typeof window !== "undefined" ? localStorage.getItem("role") : "";
  const isSuperAdmin = role === "super_admin";

  const sendAllCalcDays = async () => {
    if (!isOnline || !societyId) return;
    setSendingDays(true);
    setRespLabel("Send All Days");
    setRespBody("Sending...");
    setRespOk(true);
    try {
      for (const [w, d] of Object.entries(calcResult)) {
        if (d > 0) await sendCmd("set_days", w, "Set Wing " + w + " to " + d + " days", { days: d });
      }
      setRespBody("Sent " + Object.keys(calcResult).filter((_, i) => calcResult[Object.keys(calcResult)[i]] > 0).length + " wing day settings.");
    } catch (e: any) { setRespBody("Error: " + (e.message || "Network error")); setRespOk(false); }
    setSendingDays(false);
  };

  const sendResetDay = async () => {
    const day = parseInt(resetDayInput);
    if (!day || day < 1 || day > 28 || !societyId) return;
    setSettingResetDay(true);
    setRespLabel("Set Reset Day");
    setRespBody("Queuing...");
    setRespOk(true);
    try {
      const res = await api.post("/api/admin/pi-command", { society_id: societyId, command: "set_reset_day", params: { day } });
      if (res.data.success) { setRespBody("Reset day set to " + day + "th. Pi will apply on next sync."); setRespOk(true); setTimeout(fetchPiState, 8000); }
      else { setRespBody("Failed: " + (res.data.message || "Unknown")); setRespOk(false); }
    } catch (e: any) { setRespBody("Error: " + (e.message || "Network error")); setRespOk(false); }
    setSettingResetDay(false);
  };

  const confirmResetDay = () => {
    const day = parseInt(resetDayInput);
    if (!day || day < 1 || day > 28) return;
    if (!window.confirm("Set reset day to " + day + "th of every month?\n\nAll wings will reset to 0 days on this date.")) return;
    sendResetDay();
  };

  if (loading) return <div className="flex h-screen items-center justify-center text-gray-500">Loading...</div>;

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar role={isSuperAdmin ? "super_admin" : "society_admin"} />
      <main className="flex-1 overflow-y-auto p-6 mt-10" style={{ background: "#0a0e17" }}>
        <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
          <div className="flex items-center gap-3">
            <button onClick={() => router.push("/super-admin")} className="px-3 py-2 border border-gray-700 text-gray-400 text-xs rounded-lg hover:border-cyan-500 hover:text-cyan-400">&#8592; Back</button>
            <div>
              <h1 className="text-2xl font-bold text-white">{society?.name || "Society"}</h1>
              <p className="text-xs text-gray-500">{society?.location} | {society?.plan} | Code: {society?.society_code || "--"}</p>
            </div>
          </div>
          <div className={"flex items-center gap-2 px-4 py-2 rounded-full text-xs font-semibold " + (isOnline ? "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30" : "bg-red-500/15 text-red-400 border border-red-500/30")}>
            <span className={"w-2 h-2 rounded-full " + (isOnline ? "bg-emerald-400 animate-pulse" : "bg-red-400")} />
            {isOnline ? "Online" : "Offline"} {piState && "| FW v" + piState.firmware_version}
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-2 mb-4">
          {[
            { label: "Active Wing", value: activeWing || "--", color: "text-cyan-400" },
            { label: "Wings", value: String(wings.length), color: "text-emerald-400" },
            { label: "Reset Day", value: piState ? piState.reset_day + "th" : "--", color: "text-amber-400" },
            { label: "Next Reset", value: nextResetDate, color: "text-pink-400" },
            { label: "CPU Temp", value: piState ? piState.cpu_temp + "\u00B0C" : "--", color: piState && piState.cpu_temp > 70 ? "text-red-400" : "text-purple-400" },
            { label: "Uptime", value: uptime, color: "text-blue-400" },
            { label: "Boots", value: piState ? String(piState.boot_count) : "--", color: "text-orange-400" },
            { label: "Last Sync", value: sinceSync, color: "text-gray-400" },
          ].map((s) => (
            <div key={s.label} className="bg-gray-900/80 border border-gray-800 rounded-lg p-3">
              <div className="text-[9px] text-gray-500 uppercase tracking-wider">{s.label}</div>
              <div className={"text-sm font-bold mt-0.5 " + s.color}>{s.value}</div>
            </div>
          ))}
        </div>

        {piState?.emergency_stop && (
          <div className="bg-red-500/20 border border-red-500/50 rounded-xl p-4 mb-4 flex items-center gap-3">
            <span className="text-2xl">&#9888;&#65039;</span>
            <div><div className="text-red-400 font-bold text-sm">EMERGENCY STOP ACTIVE (Hardware)</div><div className="text-red-400/60 text-xs">Physical E-Stop pressed. All relays off. Release the button to resume.</div></div>
          </div>
        )}

        {!piState && (
          <div className="bg-gray-900/50 border border-gray-800 rounded-xl p-8 mb-4 text-center">
            <div className="text-4xl mb-3 opacity-30">&#128225;</div>
            <div className="text-gray-400 font-semibold mb-2">Waiting for Pi Connection</div>
            <div className="text-gray-600 text-xs max-w-md mx-auto">Pi must be running and syncing to this society. API Key: <code className="text-amber-400">{society?.api_key ? society.api_key.slice(0, 7) + "..." : "Not set — edit society to add"}</code></div>
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
          <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-800 bg-gray-800/50 flex items-center justify-between">
              <h2 className="text-xs font-semibold text-gray-300">&#128295; Wings</h2>
              <button onClick={fetchPiState} className="text-[10px] text-gray-500 hover:text-cyan-400">&#8635; Refresh</button>
            </div>
            <div className="p-4 space-y-2 max-h-[500px] overflow-y-auto">
              {wings.length === 0 && <div className="text-gray-600 text-xs text-center py-8">No wing data</div>}
              {wings.map(([id, w]) => {
                const isActive = activeWing === id;
                const pct = w.target_days > 0 ? Math.min(100, Math.round((w.used_days / w.target_days) * 100)) : 0;
                const barColor = pct > 80 ? "bg-red-500" : pct > 50 ? "bg-amber-500" : "bg-emerald-500";
                const textColor = pct > 80 ? "text-red-400" : pct > 50 ? "text-amber-400" : "text-emerald-400";
                return (
                  <div key={id} className={"rounded-lg p-3 border transition-all " + (isActive ? "border-emerald-500/40 bg-emerald-500/5" : "border-gray-800 bg-gray-900/50")}>
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <span className="text-xl font-black text-cyan-400 font-mono">{id}</span>
                        <span className="text-sm font-semibold text-gray-200">{w.name}</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        {isActive && <span className="text-[9px] px-2 py-0.5 rounded-full bg-cyan-500/15 text-cyan-400 border border-cyan-500/25 font-semibold">ACTIVE</span>}
                        {pct >= 100 && <span className="text-[9px] px-2 py-0.5 rounded-full bg-red-500/15 text-red-400 border border-red-500/25 font-semibold">FULL</span>}
                        <span className="text-[9px] text-gray-600">{w.clicks} clicks</span>
                      </div>
                    </div>
                    <div className="flex gap-2 mb-2">
                      <button onClick={() => { if (window.confirm("Switch to Wing " + id + "?")) sendCmd("set_active_wing", id, "Switch to " + id); }} disabled={cmdLoading !== null || !isOnline || piState?.emergency_stop} className="flex-1 py-1.5 bg-emerald-500 hover:bg-emerald-600 text-black text-[10px] font-bold rounded disabled:opacity-30">SWITCH TO</button>
                      <button onClick={() => { if (window.confirm("Turn OFF Wing " + id + "?")) sendCmd("off_wing", id, "Turn off " + id); }} disabled={cmdLoading !== null || !isOnline || piState?.emergency_stop} className="flex-1 py-1.5 bg-red-500 hover:bg-red-600 text-white text-[10px] font-bold rounded disabled:opacity-30">TURN OFF</button>
                    </div>
                    <div className="flex justify-between text-[10px] mb-1">
                      <span className="text-gray-500">Days: {w.used_days} / {w.target_days}</span>
                      <span className={"font-bold " + textColor}>{pct}%</span>
                    </div>
                    <div className="w-full h-1.5 bg-gray-800 rounded-full overflow-hidden">
                      <div className={"h-full rounded-full transition-all duration-500 " + barColor} style={{ width: pct + "%" }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="space-y-4">
            <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-800 bg-gray-800/50"><h2 className="text-xs font-semibold text-gray-300">&#128225; Last Response</h2></div>
              <div className="p-4">
                <div className="text-[10px] text-gray-500 mb-1">{respLabel}</div>
                <div className={"text-xs font-medium " + (respOk ? "text-gray-300" : "text-red-400")}>{respBody}</div>
              </div>
            </div>

            <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-800 bg-gray-800/50"><h2 className="text-xs font-semibold text-gray-300">&#9881; System Controls</h2></div>
              <div className="p-4 grid grid-cols-2 gap-2">
                {[
                  { label: "Reset Days", cmd: "reset_days", icon: "&#128260;", danger: false },
                  { label: "OFF All", cmd: "off_all", icon: "&#128465;", danger: true },
                  { label: "Restart EMS", cmd: "restart", icon: "&#128260;", danger: false },
                  { label: "Reboot Pi", cmd: "reboot", icon: "&#128260;", danger: true },
                ].map((b) => (
                  <button key={b.cmd} onClick={() => { if (window.confirm(b.danger ? "⚠️ " + b.label + "\n\nThis affects the EMS system." : b.label + "?")) sendCmd(b.cmd, "", b.label); }} disabled={cmdLoading !== null || !isOnline} className={"p-2 rounded-lg border text-[9px] font-semibold flex flex-col items-center gap-1 transition-all disabled:opacity-30 " + (b.danger ? "border-gray-800 text-gray-400 hover:border-red-500 hover:text-red-400 hover:bg-red-500/5" : "border-gray-800 text-gray-400 hover:border-cyan-500 hover:text-cyan-400 hover:bg-cyan-500/5")}>
                    <span className="text-base" dangerouslySetInnerHTML={{ __html: b.icon }} />{b.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-800 bg-gray-800/50"><h2 className="text-xs font-semibold text-gray-300">&#128433; LCD Display</h2></div>
              <div className="p-4">
                <div className="bg-black border-2 border-gray-700 rounded-lg p-3 font-mono text-emerald-400 text-sm text-center mb-3 min-h-[44px] flex flex-col items-center justify-center" style={{ textShadow: "0 0 10px rgba(16,185,129,0.5)" }}>
                  <div>{lcd1 || "EMS READY"}</div><div>{lcd2 || ""}</div>
                </div>
                <div className="flex gap-2 mb-2">
                  <input className="flex-1 px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs font-mono text-gray-200 focus:outline-none focus:border-cyan-500" placeholder="Line 1" maxLength={16} value={lcd1} onChange={(e) => setLcd1(e.target.value)} />
                  <input className="flex-1 px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs font-mono text-gray-200 focus:outline-none focus:border-cyan-500" placeholder="Line 2" maxLength={16} value={lcd2} onChange={(e) => setLcd2(e.target.value)} />
                  <input type="number" className="w-14 px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs text-center text-gray-200 focus:outline-none focus:border-cyan-500" value={lcdTime} onChange={(e) => setLcdTime(e.target.value)} min="1" max="300" />
                </div>
                <div className="flex gap-2">
                  <button onClick={() => { if (!lcd1 && !lcd2) return; sendCmd("lcd_display", "", "LCD: " + lcd1 + " | " + lcd2, { line1: lcd1, line2: lcd2, duration: parseInt(lcdTime) || 10 }); }} disabled={cmdLoading !== null || !isOnline || (!lcd1 && !lcd2)} className="flex-1 py-1.5 bg-cyan-500 text-black text-[10px] font-bold rounded disabled:opacity-30">SEND</button>
                  <button onClick={() => { setLcd1(""); setLcd2(""); }} className="px-3 py-1.5 border border-gray-700 text-gray-500 text-[10px] rounded hover:border-red-500 hover:text-red-400">CLEAR</button>
                </div>
              </div>
            </div>

            <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-800 bg-gray-800/50"><h2 className="text-xs font-semibold text-gray-300">&#128197; Set Reset Date</h2></div>
              <div className="p-4">
                <div className="flex gap-2 items-center">
                  <span className="text-[10px] text-gray-500">Day (1-28):</span>
                  <input type="number" className="w-16 px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs text-center text-gray-200 focus:outline-none focus:border-cyan-500" value={resetDayInput} onChange={(e) => setResetDayInput(e.target.value)} min="1" max="28" />
                  <button onClick={confirmResetDay} disabled={settingResetDay || !isOnline || cmdLoading !== null} className="px-4 py-1.5 bg-amber-500/20 border border-amber-500/30 text-amber-400 text-[10px] font-bold rounded hover:bg-amber-500/30 disabled:opacity-30">{settingResetDay ? "Setting..." : "SET"}</button>
                </div>
                {piState?.reset_day && <div className="text-[9px] text-gray-600 mt-2">Current: {piState.reset_day}th of each month</div>}
              </div>
            </div>

            <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-800 bg-gray-800/50 flex items-center justify-between cursor-pointer" onClick={() => setShowCalc(!showCalc)}>
                <h2 className="text-xs font-semibold text-gray-300">&#9889; Unit-to-Days Calculator</h2>
                <span className="text-gray-600 text-xs">{showCalc ? "▲" : "▼"}</span>
              </div>
              {showCalc && (
                <div className="p-4 space-y-3">
                  <select className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200" value={calcMode} onChange={(e) => { setCalcMode(e.target.value as any); setCalcResult({}); }}>
                    <option value="units">Units Mode</option><option value="days">Direct Days</option>
                  </select>
                  {calcMode === "units" ? (
                    <>
                      <div className="flex gap-2">
                        <input type="number" className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200" placeholder="Cycle Days (e.g. 30)" value={calcCycleDays} onChange={(e) => setCalcCycleDays(e.target.value)} />
                      </div>
                      {wings.map(([id]) => (
                        <div key={id} className="flex gap-2 items-center">
                          <span className="text-[10px] text-gray-500 w-12">Wing {id}</span>
                          <input className="flex-1 px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200" placeholder="Monthly units" value={calcWingUnits[id] || ""} onChange={(e) => setCalcWingUnits({ ...calcWingUnits, [id]: e.target.value })} />
                        </div>
                      ))}
                      <button onClick={doCalcUnits} className="w-full py-1.5 bg-amber-500/20 border border-amber-500/30 text-amber-400 text-[10px] font-bold rounded hover:bg-amber-500/30">CALCULATE</button>
                    </>
                  ) : (
                    wings.map(([id, w]) => (
                      <div key={id} className="flex gap-2 items-center">
                        <span className="text-[10px] text-gray-500 w-12">Wing {id}</span>
                        <input type="number" className="flex-1 px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200" value={calcResult[id] ?? w.target_days} onChange={(e) => setCalcResult({ ...calcResult, [id]: parseInt(e.target.value) || 0 })} />
                      </div>
                    ))
                  )}
                  {Object.keys(calcResult).length > 0 && (
                    <div className="bg-gray-800/50 rounded-lg p-3 space-y-1">
                      <div className="text-[10px] text-amber-400 font-bold text-center mb-1">
                        {calcMode === "units" ? "Total: " + Object.values(calcWingUnits).reduce((a, v) => a + (parseFloat(v) || 0), 0) + " units | Avg: " + (Object.values(calcWingUnits).reduce((a, v) => a + (parseFloat(v) || 0), 0) / (parseInt(calcCycleDays) || 30)).toFixed(2) + " units/day" : "Direct Days Mode"}
                      </div>
                      {Object.entries(calcResult).map(([id, days]) => (
                        <div key={id} className="flex justify-between text-[10px]"><span className="text-gray-500">Wing {id}</span><span className="text-cyan-400 font-bold font-mono">{days} days</span></div>
                      ))}
                      <button onClick={() => { if (window.confirm("Send " + Object.keys(calcResult).length + " wing day settings to Pi?")) sendAllCalcDays(); }} disabled={sendingDays || !isOnline || cmdLoading !== null} className="w-full mt-2 py-1.5 bg-cyan-500/20 border border-cyan-500/30 text-cyan-400 text-[10px] font-bold rounded hover:bg-cyan-500/30 disabled:opacity-30">{sendingDays ? "Sending..." : "SEND ALL DAYS TO PI"}</button>
                    </div>
                  )}
                </div>
              )}
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
                <span className="text-gray-600">[{ev.level}]</span>{" "}{ev.msg}
              </div>
            ))}
          </div>
        </div>
      </main>
    </div>
  );
}