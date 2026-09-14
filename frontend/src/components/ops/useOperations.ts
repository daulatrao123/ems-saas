"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import api from "@/lib/api";
import { CommandRow, Dashboard, EventRow, LastResponse, errorText } from "./types";

export type QueueFn = (deviceId: string, command: string, slot: string, params?: Record<string, unknown>) => Promise<boolean>;
// LOGICAL slot enable/disable (slot_configs.disabled). Never touches GPIO, toggle or contactor state.
export type SlotConfigFn = (deviceId: string, slot: string, patch: { disabled?: boolean; display_name?: string }) => Promise<boolean>;

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

  const readDashboard = useCallback(async (sid: string): Promise<Dashboard> => {
    const [dashboard, inventory] = await Promise.all([
      api.get(`/api/admin/dashboard?society_id=${sid}`),
      api.get(`/api/admin/devices?society_id=${sid}`).catch(() => null),
    ]);
    const metadata: { id: string; feedback_hardware_installed?: boolean }[] = inventory?.data?.devices || [];
    return { ...dashboard.data, devices: (dashboard.data as Dashboard).devices.map((device) => {
      const installed = metadata.find((item) => item.id === device.id)?.feedback_hardware_installed;
      return { ...device, feedback_hardware_installed: typeof installed === "boolean" ? installed : device.feedback_hardware_installed };
    }) };
  }, []);
  const loadDashboard = useCallback(async (sid: string) => {
    try { setDash(await readDashboard(sid)); setError(""); }
    catch (e) { setError(errorText(e).detail); }
  }, [readDashboard]);
  const loadCommands = useCallback(async (sid: string, deviceId: string) => {
    try { const r = await api.get(`/api/admin/pi-commands?society_id=${sid}&device_id=${deviceId}&limit=25`); setCommands((c) => ({ ...c, [deviceId]: r.data.commands })); } catch { /* shown via error state */ }
  }, []);
  const loadEvents = useCallback(async (sid: string) => {
    try { setEvents((await api.get(`/api/admin/pi-events?society_id=${sid}&latest=50`)).data.events); } catch { /* optional */ }
  }, []);

  const refreshAll = useCallback(async (sid: string) => {
    const r = await readDashboard(sid).catch((e) => { setError(errorText(e).detail); return null; });
    if (r) { setDash(r); await Promise.all([...r.devices.map((d) => loadCommands(sid, d.id)), loadEvents(sid)]); }
    setLoading(false);
  }, [readDashboard, loadCommands, loadEvents]);

  useEffect(() => {
    const pending = timers.current;
    if (societyId) Promise.resolve().then(() => refreshAll(societyId));   // async fetch; state updates happen in callbacks
    const metadataChanged = (event: Event) => {
      if (societyId && (event as CustomEvent<{ societyId: string }>).detail?.societyId === societyId) void loadDashboard(societyId);
    };
    window.addEventListener("ems-device-metadata-changed", metadataChanged);
    return () => { pending.forEach(clearTimeout); window.removeEventListener("ems-device-metadata-changed", metadataChanged); };
  }, [societyId, refreshAll, loadDashboard]);

  const queue: QueueFn = useCallback(async (deviceId, command, slot, params = {}) => {
    if (!societyId) return false;
    const key = `${deviceId}:${command}:${slot}`;
    if (pending.has(key)) return false;                       // prevents accidental double submission
    setPending((p) => new Set(p).add(key));
    const at = new Date().toISOString();
    try {
      const res = await api.post("/api/admin/pi-command", { idempotency_key: crypto.randomUUID(), society_id: societyId, device_id: deviceId, slot, command, params });
      setLast({ device_id: deviceId, kind: "queued", command, slot, command_id: res.data.command_id, sequence_no: res.data.sequence_no, duplicate: !!res.data.duplicate, at });
      await loadCommands(societyId, deviceId);
      // one targeted follow-up after the Pi's sync window; not a polling loop
      timers.current.push(setTimeout(() => { loadCommands(societyId, deviceId); loadDashboard(societyId); loadEvents(societyId); }, 4000));
      return true;
    } catch (e) {
      const { http, detail } = errorText(e);
      setLast({ device_id: deviceId, kind: "error", command, slot, http, detail, at });
      return false;
    } finally {
      setPending((p) => { const n = new Set(p); n.delete(key); return n; });
    }
  }, [societyId, pending, loadCommands, loadDashboard, loadEvents]);

  const setSlotConfig: SlotConfigFn = useCallback(async (deviceId, slot, patch) => {
    if (!societyId) return false;
    const key = `${deviceId}:slot-config:${slot}`;
    if (pending.has(key)) return false;
    setPending((p) => new Set(p).add(key));
    const at = new Date().toISOString();
    try {
      await api.post("/api/admin/slot-config", { society_id: societyId, device_id: deviceId, slot, ...patch });
      setLast({ device_id: deviceId, kind: "queued", command: patch.disabled === undefined ? "slot_rename" : patch.disabled ? "slot_disable" : "slot_enable", slot, command_id: "config", sequence_no: 0, duplicate: false, at });
      await loadDashboard(societyId);
      return true;
    } catch (e) {
      const { http, detail } = errorText(e);
      setLast({ device_id: deviceId, kind: "error", command: "slot_config", slot, http, detail, at });
      return false;
    } finally {
      setPending((p) => { const n = new Set(p); n.delete(key); return n; });
    }
  }, [societyId, pending, loadDashboard]);

  const isPending = (deviceId: string, command: string, slot = "") => pending.has(`${deviceId}:${command}:${slot}`);
  return { dash, commands, events, last, error, loading, queue, setSlotConfig, isPending, refresh: () => societyId && refreshAll(societyId) };
}
