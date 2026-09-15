"use client";
import { btn, input, tone } from "../DashboardHeader";
import { CalendarComparison } from "./types";
import { monthLabel } from "./comparisonLabels";

export function ComparisonPeriod({ society, controller, operatingDate, month, data, onChange }: {
  society: string; controller: string; operatingDate?: string; month: string; data: CalendarComparison | null; onChange: (month: string) => void;
}) {
  const today = data?.calendar_today || new Date().toISOString().slice(0, 10);
  const stale = operatingDate && operatingDate < today, ahead = operatingDate && operatingDate > today;
  return <section data-testid="comparison-period" className="border-y border-[#1e2a3a] py-3 space-y-2">
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0 text-xs text-gray-400 space-y-1 break-words"><div data-testid="comparison-society-controller">{society} · Controller {controller}</div><div data-testid="comparison-operating-date">Pi operating day: {operatingDate || "UNAVAILABLE"} · Calendar date: {today} UTC</div></div>
      <div className="flex flex-wrap items-end gap-2"><label className="text-xs text-gray-400">Calendar-month comparison<input data-testid="comparison-month-select" aria-label="Calendar-month comparison" type="month" min="1971-01" max={today.slice(0, 7)} value={month} onChange={(e) => onChange(e.target.value)} className={`${input} block mt-1 max-w-full`} /></label><button data-testid="comparison-operating-view" onClick={() => onChange("")} className={`${btn} ${tone.gray}`}>PI DAY VIEW</button></div>
    </div>
    <div data-testid="comparison-period-label" className="text-xs text-cyan-200">{month ? `${monthLabel(month)} · calendar-month daily values${data ? ` · ${data.period.start} to ${data.period.end}` : ""}` : "Daily values end on the Pi operating day"}</div>
    {(stale || ahead) && <div data-testid="comparison-operating-date-warning" role="status" className="text-xs text-amber-300">{stale ? "Pi operating day is behind the calendar date." : "Pi operating day is ahead of the calendar date."} Operating-day cards use {operatingDate}; monthly charts use the selected calendar dates.</div>}
  </section>;
}