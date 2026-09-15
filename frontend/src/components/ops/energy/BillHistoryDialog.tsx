"use client";
import { useEffect, useRef, useState } from "react";
import { btn, input, tone } from "../DashboardHeader";
import { WingCode, WINGS } from "./types";
import { BillHistory, EnergyReferenceData } from "./referenceTypes";
import { BillMonthEditor } from "./BillMonthEditor";
import { BillSaveConfirmation } from "./BillSaveConfirmation";
import { monthLabel } from "./comparisonLabels";

export function BillHistoryDialog({ societyId, societyName, controllerName, operatingDate, deviceId, initialWing, data, readOnly, onClose, onSavedMonth }: {
  societyId: string; societyName: string; controllerName: string; operatingDate: string; deviceId: string; initialWing: WingCode; data: EnergyReferenceData; readOnly: boolean; onClose: () => void; onSavedMonth: (month: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [wing, setWing] = useState(initialWing);
  const [end, setEnd] = useState(new Date().toISOString().slice(0, 7));
  const [saved, setSaved] = useState<{ history: BillHistory; months: string[] } | null>(null);
  useEffect(() => { const node = dialog.current; node?.showModal(); return () => { node?.close(); }; }, []);
  return <dialog ref={dialog} data-testid="bill-history-dialog" aria-labelledby="bill-history-title" onCancel={onClose}
    className="fixed inset-0 m-auto w-[calc(100%-1rem)] max-w-2xl max-h-[90dvh] overflow-y-auto border border-cyan-700 bg-[#0f1520] text-white p-3 sm:p-5 backdrop:bg-black/70">
    <div className="flex items-start justify-between gap-3 mb-3"><h2 id="bill-history-title" data-testid="bill-history-title" className="text-base sm:text-lg font-bold">Monthly consumption · kWh</h2><button data-testid="bills-close" aria-label="Close bill history" onClick={onClose} className={`${btn} ${tone.gray}`}>✕</button></div>
    <div data-testid="bills-context" className="text-xs text-gray-400 space-y-1 mb-4 break-words"><div>{societyName} · Controller {controllerName}</div><div data-testid="bills-controller-id">{deviceId} · Wing {saved?.history.wing || wing}</div><div data-testid="bills-operating-date">Pi operating day: {operatingDate} · bill for {monthLabel(operatingDate.slice(0, 7))} applies to that day</div></div>
    {saved ? <BillSaveConfirmation history={saved.history} changedMonths={saved.months} operatingDate={operatingDate} onClose={onClose} /> : <>
      <div className="flex flex-wrap gap-3 mb-3">
        <label className="text-xs text-gray-400">Wing<select data-testid="bills-wing" aria-label="Bill history wing" value={wing} onChange={(e) => setWing(e.target.value as WingCode)} className={`${input} block mt-1`}>{WINGS.map((w) => <option key={w} value={w}>Wing {w}</option>)}</select></label>
        <label className="text-xs text-gray-400">12 months ending<input data-testid="bills-end-month" aria-label="12 months ending" type="month" min="1971-01" max={new Date().toISOString().slice(0, 7)} value={end} onChange={(e) => setEnd(e.target.value)} className={`${input} block mt-1`} /></label>
      </div>
      <div data-testid="bills-known-months" className="mb-3 text-xs text-gray-500">Wing {wing} · {data.wings[wing].history.valid_months} months in its last saved history window</div>
      {end && <BillMonthEditor key={`${wing}:${end}`} societyId={societyId} deviceId={deviceId} wing={wing} endMonth={end} readOnly={readOnly} onSaved={(history, changedMonths) => { setSaved({ history, months: changedMonths }); const latest = [...changedMonths].sort().pop(); if (latest) onSavedMonth(latest); }} />}
    </>}
  </dialog>;
}