"use client";
import { useState } from "react";
import { btn, input, label, tone } from "../DashboardHeader";
import { ManualEntryInput, WingCode } from "./types";

export function ManualGenerationEntry({ wing, operatingDate, disabled, onAdd }: {
  wing: WingCode; operatingDate: string; disabled: boolean; onAdd: (entry: ManualEntryInput) => Promise<boolean>;
}) {
  const [day, setDay] = useState(operatingDate);
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const amount = Number(value);
  const valid = value.trim() !== "" && Number.isFinite(amount) && Math.abs(amount) <= 1000000 && !!day && !!reason.trim();
  return (
    <form data-testid={`manual-entry-form-${wing}`} className="border-t border-[#1e2a3a] pt-3 space-y-2"
      onSubmit={async (event) => {
        event.preventDefault();
        if (!valid || disabled) return;
        if (await onAdd({ wing, operating_date: day, kind: "MANUAL_GENERATION", value_kwh: amount, reason: reason.trim() })) {
          setValue(""); setReason("");
        }
      }}>
      <div data-testid={`manual-entry-contract-${wing}`} className={label}>Add generation · additive entry</div>
      <label className="block text-[11px] text-gray-400">Operating date
        <input data-testid={`manual-entry-date-${wing}`} aria-label={`Wing ${wing} entry date`} type="date" required value={day} disabled={disabled} onChange={(e) => setDay(e.target.value)} className={`${input} mt-1 w-full`} />
      </label>
      <label className="block text-[11px] text-gray-400">Generation adjustment (kWh)
        <input data-testid={`manual-entry-value-${wing}`} aria-label={`Wing ${wing} generation adjustment`} type="number" step="0.0001" min={-1000000} max={1000000} required value={value} disabled={disabled} onChange={(e) => setValue(e.target.value)} className={`${input} mt-1 w-full`} />
      </label>
      <label className="block text-[11px] text-gray-400">Reason
        <input data-testid={`manual-entry-reason-${wing}`} aria-label={`Wing ${wing} entry reason`} required maxLength={300} value={reason} disabled={disabled} onChange={(e) => setReason(e.target.value)} className={`${input} mt-1 w-full`} />
      </label>
      <button data-testid={`manual-entry-submit-${wing}`} type="submit" disabled={disabled || !valid} className={`${btn} ${tone.amber} w-full`}>ADD ENTRY</button>
    </form>
  );
}