"use client";
import { MonthRow, fmtKwh } from "./types";

// Dependency-free monthly bar chart (SVG). Months without physical data render as an explicit UNAVAILABLE marker, not a zero bar.
export function MonthlyBars({ rows, unit }: { rows: MonthRow[]; unit: string }) {
  const W = 560, H = 180, PAD = { l: 44, r: 8, t: 12, b: 28 };
  const iw = W - PAD.l - PAD.r, ih = H - PAD.t - PAD.b;
  const max = Math.max(1, ...rows.map((r) => r.generation_kwh ?? 0));
  const slot = rows.length ? iw / rows.length : iw; const bw = Math.min(56, slot * 0.6);
  const ticks = [0, 0.5, 1].map((f) => ({ y: PAD.t + ih - f * ih, v: max * f }));
  return (
    <svg data-testid="energy-monthly-chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMinYMid meet" className="w-full max-w-3xl h-44 font-mono" role="img" aria-labelledby="monthly-generation-heading">
      {ticks.map((t) => (
        <g key={t.y}><line x1={PAD.l} x2={W - PAD.r} y1={t.y} y2={t.y} stroke="#1e2a3a" strokeDasharray="3 3" /><text x={PAD.l - 6} y={t.y + 3} textAnchor="end" fontSize="9" fill="#6b7280">{t.v.toFixed(0)}</text></g>
      ))}
      <text x={4} y={PAD.t} fontSize="8" fill="#6b7280">{unit}</text>
      {rows.map((r, i) => {
        const cx = PAD.l + slot * i + slot / 2; const v = r.generation_kwh;
        const h = v == null ? 0 : (v / max) * ih; const y = PAD.t + ih - h;
        const fill = r.completeness === "COMPLETE" ? "#34d399" : "#f59e0b";
        return (
          <g key={r.month} data-testid={`energy-month-${r.month}`} data-kwh={v == null ? "UNAVAILABLE" : String(v)}>
            {v == null
              ? <text x={cx} y={PAD.t + ih - 6} textAnchor="middle" fontSize="8" fill="#6b7280">N/A</text>
              : <rect x={cx - bw / 2} y={y} width={bw} height={Math.max(1, h)} fill={fill} opacity={0.85}><title>{`${r.month}: ${fmtKwh(v)} (${r.completeness}, ${r.physical_days}/${r.expected_days} days)`}</title></rect>}
            {v != null && <text x={cx} y={y - 3} textAnchor="middle" fontSize="8" fill="#d1d5db">{v.toFixed(0)}</text>}
            <text x={cx} y={H - 10} textAnchor="middle" fontSize="9" fill="#9ca3af">{r.month}</text>
          </g>
        );
      })}
    </svg>
  );
}
