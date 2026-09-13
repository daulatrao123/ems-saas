"use client";
import { useCallback, useEffect, useState } from "react";
import api from "@/lib/api";
import { EventRow, errorText } from "../types";
import { AllocationConfig, DEFAULT_MONTHS, EnergySummary, MonthlyGeneration, isAllocationEvent } from "./types";

// Read-only energy data for one device: /summary, /generation/monthly (default 6 months), /allocation config and
// today's allocation events from the existing pi-events feed. No writes; explicit refresh only (no polling).
export function useEnergy(societyId: string | null, deviceId: string | null) {
  const [summary, setSummary] = useState<EnergySummary | null>(null);
  const [monthly, setMonthly] = useState<MonthlyGeneration | null>(null);
  const [allocation, setAllocation] = useState<AllocationConfig | null>(null);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!societyId || !deviceId) { setLoading(false); return; }
    const q = `society_id=${societyId}&device_id=${deviceId}`;
    setLoading(true);
    const [s, m, a, e] = await Promise.allSettled([
      api.get(`/api/energy/summary?${q}`), api.get(`/api/energy/generation/monthly?${q}&months=${DEFAULT_MONTHS}`),
      api.get(`/api/energy/allocation?${q}`), api.get(`/api/admin/pi-events?${q}&latest=100`),
    ]);
    setSummary(s.status === "fulfilled" ? s.value.data : null);
    setMonthly(m.status === "fulfilled" ? m.value.data : null);
    setAllocation(a.status === "fulfilled" ? a.value.data.allocation : null);
    setEvents(e.status === "fulfilled" ? (e.value.data.events as EventRow[]).filter((r) => isAllocationEvent(r.level)) : []);
    const failed = [s, m, a, e].find((r) => r.status === "rejected") as PromiseRejectedResult | undefined;
    setError(failed ? errorText(failed.reason).detail : "");
    setLoading(false);
  }, [societyId, deviceId]);

  useEffect(() => { Promise.resolve().then(load); }, [load]);
  return { summary, monthly, allocation, events, error, loading, refresh: load };
}
