"use client";
import { useState } from "react";

type Row = { label: string; value: string; secret?: boolean };

// Displays freshly issued credentials exactly once (React state only; never persisted).
export function OneTimeSecret({ title, rows, testId, onDismiss }: { title: string; rows: Row[]; testId: string; onDismiss: () => void }) {
  const [copied, setCopied] = useState<string | null>(null);
  const copy = async (label: string, value: string) => {
    try { await navigator.clipboard.writeText(value); setCopied(label); setTimeout(() => setCopied(null), 1500); } catch { /* clipboard unavailable */ }
  };
  return (
    <div data-testid={testId} className="mt-4 rounded-lg border border-amber-500/40 bg-amber-500/5 p-4">
      <div className="flex justify-between items-start gap-4">
        <div>
          <div className="text-sm font-bold text-amber-300">{title}</div>
          <div className="text-[11px] text-amber-200/70 mt-0.5">Shown once. Copy it now — it cannot be retrieved later.</div>
        </div>
        <button data-testid={`${testId}-dismiss`} onClick={onDismiss} className="text-[10px] font-bold text-gray-400 hover:text-white">DISMISS</button>
      </div>
      <dl className="mt-3 space-y-2">
        {rows.map((r) => (
          <div key={r.label} className="flex items-center gap-3">
            <dt className="w-28 shrink-0 text-[10px] uppercase tracking-wide text-gray-500">{r.label}</dt>
            <dd data-testid={`${testId}-${r.label.toLowerCase().replace(/\s+/g, "-")}`}
              className={`flex-1 truncate font-mono text-xs ${r.secret ? "text-amber-200" : "text-gray-200"}`}>{r.value}</dd>
            <button data-testid={`${testId}-copy-${r.label.toLowerCase().replace(/\s+/g, "-")}`} onClick={() => copy(r.label, r.value)}
              className="text-[10px] font-bold px-2 py-1 rounded border border-gray-700 text-gray-300 hover:bg-gray-800">
              {copied === r.label ? "COPIED" : "COPY"}
            </button>
          </div>
        ))}
      </dl>
    </div>
  );
}
