"use client";
import { Setup, Save } from "./types";
import { useDraft } from "./hooks";
import { Actions, Alert, Section } from "./controls";

export function ModeForm({ setup, save, disabled, mark }: { setup: Setup; save: Save; disabled: boolean; mark: (n: string, d: boolean) => void }) {
  const d = useDraft(setup.mode.mode, setup.mode.version, "mode", mark);
  const enabled = ["M2", "M3", "M4", "M5"].filter(id => setup.meters[id].enabled);
  return <Section id="setup-mode" number="03" title="Consumption source"><form data-testid="setup-mode-form" onSubmit={async e => { e.preventDefault(); if (disabled) return; if (await save("put", "calculation-mode", { mode: d.draft, expected_version: d.base }, "Calculation mode")) d.saved(); }}>
    <fieldset disabled={disabled} className="space-y-3"><legend className="sr-only">Consumption calculation mode</legend>{(["MANUAL", "AUTO"] as const).map(mode => <label key={mode} className={`flex items-start gap-3 border-l-2 px-4 py-4 ${d.draft === mode ? "border-cyan-400 bg-cyan-500/5" : "border-gray-700"}`}>
      <input data-testid={`mode-${mode.toLowerCase()}`} name="calculation-mode" type="radio" value={mode} checked={d.draft === mode} onChange={() => d.change(mode)} className="mt-1" />
      <span className="text-sm text-gray-200"><strong>{mode}</strong><span className="mt-1 block text-xs text-gray-400">{mode === "MANUAL" ? "Monthly bill-derived consumption reference" : "Physical consumption meter readings"}</span></span></label>)}</fieldset>
    <p data-testid="mode-consumption-meters" className="mt-4 text-xs text-gray-400">Enabled consumption meters: {enabled.join(", ") || "None"}</p>
    {!enabled.length && <Alert id="mode-m1-only">M1-only installation: MANUAL consumption is recommended. AUTO does not detect or configure meters.</Alert>}
    <Actions id="mode" dirty={d.dirty} disabled={disabled} reset={d.reset} label="Save consumption mode" />
  </form></Section>;
}