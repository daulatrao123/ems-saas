"use client";
import { useMemo } from "react";
import { Setup, Save, numberText, value } from "./types";
import { Actions, Alert, Field, Section, SelectField } from "./controls";
import { useDraft } from "./hooks";
import { RegisterFields, registerDraft, registerPayload, registerValid } from "./RegisterFields";

export function MeterForm({ setup, save, disabled, mark }: { setup: Setup; save: Save; disabled: boolean; mark: (n: string, d: boolean) => void }) {
  const m = setup.meters.M1;
  const initial = useMemo(() => ({ serial: m.serial || "", model: m.model || "", address: numberText(m.modbus_address), phases: String(m.phases || 1), ct: String(m.ct_ratio ?? 1), max: numberText(m.max_kw), enabled: m.enabled,
    energy: registerDraft(m.register_map?.registers.energy_total_kwh), power: registerDraft(m.register_map?.registers.power_kw, true), hasPower: !!m.register_map?.registers.power_kw, verified: m.register_map?.verified === true }), [m]);
  const d = useDraft(initial, setup.config_version, "m1", mark), v = d.draft;
  const change = <K extends keyof typeof v>(key: K, val: typeof v[K]) => d.change({ ...v, [key]: val, verified: key === "verified" ? Boolean(val) : false });
  const valid = v.serial.trim() && v.model.trim() && v.address !== "" && Number.isInteger(+v.address) && +v.address >= 1 && +v.address <= 247 && +v.ct >= 0.001 && +v.ct <= 100000 && (v.max === "" || (+v.max >= 0.001 && +v.max <= 100000)) && v.verified && registerValid(v.energy) && (!v.hasPower || registerValid(v.power)) && (!v.enabled || !!setup.bus.port);
  return <Section id="setup-m1" number="02" title="M1 · Physical generation meter"><p data-testid="m1-reported-status" className="mb-5 text-xs text-gray-400">Configured: {m.enabled ? "Enabled" : "Disabled"} · Communication: {m.comm_status || "UNKNOWN"} · Last reading: {m.last_seen || "UNKNOWN"}</p>
    <form data-testid="setup-m1-form" onSubmit={async e => { e.preventDefault(); if (!valid || disabled) return; if (await save("put", "meters/M1", { expected_version: d.base, serial: v.serial.trim(), model: v.model.trim(), modbus_address: +v.address, phases: +v.phases, ct_ratio: +v.ct, max_kw: value(v.max), enabled: v.enabled,
      register_map: { name: v.model.trim(), verified: true, registers: { energy_total_kwh: registerPayload(v.energy), ...(v.hasPower ? { power_kw: registerPayload(v.power) } : {}) } } }, "M1 configuration")) d.saved(); }}>
      <fieldset disabled={disabled} className="min-w-0 space-y-5"><div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field id="m1-model" label="Installed make / model" required maxLength={120} value={v.model} onChange={e => change("model", e.target.value)} placeholder="From meter label" />
        <Field id="m1-serial" label="Meter serial number" required maxLength={120} value={v.serial} onChange={e => change("serial", e.target.value)} />
        <Field id="m1-address" label="Modbus address" type="number" min={1} max={247} step={1} required value={v.address} onChange={e => change("address", e.target.value)} />
        <SelectField id="m1-phases" label="Phases" value={v.phases} onChange={e => change("phases", e.target.value)}><option value="1">1 phase</option><option value="3">3 phases</option></SelectField>
        <Field id="m1-ct-ratio" label="CT multiplier · primary ÷ secondary" type="number" min={0.001} max={100000} step="any" required value={v.ct} onChange={e => change("ct", e.target.value)} />
        <Field id="m1-max-kw" label="Maximum expected power · kW (optional)" type="number" min={0.001} max={100000} step="any" value={v.max} onChange={e => change("max", e.target.value)} />
      </div><div className="border-t border-gray-800 pt-5"><h3 data-testid="m1-register-title" className="mb-4 text-sm font-semibold text-gray-200">Cumulative generation register</h3><RegisterFields id="m1-energy-register" draft={v.energy} change={r => change("energy", r)} /></div>
      <label className="flex items-center gap-3 text-sm text-gray-300"><input data-testid="m1-power-register-toggle" type="checkbox" checked={v.hasPower} onChange={e => change("hasPower", e.target.checked)} />Instantaneous power register available</label>
      {v.hasPower && <RegisterFields id="m1-power-register" power draft={v.power} change={r => change("power", r)} />}
      <label className="flex items-start gap-3 text-sm text-gray-300"><input data-testid="m1-verified" className="mt-1" type="checkbox" checked={v.verified} onChange={e => change("verified", e.target.checked)} />Register addresses, units, scaling and word order checked against this meter’s datasheet</label>
      <label className="flex items-center gap-3 text-sm text-gray-300"><input data-testid="m1-enabled" type="checkbox" checked={v.enabled} onChange={e => d.change({ ...v, enabled: e.target.checked })} />Enable physical M1 readings</label></fieldset>
      {!setup.bus.port && <Alert id="m1-bus-required">Save the adapter path before enabling M1.</Alert>}
      <p data-testid="m1-authority" className="mt-4 text-xs text-gray-500">M1 remains physical in both calculation modes. Datasheet confirmation is not hardware verification.</p>
      <Actions id="m1" dirty={d.dirty} disabled={disabled || !valid} reset={d.reset} label="Save M1 configuration" />
    </form></Section>;
}