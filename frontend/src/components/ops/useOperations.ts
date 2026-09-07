"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import api from "@/lib/api";
import { CommandRow, Dashboard, EventRow, LastResponse, errorText } from "./types";

export type QueueFn = (deviceId: string, command: string, slot: string, params?: Record<string, unknown>) => Promise<boolean>;

// Single data source for the operational dashboard. Explicit, targeted refreshes only — no polling.
export function useOperations(societyId: string | null) {
  const [dash, setDash] = useState<Dashboard | null>(null);
  const [commands, setCommands] = useState<Record<string, CommandRow[]>>({});
  const [events, setEvents] = useState<EventRow[]>([]);
  const [last, setLast] = useState<LastResponse | null>(null);
  const [error, setError] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<Set<string>>(new Set());  // `${deviceId}:${command}:${slot}` in flight
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  const loadDashboard = useCallback(async (sid: string) => {
    try { setDash((await api.get(`/api/admin/dashboard?society_id=${sid}`)).data); setError(""); }
    catch (e) { setError(errorText(e).detail); }
  }, []);
  const loadCommands = useCallback(async (sid: string, deviceId: string) => {
    try { const r = await api.get(`/api/admin/pi-commands?society_id=${sid}&device_id=${deviceId}&limit=25`); setCommands((c) => ({ ...c, [deviceId]: r.data.commands })); } catch { /* shown via error state */ }
  }, []);
  const loadEvents = useCallback(async (sid: string) => {
    try { setEvents((await api.get(`/api/admin/pi-events?society_id=${sid}&latest=50`)).data.events); } catch { /* optional */ }
  }, []);

  const refreshAll = useCallback(async (sid: string) => {
    const r = await api.get(`/api/admin/dashboard?society_id=${sid}`).catch((e) => { setError(errorText(e).detail); return null; });
    if (r) { setDash(r.data); await Promise.all([...r.data.devices.map((d: { id: string }) => loadCommands(sid, d.id)), loadEvents(sid)]); }
    setLoading(false);
  }, [loadCommands, loadEvents]);

  useEffect(() => {
    const pending = timers.current;
    if (societyId) Promise.resolve().then(() => refreshAll(societyId));   // async fetch; state updates happen in callbacks
    return () => { pending.forEach(clearTimeout); };
  }, [societyId, refreshAll]);

  const queue: QueueFn = useCallback(async (deviceId, command, slot, params = {}) => {
    if (!societyId) return false;
    const key = `${deviceId}:${command}:${slot}`;
    if (pending.has(key)) return false;                       // prevents accidental double submission
    setPending((p) => new Set(p).add(key));
    const at = new Date().toISOString();
    try {
      const res = await api.post("/api/admin/pi-command", { idempotency_key: crypto.randomUUID(), society_id: societyId, device_id: deviceId, slot, command, params });
      setLast({ kind: "queued", command, slot, command_id: res.data.command_id, sequence_no: res.data.sequence_no, duplicate: !!res.data.duplicate, at });
      await loadCommands(societyId, deviceId);
      // one targeted follow-up after the Pi's sync window; not a polling loop
      timers.current.push(setTimeout(() => { loadCommands(societyId, deviceId); loadDashboard(societyId); loadEvents(societyId); }, 4000));
      return true;
    } catch (e) {
      const { http, detail } = errorText(e);
      setLast({ kind: "error", command, slot, http, detail, at });
      return false;
    } finally {
      setPending((p) => { const n = new Set(p); n.delete(key); return n; });
    }
  }, [societyId, pending, loadCommands, loadDashboard, loadEvents]);

  const isPending = (deviceId: string, command: string, slot = "") => pending.has(`${deviceId}:${command}:${slot}`);
  return { dash, commands, events, last, error, loading, queue, isPending, refresh: () => societyId && refreshAll(societyId) };
}
