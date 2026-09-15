"use client";
import { ComparisonPoint, CalculationMode, fmtKwh } from "./types";

const valid = (v: number | null | undefined) => v != null && Number.isFinite(v) ? v : null;
const difference = (row: ComparisonPoint, excess: boolean) => {
  const value = valid(row.generation_minus_consumption_kwh);
  return value !== null && (value <= 0 || excess) ? value : null;
};
export const signedKwh = (value: number | null | undefined) => value != null && value > 0 ? `+${fmtKwh(value)}` : fmtKwh(value);

export function EnergyComparisonChart({ scope, rows, mode, excessEnabled, compact = false }: {
  scope: string; rows: ComparisonPoint[]; mode: CalculationMode; excessEnabled: boolean; compact?: boolean;
}) {
  const width = 640, top = 20, height = 140, bottom = top + height;
  const max = Math.max(1, ...rows.flatMap((r) => [valid(r.generated_kwh) || 0, valid(r.consumed_kwh) || 0]));
  const step = 590 / Math.max(rows.length, 1), bar = Math.min(18, step * 0.34);
  const hasData = rows.some((r) => valid(r.generated_kwh) !== null || valid(r.consumed_kwh) !== null);
  return <figure data-testid={`comparison-${scope}`} className="min-w-0 space-y-2 border-t border-[#1e2a3a] pt-3">
    <figcaption data-testid={`comparison-caption-${scope}`} className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
      <span className="text-emerald-300">Generation · physical</span>
      <span data-testid={`comparison-consumption-source-${scope}`} className="text-amber-300">Consumption · {mode === "MANUAL" ? "monthly bill / calendar days · reference" : "physical meters"}</span>
    </figcaption>
    {!hasData ? <div data-testid={`comparison-empty-${scope}`} className="py-8 text-xs text-gray-500">GENERATION / CONSUMPTION UNAVAILABLE</div> :
      <svg data-testid={`comparison-chart-${scope}`} viewBox={`0 0 ${width} 198`} role="img" aria-label={`${scope} daily generation and consumption in kWh`} className="w-full h-auto overflow-visible">
        {[0, 0.5, 1].map((fraction) => <g key={fraction}><line x1="40" x2="635" y1={bottom - height * fraction} y2={bottom - height * fraction} stroke="#243245" /><text x="36" y={bottom - height * fraction + 4} textAnchor="end" fill="#9ca3af" fontSize="10">{Number((max * fraction).toFixed(1))}</text></g>)}
        {rows.map((row, i) => <g key={row.date}>
          {(["generated_kwh", "consumed_kwh"] as const).map((key, j) => {
            const value = valid(row[key]);
            return value === null ? null : <rect data-testid={`comparison-${scope}-${key}-${row.date}`} key={key} x={43 + i * step + j * bar} y={bottom - value / max * height} width={bar - 1} height={Math.max(value === 0 ? 1 : 0, value / max * height)} fill={j ? "#fbbf24" : "#34d399"} opacity={j ? 0.85 : 1}>
              <title>{`${row.date} · ${j ? "Consumption" : "Generation"}: ${fmtKwh(value)} · ${j && mode === "MANUAL" ? "bill-derived reference" : "physical"}`}</title>
            </rect>;
          })}
          {(rows.length <= 7 || i % 5 === 0 || i === rows.length - 1) && <text x={43 + i * step + bar} y="180" textAnchor="middle" fill="#9ca3af" fontSize="10">{row.date.slice(5)}</text>}
        </g>)}
      </svg>}
    <details data-testid={`comparison-details-${scope}`} open={compact ? true : undefined} className="text-[10px]">
      <summary data-testid={`comparison-details-toggle-${scope}`} className="cursor-pointer text-gray-400">Daily values · kWh</summary>
      <div className="mt-2 grid grid-cols-[minmax(0,1fr)_repeat(3,minmax(0,1fr))] gap-1 text-gray-500 [&>span]:min-w-0 [&>span]:break-words"><span>Date</span><span>{compact ? "Gen." : "Generation"}</span><span>{compact ? "Cons." : "Consumption"}</span><span>Balance</span></div>
      {rows.map((row) => { const delta = difference(row, excessEnabled); return <div key={row.date} data-testid={`comparison-row-${scope}-${row.date}`} className="grid grid-cols-[minmax(0,1fr)_repeat(3,minmax(0,1fr))] gap-1 py-1 border-b border-[#1e2a3a] font-mono [&>span]:min-w-0 [&>span]:break-words">
        <span className="text-gray-500">{row.date.slice(5)}</span>
        <span data-testid={`comparison-generation-${scope}-${row.date}`} className="text-emerald-300">{valid(row.generated_kwh)?.toFixed(2) ?? "N/A"}</span>
        <span data-testid={`comparison-consumption-${scope}-${row.date}`} className="text-amber-300">{valid(row.consumed_kwh)?.toFixed(2) ?? "N/A"}</span>
        <span data-testid={`comparison-balance-${scope}-${row.date}`} className={delta !== null && delta < 0 ? "text-red-300" : "text-cyan-300"}>{delta === null ? (row.generation_minus_consumption_kwh !== null && row.generation_minus_consumption_kwh > 0 && !excessEnabled ? "—" : "N/A") : `${delta > 0 ? "+" : ""}${delta.toFixed(2)}`}</span>
      </div>; })}
    </details>
  </figure>;
}