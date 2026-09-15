"use client";
import { btn, tone } from "../DashboardHeader";
import { BillHistory, dailyRate } from "./referenceTypes";
import { fmtKwh } from "./types";
import { monthLabel } from "./comparisonLabels";

export function BillSaveConfirmation({ history, changedMonths, operatingDate, onClose }: {
  history: BillHistory; changedMonths: string[]; operatingDate: string; onClose: () => void;
}) {
  const rows = history.months.filter((row) => changedMonths.includes(row.month));
  const last = rows[rows.length - 1]?.month;
  return <div data-testid="bills-save-popup" role="status" className="space-y-4 py-3">
    <div data-testid="bills-popup-wing" className="text-sm text-emerald-300">Saved successfully · Wing {history.wing}</div>
    <div className="space-y-3">{rows.map((row) => <div key={row.month} data-testid={`bills-saved-month-${row.month}`} className="border-l-2 border-cyan-700 pl-3 text-xs space-y-1">
      <div data-testid={`bills-saved-month-label-${row.month}`} className="text-white font-bold">{monthLabel(row.month)}</div>
      <div data-testid={`bills-saved-month-rate-${row.month}`} className="text-cyan-200">{fmtKwh(row.consumption_kwh)} ÷ {row.days} days = {dailyRate(row.daily_kwh)} · BILL REFERENCE</div>
      <div data-testid={`bills-saved-month-applies-${row.month}`} className="text-gray-400">{operatingDate.startsWith(row.month) ? `Applies to Pi operating day ${operatingDate}.` : `Saved for ${row.month}; not the Pi operating month ${operatingDate.slice(0, 7)}. Available in the selected-month daily table.`}</div>
    </div>)}</div>
    <button data-testid="bills-popup-done" onClick={onClose} className={`${btn} ${tone.cyan} w-full`}>{last ? `VIEW ${monthLabel(last).toUpperCase()} DAILY VALUES` : "DONE"}</button>
  </div>;
}