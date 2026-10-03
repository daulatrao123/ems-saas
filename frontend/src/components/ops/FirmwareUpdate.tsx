"use client";
import { useEffect, useState } from "react";
import api from "@/lib/api";
import { btn, panel, tone } from "./DashboardHeader";
import type { Confirm } from "./ConfirmDialog";

type Offer = {
  current_firmware?: string | null;
  available_firmware?: string | null;
  status?: string;
  release_notes?: string;
  install_allowed?: boolean;
  bootstrap_required?: boolean;
  bootstrap_message?: string;
  device_name?: string;
  reason?: string;
};

const LABEL: Record<string, string> = {
  NONE: "NONE",
  UPDATE_AVAILABLE: "UPDATE AVAILABLE",
  REQUESTED: "REQUESTED",
  DOWNLOADING: "DOWNLOADING",
  VERIFYING: "VERIFYING",
  STAGED: "STAGED",
  INSTALLING: "INSTALLING",
  RESTARTING: "RESTARTING",
  HEALTH_CHECK: "HEALTH CHECK",
  SUCCESS: "SUCCESS",
  FAILED: "FAILED",
  ROLLING_BACK: "ROLLING BACK",
  ROLLED_BACK: "ROLLED BACK",
};

export function FirmwareUpdate({ deviceId, deviceName, ask }: { deviceId: string; deviceName: string; ask: (c: Confirm) => void }) {
  const [offer, setOffer] = useState<Offer | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => api.get(`/api/admin/devices/${deviceId}/firmware`).then((res) => setOffer(res.data)).catch(() => setError("Firmware status unavailable"));
  useEffect(() => { void load(); }, [deviceId]);

  const install = async () => {
    setBusy(true);
    setError("");
    try {
      await api.post(`/api/admin/devices/${deviceId}/firmware/install`, {});
      await load();
      return true;
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(typeof detail === "string" ? detail : "Firmware update request failed. No update was started.");
      return false;
    } finally { setBusy(false); }
  };

  const current = offer?.current_firmware || "UNAVAILABLE";
  const available = offer?.available_firmware;
  const status = LABEL[offer?.status || ""] || offer?.status || "UNAVAILABLE";
  return <section data-testid="firmware-update" className={`${panel} mt-3 p-4 space-y-2`}>
    <div className="text-sm font-semibold text-white">Firmware</div>
    <p data-testid="firmware-current">Current firmware: {current}</p>
    <p data-testid="firmware-available">Available: {available || "None"}</p>
    <p data-testid="firmware-status">Status: {status}</p>
    {offer?.release_notes && <p data-testid="firmware-notes" className="whitespace-pre-line text-xs text-gray-300">{offer.release_notes}</p>}
    {offer?.bootstrap_required && <p data-testid="firmware-bootstrap" className="text-xs text-amber-200">{offer.bootstrap_message}</p>}
    {error && <p data-testid="firmware-error" role="alert" className="text-xs text-red-300">{error}</p>}
    {offer?.install_allowed && <button type="button" data-testid="firmware-install" disabled={busy} className={`${btn} ${tone.red}`}
      onClick={() => ask({
        title: "Install firmware update?",
        body: `Device: ${offer.device_name || deviceName}\n\nCurrent firmware: ${current}\nNew firmware: ${available}`,
        consequence: "The Raspberry Pi will download and verify the firmware, restart the EMS controller, and perform a health check. If the update fails, the previous firmware will be restored.",
        action: "Install Firmware",
        severity: "DANGER",
        failure: "Firmware update request failed. No update was started.",
        onConfirm: install,
      })}>{busy ? "Working…" : "Install Firmware"}</button>}
  </section>;
}
