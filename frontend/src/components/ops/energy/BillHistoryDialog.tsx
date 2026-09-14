"use client";
import { useEffect, useRef, useState } from "react";
import { btn, input, tone } from "../DashboardHeader";
import { WingCode, WINGS } from "./types";
import { BillHistory, EnergyReferenceData, dailyRate } from "./referenceTypes";
import { BillMonthEditor } from "./BillMonthEditor";

export function BillHistoryDialog({ societyId, deviceId, initialWing, data, readOnly, onClose, onSaved }: {
  societyId: string; deviceId: string; initialWing: WingCode; data: EnergyReferenceData; readOnly: boolean; onClose: () => void; onSaved: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [wing, setWing] = useState(initialWing);
  const [end, setEnd] = useState(data.wings[initialWing].history.end_month);
  const [saved, setSaved] = useState<BillHistory | null>(null);
  useEffect(() => { const node = dialog.current; node?.showModal(); return () => { node?.close(); }; }, []);
  return <dialog ref={dialog} data-testid="bill-history-dialog" aria-labelledby="bill-history-title" onCancel={onClose}
    className="fixed inset-0 m-auto w-[calc(100%-1rem)] max-w-2xl max-h-[90dvh] overflow-y-auto border border-cyan-700 bg-[#0f1520] text-white p-3 sm:p-5 backdrop:bg-black/70">
    <div className="flex items-start justify-between gap-3 mb-4">
      <h2 id="bill-history-title" data-testid="bill-history-title" className="text-base sm:text-lg font-bold">Historical Consumption Baseline</h2>
      <button data-testid="bills-close" aria-label="Close bill history" onClick={onClose} className={`${btn} ${tone.gray}`}>✕</button>
    </div>
    {saved ? <div data-testid="bills-save-popup" role="status" className="space-y-4 py-6">
      <div data-testid="bills-popup-wing" className="text-sm text-gray-400">Wing {saved.wing} · Historical baseline</div>
      <div data-testid="bills-popup-daily" className="text-lg text-cyan-200">Reference daily consumption: {dailyRate(saved.reference_daily_kwh)}</div>
      <div data-testid="bills-popup-months" className="text-sm">Based on {saved.valid_months} valid months</div>
      <button data-testid="bills-popup-done" onClick={onClose} className={`${btn} ${tone.cyan}`}>DONE</button>
    </div> : <>
      <div className="flex flex-wrap gap-3 mb-3">
        <label className="text-xs text-gray-400">Wing<select data-testid="bills-wing" aria-label="Bill history wing" value={wing} onChange={(e) => { const w = e.target.value as WingCode; setWing(w); setEnd(data.wings[w].history.end_month); }} className={`${input} block mt-1`}>{WINGS.map((w) => <option key={w} value={w}>Wing {w}</option>)}</select></label>
        <label className="text-xs text-gray-400">12 months ending<input data-testid="bills-end-month" aria-label="12 months ending" type="month" min="1971-01" max={new Date().toISOString().slice(0, 7)} value={end} onChange={(e) => setEnd(e.target.value)} className={`${input} block mt-1`} /></label>
      </div>
      {end && <BillMonthEditor key={`${wing}:${end}`} societyId={societyId} deviceId={deviceId} wing={wing} endMonth={end} readOnly={readOnly} onSaved={(history) => { setSaved(history); onSaved(); }} />}
    </>}
  </dialog>;
}