"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import api from "@/lib/api";
import { errorText } from "../types";
import { CalculationMode, EnergySummary, EnergyComparison, ManualEntryInput } from "./types";
import { readRequest } from "../readRequest";
import { summaryValid, comparisonValid } from "./readValidation";
import { useEnergyHistory } from "./useEnergyHistory";

// One device-scoped source. Mode writes are calculation-only; manual writes append
// existing adjustments. Neither path uses hardware commands or allocation writes.
export function useEnergy(societyId: string | null, deviceId: string | null) {
  const key = `${societyId}:${deviceId}`;
  const request = useRef(0);
  const controller = useRef(new AbortController());
  const history = useEnergyHistory();
  const loadHistory = history.load;
  const pendingWrites = useRef(new Set<string>());
  const [busyKeys, setBusyKeys] = useState<string[]>([]);
  const [loadedKey, setLoadedKey] = useState<string | null>(null);
  const [summary, setSummary] = useState<EnergySummary | null>(null);
  const [comparison, setComparison] = useState<EnergyComparison | null>(null);
  const [write, setWrite] = useState<{ key: string; token: number; busy: boolean; error: string; notice: string } | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    const version = ++request.current;
    controller.current.abort(); controller.current = new AbortController();
    const signal = controller.current.signal;
    const current = () => version === request.current && !signal.aborted;
    if (!societyId || !deviceId) { setSummary(null); setComparison(null); setLoading(false); return; }
    const q = `society_id=${societyId}&device_id=${deviceId}`;
    setLoading(true); setSummary(null); setComparison(null); setError("");
    loadHistory(q, signal, current);
    const comparisonRead = readRequest(`/api/energy/graph/comparison?${q}&days=30`, signal)
      .then((value) => ({ status: "fulfilled" as const, value }), (reason) => ({ status: "rejected" as const, reason }));
    let snapshot: EnergySummary | null = null;
    try {
      const { data } = await readRequest(`/api/energy/summary?${q}`, signal);
      if (!current()) return;
      if (!summaryValid(data, deviceId)) throw new Error("Invalid energy summary");
      snapshot = data; setSummary(data);
    } catch (e) { if (current()) setError(`Energy summary unavailable: ${errorText(e).detail}`); }
    finally { if (current()) { setLoadedKey(`${societyId}:${deviceId}`); setLoading(false); } }
    // Summary and each optional panel publish independently; comparison still
    // requires matching device/mode/version/day before becoming visible.
    const c = await comparisonRead;
    if (!current() || !snapshot) return;
    const s = { value: { data: snapshot } };
    const validComparison = c.status === "fulfilled" && comparisonValid(c.value.data) && c.value.data.device_id === deviceId
      && c.value.data.mode === s.value.data.calculation?.mode && c.value.data.version === s.value.data.calculation?.version
      && c.value.data.operating_date === s.value.data.as_of_operating_date
      && (!snapshot.references?.included_wings || JSON.stringify(c.value.data.included_wings) === JSON.stringify(snapshot.references.included_wings));
    setComparison(validComparison ? c.value.data as EnergyComparison : null);
    if (!validComparison) setError("Comparison unavailable for the current mode/day; refresh energy data");
  }, [societyId, deviceId, loadHistory]);

  useEffect(() => { let active = true; const counter = request, reads = controller; Promise.resolve().then(() => { if (active) void load(); }); return () => { active = false; ++counter.current; reads.current.abort(); }; }, [load]);
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
  return { summary: current ? summary : null, comparison: current ? comparison : null, monthly: current ? history.monthly : null, allocation: current ? history.allocation : null,
    events: current ? history.events : [], entries: current ? history.entries : [], panelErrors: current ? history.panelErrors : [], error: current ? error : "", loading: !!societyId && !!deviceId && (loading || !current),
    refresh: () => { setWrite((previous) => previous?.key === key ? { ...previous, error: "", notice: "" } : previous); return load(); },
    saving: busyKeys.includes(key), saveError: write?.key === key ? write.error : "", notice: write?.key === key ? write.notice : "",
    setMode: (mode: CalculationMode) => mutate(mode), addEntry: (entry: ManualEntryInput) => mutate(null, entry) };
}
