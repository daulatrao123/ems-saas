"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import api from "@/lib/api";
import { EventRow, errorText } from "../types";
import { AllocationConfig, CalculationMode, DEFAULT_MONTHS, EnergySummary, EnergyComparison, ManualEntry, ManualEntryInput, MonthlyGeneration, isAllocationEvent } from "./types";

// One device-scoped source. Mode writes are calculation-only; manual writes append
// existing adjustments. Neither path uses hardware commands or allocation writes.
export function useEnergy(societyId: string | null, deviceId: string | null) {
  const key = `${societyId}:${deviceId}`;
  const request = useRef(0);
  const pendingWrites = useRef(new Set<string>());
  const [busyKeys, setBusyKeys] = useState<string[]>([]);
  const [loadedKey, setLoadedKey] = useState<string | null>(null);
  const [summary, setSummary] = useState<EnergySummary | null>(null);
  const [comparison, setComparison] = useState<EnergyComparison | null>(null);
  const [monthly, setMonthly] = useState<MonthlyGeneration | null>(null);
  const [allocation, setAllocation] = useState<AllocationConfig | null>(null);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [entries, setEntries] = useState<ManualEntry[]>([]);
  const [write, setWrite] = useState<{ key: string; token: number; busy: boolean; error: string; notice: string } | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    const version = ++request.current;
    if (!societyId || !deviceId) { setLoading(false); return; }
    const q = `society_id=${societyId}&device_id=${deviceId}`;
    setLoading(true);
    setMonthly(null); setAllocation(null); setEvents([]); setEntries([]);
    const read = (url: string) => api.get(url, { timeout: 12000 });
    const core = Promise.allSettled([read(`/api/energy/summary?${q}`), read(`/api/energy/graph/comparison?${q}&days=30`)]);
    const secondary = Promise.allSettled([
      read(`/api/energy/generation/monthly?${q}&months=${DEFAULT_MONTHS}`), read(`/api/energy/allocation?${q}`),
      read(`/api/admin/pi-events?${q}&latest=100`), read(`/api/energy/adjustments?${q}&limit=50`),
    ]);
    const [s, c] = await core;
    if (version !== request.current) return;
    const validSummary = s.status === "fulfilled" && s.value.data?.device_id === deviceId;
    setSummary(validSummary ? s.value.data : null);
    const validComparison = validSummary && c.status === "fulfilled" && c.value.data?.device_id === deviceId
      && c.value.data.mode === s.value.data.calculation?.mode && c.value.data.version === s.value.data.calculation?.version
      && c.value.data.operating_date === s.value.data.as_of_operating_date;
    setComparison(validComparison ? c.value.data : null);
    const coreFailed = [s, c].find((r) => r.status === "rejected") as PromiseRejectedResult | undefined;
    setError(coreFailed ? errorText(coreFailed.reason).detail : !validSummary ? "Energy ownership unavailable for this device" : !validComparison ? "Comparison unavailable for the current mode/day; refresh energy data" : "");
    setLoadedKey(`${societyId}:${deviceId}`);
    setLoading(false);
    // Optional panels cannot prevent saved-bill/current comparison visibility.
    const [m, a, e, j] = await secondary;
    if (version !== request.current) return;
    setMonthly(m.status === "fulfilled" && m.value.data?.meter_id === "M1" ? m.value.data : null);
    setAllocation(a.status === "fulfilled" ? a.value.data?.allocation : null);
    setEvents(e.status === "fulfilled" && Array.isArray(e.value.data?.events) ? (e.value.data.events as EventRow[]).filter((r) => r && isAllocationEvent(r.level)) : []);
    setEntries(j.status === "fulfilled" && Array.isArray(j.value.data?.rows) ? j.value.data.rows : []);
    const failed = [m, a, e, j].find((r) => r.status === "rejected") as PromiseRejectedResult | undefined;
    const detail = failed ? `Optional energy history unavailable: ${errorText(failed.reason).detail}` : e.status === "fulfilled" && !Array.isArray(e.value.data?.events) ? "Event history response unavailable" : "";
    if (detail) setError((currentError) => currentError || detail);
  }, [societyId, deviceId]);

  useEffect(() => { const counter = request; Promise.resolve().then(load); return () => { ++counter.current; }; }, [load]);
  // A response from another selection must never be rendered, even for one frame.
  const current = loadedKey === key && !!societyId && !!deviceId;
  const mutate = async (mode: CalculationMode | null, entry?: ManualEntryInput) => {
    if (!current || loading || pendingWrites.current.has(key) || !summary?.calculation) return false;
    pendingWrites.current.add(key); // synchronous: two submit events in one render cannot append twice
    setBusyKeys((keys) => [...keys, key]);
    const token = ++request.current; // invalidate reads begun before this save
    setWrite({ key, token, busy: true, error: "", notice: "" });
    let saved = false;
    try {
      const identity = { society_id: societyId, device_id: deviceId };
      if (mode) await api.put("/api/energy/calculation-mode", { ...identity, mode, expected_version: summary.calculation.version });
      else await api.post("/api/energy/adjustments", { ...identity, ...entry });
      saved = true;
      if (token === request.current) await load(); // never refresh a device that has since been deselected
      setWrite((previous) => previous?.token === token ? { ...previous, notice: mode ? "Calculation mode saved" : "Generation entry added" } : previous);
    } catch (e) {
      const detail = errorText(e).detail;
      setWrite((previous) => previous?.token === token ? { ...previous, error: mode ? detail : `${detail}. Save not confirmed; check recent entries before adding again.` } : previous);
    } finally {
      pendingWrites.current.delete(key);
      setBusyKeys((keys) => keys.filter((pending) => pending !== key));
      setWrite((previous) => previous?.token === token ? { ...previous, busy: false } : previous);
    }
    return saved;
  };
  return { summary: current ? summary : null, comparison: current ? comparison : null, monthly: current ? monthly : null, allocation: current ? allocation : null,
    events: current ? events : [], entries: current ? entries : [], error: current ? error : "", loading: loading || !current,
    refresh: () => { setWrite((previous) => previous?.key === key ? { ...previous, error: "", notice: "" } : previous); return load(); },
    saving: busyKeys.includes(key), saveError: write?.key === key ? write.error : "", notice: write?.key === key ? write.notice : "",
    setMode: (mode: CalculationMode) => mutate(mode), addEntry: (entry: ManualEntryInput) => mutate(null, entry) };
}
