"use client";
import { btn, tone } from "./DashboardHeader";

export type Confirm = { title: string; body: string; action: string; danger?: boolean; onConfirm: () => void };

export function ConfirmDialog({ c, onClose }: { c: Confirm | null; onClose: () => void }) {
  if (!c) return null;
  return (
    <div data-testid="confirm-dialog" className="fixed inset-0 z-[200] grid place-items-center bg-black/70 p-4" onClick={onClose}>
      <div className={`w-full max-w-md border ${c.danger ? "border-red-500/60" : "border-amber-500/50"} bg-[#0f1520] p-5`} onClick={(e) => e.stopPropagation()}>
        <div className={`text-[10px] uppercase tracking-[0.14em] ${c.danger ? "text-red-400" : "text-amber-300"}`}>{c.danger ? "Destructive action" : "Confirm operation"}</div>
        <h3 className="mt-1 text-lg font-bold text-white">{c.title}</h3>
        <p className="mt-2 text-sm text-gray-300 whitespace-pre-line">{c.body}</p>
        <div className="mt-5 flex justify-end gap-2">
          <button data-testid="confirm-cancel" onClick={onClose} className={`${btn} ${tone.gray}`}>CANCEL</button>
          <button data-testid="confirm-accept" onClick={() => { c.onConfirm(); onClose(); }} className={`${btn} ${c.danger ? tone.red : tone.amber}`}>{c.action}</button>
        </div>
      </div>
    </div>
  );
}
