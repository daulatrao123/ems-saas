"use client";
import { useEffect, useRef, useState } from "react";
import api from "@/lib/api";
import { errorText } from "../types";
import { CalendarComparison, EnergySummary, WINGS } from "./types";

export function useCalendarComparison(societyId: string, summary: EnergySummary | null, month: string, revision: number) {
  const device = summary?.device_id, mode = summary?.calculation.mode, version = summary?.calculation.version, day = summary?.as_of_operating_date;
  const key = `${societyId}:${device}:${mode}:${version}:${day}:${month}:${revision}`;
  const currentKey = useRef(key); currentKey.current = key;
  const [result, setResult] = useState<{ key: string; data: CalendarComparison | null; error: string } | null>(null);
  useEffect(() => {
    if (!device || !month || !mode || !day) return;
    const controller = new AbortController();
    const q = new URLSearchParams({ society_id: societyId, device_id: device, month });
    api.get(`/api/energy/graph/comparison?${q}`, { signal: controller.signal, timeout: 12000 })
      .then(({ data }: { data: CalendarComparison }) => {
        if (controller.signal.aborted || currentKey.current !== key) return;
        const rowsValid = (rows: unknown) => Array.isArray(rows) && rows.length > 0 && rows.length <= 31 && rows.every((row) => row && typeof row.date === "string" && row.date.startsWith(`${month}-`)
          && [row.generated_kwh, row.consumed_kwh].every((v) => v === null || (typeof v === "number" && Number.isFinite(v) && v >= 0))
          && (row.generation_minus_consumption_kwh === null || (typeof row.generation_minus_consumption_kwh === "number" && Number.isFinite(row.generation_minus_consumption_kwh)))
          && (row.generated_kwh === null || row.generation_source === "PHYSICAL")
          && (row.consumed_kwh === null || row.consumption_source === (mode === "MANUAL" ? "HISTORICAL" : "PHYSICAL")));
        const valid = data?.device_id === device && data.mode === mode && data.version === version && data.operating_date === day
          && data.period?.kind === "CALENDAR_MONTH" && data.period.month === month && data.period.start === `${month}-01`
          && typeof data.period.end === "string" && data.period.end.startsWith(`${month}-`) && [28, 29, 30, 31].includes(data.period.calendar_days)
          && rowsValid(data.society?.rows) && WINGS.every((wing) => data.wings?.[wing]?.wing === wing && rowsValid(data.wings[wing].rows));
        setResult({ key, data: valid ? data : null, error: valid ? "" : "Month comparison does not match the current controller/mode/day. Refresh to retry." });
      }).catch((e) => { if (!controller.signal.aborted && currentKey.current === key) setResult({ key, data: null, error: `Month comparison unavailable: ${errorText(e).detail}` }); });
    return () => controller.abort();
  }, [societyId, device, mode, version, day, month, key]);
  const current = result?.key === key;
  return { data: current ? result.data : null, error: current ? result.error : "", loading: !!device && !!month && !current };
}