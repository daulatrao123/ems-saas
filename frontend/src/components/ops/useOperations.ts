"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import api from "@/lib/api";
import { LastResponse, errorText } from "./types";
import { useOperationsRead } from "./useOperationsRead";

export type QueueFn = (deviceId: string, command: string, slot: string, params?: Record<string, unknown>) => Promise<boolean>;
// LOGICAL slot enable/disable (slot_configs.disabled). Never touches GPIO, toggle or contactor state.
export type SlotConfigFn = (deviceId: string, slot: string, patch: { disabled?: boolean; display_name?: string }) => Promise<boolean>;

// Single data source for the operational dashboard. Explicit, targeted refreshes only — no polling.
export function useOperations(societyId: string | null) {
  const { dash, commands, events, error, panelErrors, loading, loadDashboard, loadCommands, loadEvents, refreshAll } = useOperationsRead(societyId);
  const [last, setLast] = useState<LastResponse | null>(null);
  const [pending, setPending] = useState<Set<string>>(new Set());  // `${deviceId}:${command}:${slot}` in flight
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(() => {
    const pending = timers.current;
    return () => { pending.forEach(clearTimeout); };
  }, [societyId]);

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
  return { dash, commands, events, last, error, panelErrors, loading, queue, setSlotConfig, isPending, refresh: () => societyId && refreshAll(societyId) };
}
