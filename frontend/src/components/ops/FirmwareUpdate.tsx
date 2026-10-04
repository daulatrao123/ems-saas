"use client";
import { useEffect, useState } from "react";
import api from "@/lib/api";
import { btn, panel, tone } from "./DashboardHeader";

type HistoryRow = {
  firmware_version?: string;
  previous_version?: string | null;
  state?: string;
  created_by?: string | null;
  scheduled_for?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  error?: string | null;
};

type Schedule = {
  firmware_version?: string;
  state?: string;
  scheduled_for?: string | null;
  error?: string | null;
  created_by?: string | null;
};

type Offer = {
  current_firmware?: string | null;
  available_firmware?: string | null;
  latest_firmware?: string | null;
  status?: string;
  release_notes?: string;
  release_date?: string | null;
  install_allowed?: boolean;
  notification?: boolean;
  latest_installed?: boolean;
  bootstrap_required?: boolean;
  bootstrap_message?: string;
  device_name?: string;
  reason?: string;
  timezone?: string;
  maintenance_window?: string;
  schedule?: Schedule | null;
  history?: HistoryRow[];
};

const LABEL: Record<string, string> = {
  NONE: "No update",
  LATEST_INSTALLED: "Latest firmware installed",
  UPDATE_AVAILABLE: "Update available",
  SCHEDULED: "Scheduled",
  WAITING: "Waiting for maintenance window",
  DEFERRED: "Deferred",
  REQUESTED: "Scheduled",
  DOWNLOADING: "Downloading",
  VERIFYING: "Verifying",
  STAGED: "Staged",
  INSTALLING: "Updating",
  RESTARTING: "Updating",
  HEALTH_CHECK: "Health check",
  SUCCESS: "Completed",
  FAILED: "Failed",
  ROLLING_BACK: "Rolling back",
  ROLLED_BACK: "Rolled back",
  CANCELLED: "Cancelled",
};

const PENDING = new Set(["SCHEDULED", "WAITING", "DEFERRED", "REQUESTED"]);

function stamp(value?: string | null) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(undefined, { hour12: true });
}

