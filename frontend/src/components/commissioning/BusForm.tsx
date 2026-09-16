"use client";
import { useMemo } from "react";
import { Setup, Save } from "./types";
import { useDraft } from "./hooks";
import { Actions, Alert, Field, Section, SelectField } from "./controls";

export function BusForm({ setup, save, disabled, mark }: { setup: Setup; save: Save; disabled: boolean; mark: (n: string, d: boolean) => void }) {
  const initial = useMemo(() => ({ port: setup.bus.port || "", baudrate: String(setup.bus.serial?.baudrate ?? 9600), bytesize: String(setup.bus.serial?.bytesize ?? 8), parity: setup.bus.serial?.parity ?? "N", stopbits: String(setup.bus.serial?.stopbits ?? 1), timeout: String(setup.bus.serial?.timeout_s ?? 0.5) }), [setup.bus]);
  const d = useDraft(initial, setup.config_version, "bus", mark), v = d.draft;
  const change = (key: keyof typeof v, val: string) => d.change({ ...v, [key]: val });
  const valid = v.port.startsWith("/dev/") && Number.isInteger(+v.baudrate) && +v.baudrate >= 300 && +v.baudrate <= 115200 && +v.timeout >= 0.05 && +v.timeout <= 5;
  return <Section id="setup-bus" number="01" title="RS485 bus"><form data-testid="setup-bus-form" onSubmit={async e => { e.preventDefault(); if (!valid || disabled) return; if (await save("put", "bus", { expected_version: d.base, port: v.port.trim(), serial: { baudrate: +v.baudrate, bytesize: +v.bytesize, parity: v.parity, stopbits: +v.stopbits, timeout_s: +v.timeout } }, "Bus configuration")) d.saved(); }}>
    <fieldset disabled={disabled} className="min-w-0 space-y-4"><Field id="bus-port" label="Adapter path" value={v.port} placeholder="/dev/serial/by-id/…" onChange={e => change("port", e.target.value)} required />
      <div className="grid grid-cols-2 gap-4"><Field id="bus-baudrate" label="Baud rate" type="number" min={300} max={115200} step={1} value={v.baudrate} onChange={e => change("baudrate", e.target.value)} required />
        <SelectField id="bus-parity" label="Parity" value={v.parity} onChange={e => change("parity", e.target.value)}>{["N", "E", "O"].map(x => <option key={x}>{x}</option>)}</SelectField>
        <SelectField id="bus-bytesize" label="Data bits" value={v.bytesize} onChange={e => change("bytesize", e.target.value)}>{[7, 8].map(x => <option key={x}>{x}</option>)}</SelectField>
        <SelectField id="bus-stopbits" label="Stop bits" value={v.stopbits} onChange={e => change("stopbits", e.target.value)}>{[1, 2].map(x => <option key={x}>{x}</option>)}</SelectField>
        <Field id="bus-timeout" label="Timeout · seconds" type="number" min={0.05} max={5} step={0.05} value={v.timeout} onChange={e => change("timeout", e.target.value)} required /></div>
    </fieldset><Alert id="bus-defaults-note">Serial defaults: 9600 · 8N1 · 0.5 s. Installed meter settings must match.</Alert><Actions id="bus" dirty={d.dirty} disabled={disabled || !valid} reset={d.reset} label="Save bus" />
  </form></Section>;
}