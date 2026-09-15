"use client";
import { useCallback, useState } from "react";
import { readRequest, record } from "../readRequest";
import { eventsValid } from "../operationsValidation";
import { errorText, EventRow } from "../types";
import { monthlyValid, allocationValid, entriesValid } from "./readValidation";
import { MonthlyGeneration, AllocationConfig, ManualEntry, DEFAULT_MONTHS, isAllocationEvent } from "./types";

export function useEnergyHistory() {
  const [monthly, setMonthly] = useState<MonthlyGeneration | null>(null);
  const [allocation, setAllocation] = useState<AllocationConfig | null>(null);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [entries, setEntries] = useState<ManualEntry[]>([]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const load = useCallback((q: string, signal: AbortSignal, current: () => boolean) => {
    setMonthly(null); setAllocation(null); setEvents([]); setEntries([]); setErrors({});
    const panel = (name: string, url: string, publish: (data: unknown) => boolean) => {
      void readRequest(url, signal).then(({ data }) => {
        if (current() && !publish(data)) throw new Error("Invalid response");
      }).catch((e) => { if (current()) setErrors((prev) => ({ ...prev, [name]: `${name} unavailable: ${errorText(e).detail}` })); });
    };
    panel("Monthly generation", `/api/energy/generation/monthly?${q}&months=${DEFAULT_MONTHS}`, (d) => { if (!monthlyValid(d)) return false; setMonthly(d); return true; });
    panel("Allocation configuration", `/api/energy/allocation?${q}`, (d) => { if (!record(d) || !allocationValid(d.allocation)) return false; setAllocation(d.allocation); return true; });
    panel("Event history", `/api/admin/pi-events?${q}&latest=100`, (d) => { if (!record(d) || !eventsValid(d.events)) return false; setEvents(d.events.filter((r) => isAllocationEvent(r.level))); return true; });
    panel("Adjustment history", `/api/energy/adjustments?${q}&limit=50`, (d) => { if (!record(d) || !entriesValid(d.rows)) return false; setEntries(d.rows); return true; });
  }, []);
  return { monthly, allocation, events, entries, panelErrors: Object.values(errors), load };
}