"use client";
import { useEffect, useState } from "react";
import api from "@/lib/api";
import { btn, tone } from "@/components/ops/DashboardHeader";

type Flag = { installed: boolean | null; enabled?: boolean };
type Profile = {
  capability_version: number;
  contactor_feedback: { installed: boolean; channels: Record<string, { enabled: boolean }> };
  generation_meter: Flag;
  consumption_meters: Record<string, Flag>;
  lcd: Flag;
};
type Runtime = { expected?: string; runtime?: string };

const WINGS = ["A", "B", "C", "D"] as const;

export function expectationLabel(installed: boolean | null, enabled?: boolean) {
  if (installed == null) return "NOT CONFIGURED";
  if (!installed) return "NOT INSTALLED";
  if (enabled === false) return "INSTALLED / DISABLED";
  return "INSTALLED";
}

export function runtimeLabel(runtime?: string) {
  if (!runtime || runtime === "NOT_APPLICABLE") return "NOT AVAILABLE";
  return runtime;
}

function Toggle({ testId, on, onChange }: { testId: string; on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button type="button" data-testid={testId} aria-pressed={on} onClick={() => onChange(!on)} className={`${btn} ${on ? tone.cyan : tone.gray} py-0.5`}>
      {on ? "INSTALLED" : "NOT INSTALLED"}
    </button>
  );
}

export function HardwareCapabilities({ deviceId, ask }: { deviceId: string; ask: (c: { title: string; body: string; action: string; consequence?: string; severity?: "INFO" | "WARNING" | "DANGER" | "CRITICAL"; danger?: boolean; failure?: string; typed?: string; onConfirm: () => void | Promise<void | boolean> }) => void }) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [reported, setReported] = useState<Record<string, Runtime | undefined> | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    const res = await api.get(`/api/super-admin/devices/${deviceId}/hardware-capabilities`);
    setProfile(res.data.capabilities);
    setReported(res.data.reported || null);
  };
  useEffect(() => { load().catch((e) => setError(e?.response?.data?.detail || "Hardware configuration unavailable")); }, [deviceId]);

  if (!profile) return <p data-testid={`hw-cap-status-${deviceId}`} className="mt-3 text-xs text-gray-500">{error || "Loading hardware configuration…"}</p>;

  const save = async (confirm: boolean) => {
    setBusy(true); setError("");
    try {
      const res = await api.put(`/api/super-admin/devices/${deviceId}/hardware-capabilities`, { confirm, capabilities: profile });
      setProfile(res.data.capabilities);
      setError(res.data.changed ? "" : "Already applied");
      return true;
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(typeof detail === "string" ? detail : "Save failed");
      return false;
    } finally { setBusy(false); }
  };
  const requestSave = () => ask({
    title: "Confirm contactor feedback configuration",
    body: "You are changing the declared physical hardware for this device, including contactor feedback, meters, and the LCD.",
    consequence: "The EMS will use this capability information when determining whether physical feedback verification is available. Incorrect configuration can affect safety validation.",
    action: "Confirm Hardware Configuration",
    severity: "DANGER",
    failure: "Hardware configuration was not saved.",
    onConfirm: () => save(true),
  });
  const feedbackRuntime = (reported?.contactor_feedback as Runtime | undefined)?.runtime;

  return (
    <section data-testid={`hw-cap-${deviceId}`} className="mt-4 border-t border-[#1e2a3a] pt-3">
      <h4 className="text-sm font-semibold text-white">Hardware Configuration</h4>
      <p className="text-[11px] text-gray-500 mb-2">GPIO pin numbers stay in firmware. Expected hardware is not the same as runtime health.</p>
      <div className="grid gap-2 text-[11px]">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span>Contactor Feedback <span data-testid={`hw-cap-feedback-runtime-${deviceId}`} className="text-gray-400">· {runtimeLabel(feedbackRuntime)}</span></span>
          <Toggle testId={`hw-cap-feedback-${deviceId}`} on={profile.contactor_feedback.installed} onChange={(installed) => setProfile({ ...profile, contactor_feedback: { installed, channels: Object.fromEntries(WINGS.map((w) => [w, { enabled: installed && profile.contactor_feedback.channels[w]?.enabled !== false }])) } })} />
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span>Generation Meter <span className="text-gray-400">· {expectationLabel(profile.generation_meter.installed, profile.generation_meter.enabled)}</span></span>
          <Toggle testId={`hw-cap-gen-${deviceId}`} on={profile.generation_meter.installed === true} onChange={(installed) => setProfile({ ...profile, generation_meter: { installed, enabled: installed ? profile.generation_meter.enabled === true : false } })} />
        </div>
        {WINGS.map((wing) => (
          <div key={wing} className="flex flex-wrap items-center justify-between gap-2">
            <span>Consumption Meter {wing}</span>
            <Toggle testId={`hw-cap-meter-${wing}-${deviceId}`} on={profile.consumption_meters[wing]?.installed === true} onChange={(installed) => setProfile({ ...profile, consumption_meters: { ...profile.consumption_meters, [wing]: { installed, enabled: installed ? profile.consumption_meters[wing]?.enabled === true : false } } })} />
          </div>
        ))}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span>LCD</span>
          <Toggle testId={`hw-cap-lcd-${deviceId}`} on={profile.lcd.installed === true} onChange={(installed) => setProfile({ ...profile, lcd: { installed, enabled: installed } })} />
        </div>
      </div>
      <button type="button" data-testid={`hw-cap-save-${deviceId}`} disabled={busy} onClick={requestSave} className={`${btn} ${tone.cyan} mt-3 py-1`}>{busy ? "SAVING…" : "SAVE HARDWARE CONFIGURATION"}</button>
      {error && <p data-testid={`hw-cap-error-${deviceId}`} className="mt-1 text-xs text-amber-300">{error}</p>}
    </section>
  );
}
