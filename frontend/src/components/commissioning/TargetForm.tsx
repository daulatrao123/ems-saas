"use client";
import { useMemo } from "react";
import Link from "next/link";
import { Actions, Alert, Field, Section } from "./controls";
import { useDraft, useRead } from "./hooks";
import { Save, TargetData, targetValid, kwh } from "./types";
import { btn, tone } from "../ops/DashboardHeader";

function Editor({ data, wing, save, disabled, mark, refresh, operationsHref }: { data: TargetData; wing: string; save: Save; disabled: boolean; mark: (n: string, d: boolean) => void; refresh: () => void; operationsHref: string }) {
  const initial = useMemo(() => ({ percent: String(data.current?.adjustment_percent ?? 0), date: data.as_of_operating_date, reason: "" }), [data]);
  const d = useDraft(initial, data.delivery.desired_version, "target", mark), v = d.draft;
  const base = data.bills.daily_average_kwh, pct = Number(v.percent);
  const valid = base !== null && data.bills.months.length > 0 && v.percent.trim() !== "" && Number.isFinite(pct) && pct >= -100 && pct <= 500 && /^\d{4}-\d{2}-\d{2}$/.test(v.date);
  return <form data-testid={`target-${wing}-form`} onSubmit={async e => { e.preventDefault(); if (!valid || disabled) return; if (await save("post", "targets", { wing, adjustment_percent: pct, effective_from: v.date, reason: v.reason, expected_version: d.base, expected_basis_hash: data.basis_hash }, `Wing ${wing} target`)) { d.saved(); refresh(); } }}>
    <dl data-testid="target-basis" className="mb-5 grid grid-cols-2 gap-4 text-xs"><div><dt className="text-gray-500">Latest bill months</dt><dd data-testid="target-month-count" className="mt-1 font-mono text-gray-200">{data.bills.months.length} / 6</dd></div><div><dt className="text-gray-500">Covered days</dt><dd data-testid="target-covered-days" className="mt-1 font-mono text-gray-200">{data.bills.days}</dd></div><div><dt className="text-gray-500">Total consumption</dt><dd data-testid="target-total-kwh" className="mt-1 text-gray-200">{kwh(data.bills.total_kwh)}</dd></div><div><dt className="text-gray-500">Weighted daily reference</dt><dd data-testid="target-daily-average" className="mt-1 text-gray-200">{kwh(base)}</dd></div></dl>
    <p data-testid="target-bill-months" className="mb-5 break-words text-xs text-gray-500">{data.bills.months.map(m => m.month).join(" · ") || "No bill history"}</p>
    {base === null && <Alert id="target-bills-required">No usable bill history. <Link data-testid="target-bills-link" className="underline" href={`${operationsHref}#ops-energy`}>Open energy & bill history</Link></Alert>}
    <fieldset disabled={disabled} className="grid grid-cols-1 gap-4 sm:grid-cols-2"><Field id="target-percentage" label="Adjustment · %" type="number" min={-100} max={500} step="any" required value={v.percent} onChange={e => d.change({ ...v, percent: e.target.value })} />
      <Field id="target-effective-date" label="Effective from · operating date" type="date" required value={v.date} onChange={e => d.change({ ...v, date: e.target.value })} />
      <div className="sm:col-span-2"><Field id="target-reason" label="Reason (optional)" maxLength={300} value={v.reason} onChange={e => d.change({ ...v, reason: e.target.value })} /></div></fieldset>
    <div className="mt-5 border-l-2 border-cyan-400 bg-cyan-500/5 p-4"><p className="text-xs text-gray-400">Target preview · reference, not dispatch</p><p data-testid="target-preview" className="mt-2 break-words font-mono text-xl text-white">{valid ? `≈ ${kwh(base! * (1 + pct / 100))} / day` : "UNAVAILABLE"}</p><p data-testid="target-formula" className="mt-2 text-xs text-gray-500">Total bill kWh ÷ covered days × (1 + adjustment ÷ 100)</p></div>
    <p data-testid="target-current" className="mt-4 text-xs text-gray-400">Saved effective target: {data.current ? `${kwh(data.current.target_kwh_per_day)} / day · from ${data.current.effective_from}` : "UNAVAILABLE"}</p>
    <Actions id="target" dirty={d.dirty} disabled={disabled || !valid} allowInitial={!data.current} reset={d.reset} label={`Save Wing ${wing} target`} />
    {data.history.length > 0 && <details data-testid="target-history" className="mt-5 text-xs text-gray-400"><summary data-testid="target-history-toggle" className="cursor-pointer">Saved target history · {data.history.length}</summary><ul className="mt-3 space-y-2">{data.history.map(t => <li data-testid={`target-history-${t.id}`} key={t.id} className="break-words">{t.effective_from} · {kwh(t.target_kwh_per_day)} / day · {t.adjustment_percent}%</li>)}</ul></details>}
  </form>;
}

export function TargetForm({ sid, did, wing, setWing, save, disabled, dirty, mark, operationsHref, version }: { sid: string; did: string; wing: string; setWing: (w: string) => void; save: Save; disabled: boolean; dirty: boolean; mark: (n: string, d: boolean) => void; operationsHref: string; version: number }) {
  const read = useRead<TargetData>(`/api/energy/targets?society_id=${encodeURIComponent(sid)}&device_id=${encodeURIComponent(did)}&wing=${wing}&setup_version=${version}`, targetValid);
  return <Section id="setup-targets" number="04" title="Daily generation targets"><div data-testid="target-wing-tabs" className="mb-5 flex flex-wrap gap-2" role="tablist">{["A", "B", "C", "D"].map(w => <button key={w} data-testid={`target-wing-${w}`} role="tab" type="button" aria-selected={w === wing} disabled={disabled || dirty} onClick={() => setWing(w)} className={`${btn} ${w === wing ? tone.cyan : tone.gray}`}>Wing {w}</button>)}</div>
    {read.error && <Alert id="targets-load-error">{read.error} <button data-testid="targets-retry" type="button" onClick={read.refresh} className="underline">Retry</button></Alert>}
    {read.loading && <p data-testid="targets-loading" className="text-xs text-gray-500">Loading target reference…</p>}
    {read.data?.wing === wing && <Editor key={wing} data={read.data} wing={wing} save={save} disabled={disabled || read.loading || !!read.error} mark={mark} refresh={read.refresh} operationsHref={operationsHref} />}
  </Section>;
}