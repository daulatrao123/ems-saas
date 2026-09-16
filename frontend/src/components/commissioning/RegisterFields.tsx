"use client";
import { Field, SelectField } from "./controls";
import { Register } from "./types";

export type RegisterDraft = { address: string; function: string; type: string; word_order: string; scale: string; unit: string };
export const registerDraft = (r: Register | undefined, power = false): RegisterDraft => ({ address: r?.address == null ? "" : String(r.address), function: String(r?.function ?? 3), type: r?.type ?? "float32", word_order: r?.word_order ?? "big", scale: String(r?.scale ?? 1), unit: r?.unit ?? (power ? "kW" : "kWh") });
export const registerValid = (r: RegisterDraft) => r.address.trim() !== "" && Number.isInteger(+r.address) && +r.address >= 0 && +r.address <= 65535 && r.scale.trim() !== "" && Number.isFinite(+r.scale) && +r.scale > 0;
export const registerPayload = (r: RegisterDraft) => ({ ...r, address: +r.address, function: +r.function, scale: +r.scale });
export function RegisterFields({ id, power = false, draft, change }: { id: string; power?: boolean; draft: RegisterDraft; change: (v: RegisterDraft) => void }) {
  const update = (key: keyof RegisterDraft, v: string) => change({ ...draft, [key]: v });
  return <div data-testid={`${id}-fields`} className="grid min-w-0 grid-cols-2 gap-4 sm:grid-cols-3">
    <Field id={`${id}-address`} label="Register address · zero-based" type="number" min={0} max={65535} step={1} value={draft.address} required onChange={e => update("address", e.target.value)} />
    <SelectField id={`${id}-function`} label="Function code" value={draft.function} onChange={e => update("function", e.target.value)}><option value="3">03 · Holding</option><option value="4">04 · Input</option></SelectField>
    <SelectField id={`${id}-type`} label="Value type" value={draft.type} onChange={e => update("type", e.target.value)}>{["uint16", "int16", "uint32", "int32", "float32", "uint64", "int64", "float64"].map(v => <option key={v}>{v}</option>)}</SelectField>
    <SelectField id={`${id}-word-order`} label="Word order" value={draft.word_order} onChange={e => update("word_order", e.target.value)}><option value="big">Big endian</option><option value="little">Little endian</option></SelectField>
    <Field id={`${id}-scale`} label="Scale multiplier" type="number" min="0.000000001" step="any" required value={draft.scale} onChange={e => update("scale", e.target.value)} />
    <SelectField id={`${id}-unit`} label="Register unit" value={draft.unit} onChange={e => update("unit", e.target.value)}>{(power ? ["kW", "W", "MW"] : ["kWh", "Wh", "MWh"]).map(v => <option key={v}>{v}</option>)}</SelectField>
  </div>;
}