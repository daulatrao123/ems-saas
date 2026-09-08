"use client";
import { useCallback, useEffect, useState } from "react";
import api from "@/lib/api";
import { Device, LcdMessage, errorText } from "./types";
import { btn, label, panel, tone } from "./DashboardHeader";

const MAX = 80;

// Website -> Pi LCD text. Display-only on the Pi; travels inside the authenticated /api/pi/sync reply.
export function LcdMessagePanel({ societyId, device, readOnly }: { societyId: string; device: Device; readOnly: boolean }) {
  const [text, setText] = useState("");
  const [expires, setExpires] = useState("");
  const [active, setActive] = useState<LcdMessage | null>(null);
  const [history, setHistory] = useState<LcdMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await api.get(`/api/admin/lcd-messages?society_id=${societyId}&device_id=${device.id}`);
      setActive(r.data.active || null); setHistory(r.data.messages || []); setErr(null);
    } catch (e) { setErr(errorText(e).detail); }
  }, [societyId, device.id]);
  useEffect(() => { if (!readOnly) load(); }, [load, readOnly]);

  if (readOnly) return null;
  const trimmed = text.trim();
  const send = async () => {
    if (!trimmed || trimmed.length > MAX || busy) return;
    setBusy(true);
    try {
      await api.post("/api/admin/lcd-messages", { society_id: societyId, device_id: device.id, message: trimmed, expires_at: expires ? new Date(expires).toISOString() : null });
      setText(""); setExpires(""); await load();
    } catch (e) { setErr(errorText(e).detail); } finally { setBusy(false); }
  };
  const deactivate = async (id: number) => {
    setBusy(true);
    try { await api.post(`/api/admin/lcd-messages/${id}/deactivate`, { society_id: societyId }); await load(); }
    catch (e) { setErr(errorText(e).detail); } finally { setBusy(false); }
  };

  return (
    <section data-testid="lcd-panel" className={`${panel} p-4 space-y-3`}>
      <div className="flex items-center justify-between">
        <span className={label}>LCD message → {device.name}</span>
        <span className={label}>{device.lcd?.available === false ? "LCD OFFLINE ON PI" : "20×4 display"}</span>
      </div>
      <div data-testid="lcd-active" className="font-mono text-xs">
        {active ? (
          <div className="flex items-start justify-between gap-3 border border-cyan-500/30 bg-cyan-500/5 px-3 py-2">
            <div>
              <div className="text-cyan-200">“{active.message}”</div>
              <div className="mt-1 text-[10px] text-gray-500">created {active.created_at?.replace("T", " ").slice(0, 16)} · {active.expires_at ? `expires ${active.expires_at.replace("T", " ").slice(0, 16)}` : "no expiry"} · {active.delivered_at ? "delivered to Pi" : "not yet delivered"}</div>
            </div>
            <button data-testid="lcd-deactivate" disabled={busy} onClick={() => deactivate(active.id)} className={`${btn} ${tone.gray}`}>DEACTIVATE</button>
          </div>
        ) : <span className="text-gray-500">No active message — LCD rotates SYSTEM / A·B / C·D only.</span>}
      </div>
      <div className="grid gap-2 md:grid-cols-[1fr_190px_auto]">
        <input data-testid="lcd-input" value={text} maxLength={MAX + 20} onChange={(e) => setText(e.target.value)} placeholder="Plain text shown on the Pi LCD (max 80 chars)"
          className="bg-[#0a0f18] border border-[#2a3646] px-3 py-2 font-mono text-xs text-gray-100 outline-none focus:border-cyan-500/60" />
        <input data-testid="lcd-expires" type="datetime-local" value={expires} onChange={(e) => setExpires(e.target.value)} className="bg-[#0a0f18] border border-[#2a3646] px-2 py-2 font-mono text-xs text-gray-300" />
        <button data-testid="lcd-send" disabled={busy || !trimmed || trimmed.length > MAX} onClick={send} className={`${btn} ${tone.cyan} disabled:opacity-40`}>{busy ? "SENDING…" : "SEND TO LCD"}</button>
      </div>
      <div className="flex items-center justify-between">
        <span data-testid="lcd-counter" className={`font-mono text-[10px] ${trimmed.length > MAX ? "text-red-300" : "text-gray-500"}`}>{trimmed.length} / {MAX}{trimmed.length > MAX ? " — too long" : ""}</span>
        {err && <span data-testid="lcd-error" className="font-mono text-[10px] text-red-300">{err}</span>}
      </div>
      {history.length > 1 && (
        <details className="font-mono text-[10px] text-gray-500"><summary className="cursor-pointer">History ({history.length})</summary>
          <ul className="mt-1 space-y-0.5">{history.slice(0, 10).map((m) => <li key={m.id} data-testid={`lcd-history-${m.id}`}>{m.created_at?.replace("T", " ").slice(0, 16)} · {m.active && !m.expired ? "ACTIVE" : m.expired ? "EXPIRED" : "INACTIVE"} · {m.message}</li>)}</ul>
        </details>
      )}
    </section>
  );
}
