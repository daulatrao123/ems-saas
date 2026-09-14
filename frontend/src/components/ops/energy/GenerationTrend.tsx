"use client";
import { GenerationPoint, WingCode, fmtKwh } from "./types";

// Date-aligned, source-qualified daily marks. Missing dates have no bar; manual
// corrections can be negative. Each exact value stays readable without hovering.
export function GenerationTrend({ wing, rows, source }: { wing: WingCode; rows: GenerationPoint[]; source: "PHYSICAL" | "MANUAL" }) {
  const values = rows.map((r) => r.generation_source === source && r.generated_kwh != null && Number.isFinite(r.generated_kwh) ? r.generated_kwh : null);
  const scale = Math.max(1, ...values.filter((v): v is number => v !== null).map(Math.abs));
  return (
    <div data-testid={`generation-trend-${wing}`} className="border-t border-[#1e2a3a] pt-2">
      <div data-testid={`generation-trend-source-${wing}`} className="text-[10px] text-gray-400">7-day generation · {source}</div>
      <ul className="mt-2 space-y-1 font-mono text-[10px]">
        {rows.map((row, i) => <li key={row.date} data-testid={`generation-trend-${wing}-${row.date}`} className="grid grid-cols-[42px_minmax(0,1fr)_auto] items-center gap-2">
          <span className="text-gray-500">{row.date.slice(5)}</span>
          <span className="h-1.5 bg-[#0a0e17]" aria-hidden="true">{values[i] !== null && <span className={`block h-full ${source === "MANUAL" ? "bg-amber-400" : "bg-emerald-400"}`} style={{ width: `${Math.abs(values[i]) / scale * 100}%` }} />}</span>
          <span data-testid={`generation-trend-value-${wing}-${row.date}`} className={values[i] === null ? "text-gray-500" : "text-gray-300"}>{fmtKwh(values[i])}</span>
        </li>)}
      </ul>
    </div>
  );
}