export function FirmwareUpdate({ deviceId, deviceName }: { deviceId: string; deviceName: string; ask?: unknown }) {
  const [offer, setOffer] = useState<Offer | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [details, setDetails] = useState(false);
  const [scheduling, setScheduling] = useState(false);
  const [date, setDate] = useState("");
  const [time, setTime] = useState("02:00");

  const load = () => api.get(`/api/admin/devices/${deviceId}/firmware`).then((res) => setOffer(res.data)).catch(() => setError("Firmware status unavailable"));
  useEffect(() => { void load(); }, [deviceId]);

  const current = offer?.current_firmware || "Unavailable";
  const latest = offer?.latest_firmware || offer?.available_firmware || "None";
  const status = LABEL[offer?.status || ""] || offer?.status || "Unavailable";
  const scheduleState = offer?.schedule?.state || "";
  const pending = PENDING.has(scheduleState);
  const notes = offer?.release_notes || "";

  const schedule = async () => {
    setBusy(true);
    setError("");
    try {
      await api.post(`/api/admin/devices/${deviceId}/firmware/schedule`, { date, time });
      setScheduling(false);
      await load();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail === "OUTSIDE_MAINTENANCE_WINDOW" ? "Choose a time between 02:00 AM and 04:00 AM." : (typeof detail === "string" ? detail : "The update was not scheduled."));
    } finally { setBusy(false); }
  };

  const cancel = async () => {
    setBusy(true);
    setError("");
    try {
      await api.post(`/api/admin/devices/${deviceId}/firmware/schedule/cancel`, {});
      await load();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(typeof detail === "string" ? detail : "The schedule was not cancelled.");
    } finally { setBusy(false); }
  };

  return <section data-testid="firmware-update" className={`${panel} mt-3 p-4 space-y-3`}>
    <div className="text-sm font-semibold text-white">Firmware</div>
    <p data-testid="firmware-current">Current firmware: {current}</p>
    <p data-testid="firmware-available">Latest available firmware: {latest}</p>
    <p data-testid="firmware-status">Status: {offer?.latest_installed ? "Latest firmware installed" : status}</p>
    {offer?.latest_installed && <p data-testid="firmware-latest-installed" className="text-xs text-emerald-300">Latest firmware installed</p>}
    {offer?.notification && <div data-testid="firmware-notification" className="border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100 space-y-2">
      <div className="font-semibold">Firmware update available</div>
      <p>A new EMS Controller firmware version is available.</p>
      <p>Current version: {current}<br />Latest version: {latest}</p>
      <p>This update can only be installed during the scheduled maintenance window: 02:00 AM – 04:00 AM ({offer.timezone || "society timezone"}).</p>
      <div className="flex flex-wrap gap-2">
        <button type="button" data-testid="firmware-view" className={`${btn} ${tone.gray}`} onClick={() => setDetails(true)}>View update</button>
        <button type="button" data-testid="firmware-schedule-open" className={`${btn} ${tone.amber}`} onClick={() => setScheduling(true)}>Schedule update</button>
      </div>
    </div>}
    {pending && <div data-testid="firmware-scheduled" className="border border-cyan-500/30 p-3 text-sm text-gray-200 space-y-1">
      <div className="font-semibold text-white">{scheduleState === "DEFERRED" ? "Firmware update deferred" : "Firmware update scheduled"}</div>
      <p>Version: {offer?.schedule?.firmware_version || latest}</p>
      <p>Scheduled: {stamp(offer?.schedule?.scheduled_for)}</p>
      <p>Window: 02:00 AM – 04:00 AM</p>
      <p data-testid="firmware-schedule-status">Status: {LABEL[scheduleState] || scheduleState}</p>
      {scheduleState === "DEFERRED" && <p data-testid="firmware-deferred-reason">{offer?.schedule?.error || "The controller did not perform the update during the maintenance window. It stays pending for the next 02:00 AM – 04:00 AM window. No daytime update will be attempted."}</p>}
      {PENDING.has(scheduleState) && <button type="button" data-testid="firmware-cancel" disabled={busy} className={`${btn} ${tone.gray}`} onClick={() => void cancel()}>Cancel schedule</button>}
    </div>}
    {details && <div data-testid="firmware-details" className="border border-gray-700 p-3 text-sm text-gray-200 space-y-2">
      <div className="font-semibold text-white">Firmware update</div>
      <p>Version {latest}</p>
      <p>Current version {current}</p>
      <p>Release date {stamp(offer?.release_date)}</p>
      <p>Update status {offer?.latest_installed ? "Latest firmware installed" : status}</p>
      <div>
        <div className="text-xs uppercase text-gray-500">What will change?</div>
        <p data-testid="firmware-notes" className="whitespace-pre-line text-xs text-gray-300">{notes || "No release notes were published for this version."}</p>
      </div>
      <p className="text-xs text-gray-400">The firmware update is performed only during the 02:00 AM – 04:00 AM maintenance window. The existing controller configuration and persistent EMS state are preserved according to the existing OTA architecture.</p>
      <ol className="list-decimal pl-4 text-xs text-gray-400 space-y-1">
        <li>Firmware package is downloaded.</li>
        <li>Package integrity is verified.</li>
        <li>Firmware is staged.</li>
        <li>Controller performs the update during the maintenance window.</li>
        <li>Controller starts the new firmware.</li>
        <li>Health checks are performed.</li>
        <li>Update is confirmed only after successful health checks.</li>
        <li>If the existing OTA mechanism determines the update unhealthy, the existing rollback mechanism is used.</li>
      </ol>
      <div className="flex gap-2">
        {!offer?.latest_installed && !pending && <button type="button" className={`${btn} ${tone.amber}`} onClick={() => setScheduling(true)}>Schedule update</button>}
        <button type="button" className={`${btn} ${tone.gray}`} onClick={() => setDetails(false)}>Close</button>
      </div>
    </div>}
    {scheduling && <form data-testid="firmware-schedule-form" className="border border-gray-700 p-3 space-y-2" onSubmit={(event) => { event.preventDefault(); void schedule(); }}>
      <div className="font-semibold text-white">Schedule firmware update</div>
      <p>Latest firmware: {latest}</p>
      <p>Allowed maintenance window: 02:00 AM – 04:00 AM ({offer?.timezone || "society timezone"})</p>
      <label className="block text-xs text-gray-400">Preferred update date
        <input data-testid="firmware-date" required type="date" value={date} onChange={(event) => setDate(event.target.value)} className="mt-1 block bg-[#0a0e17] border border-[#2a3646] px-2 py-1 text-sm text-gray-100" />
      </label>
      <label className="block text-xs text-gray-400">Preferred time
        <input data-testid="firmware-time" required type="time" min="02:00" max="03:59" step={60} value={time} onChange={(event) => setTime(event.target.value)} className="mt-1 block bg-[#0a0e17] border border-[#2a3646] px-2 py-1 text-sm text-gray-100" />
      </label>
      <div className="flex gap-2">
        <button type="submit" data-testid="firmware-schedule-submit" disabled={busy} className={`${btn} ${tone.amber}`}>Schedule update</button>
        <button type="button" className={`${btn} ${tone.gray}`} onClick={() => setScheduling(false)}>Cancel</button>
      </div>
    </form>}
    {offer?.bootstrap_required && <p data-testid="firmware-bootstrap" className="text-xs text-amber-200">{offer.bootstrap_message}</p>}
    {error && <p data-testid="firmware-error" role="alert" className="text-xs text-red-300">{error}</p>}
    {(offer?.history || []).length > 0 && <div data-testid="firmware-history" className="text-xs text-gray-400 space-y-1">
      <div className="uppercase text-gray-500">Update history</div>
      {offer?.history?.map((row, index) => <p key={`${row.firmware_version}-${index}`} data-testid="firmware-history-row">
        {row.previous_version || "—"} → {row.firmware_version} · {LABEL[row.state || ""] || row.state} · scheduled {stamp(row.scheduled_for)} · started {stamp(row.started_at)} · completed {stamp(row.completed_at)}
        {row.error ? ` · ${row.error}` : ""}
      </p>)}
    </div>}
    <p className="text-[11px] text-gray-500">{deviceName} updates are scheduled for this device only.</p>
  </section>;
}
