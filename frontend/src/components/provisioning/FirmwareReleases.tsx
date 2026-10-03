"use client";
import { useEffect, useState } from "react";
import api from "@/lib/api";
import { Confirm, ConfirmDialog } from "@/components/ops/ConfirmDialog";

type Release = { version: string; hardware_profile: string; status: string; package_sha256: string; notes?: string; key_id?: string };

export function FirmwareReleases() {
  const [rows, setRows] = useState<Release[]>([]);
  const [manifest, setManifest] = useState("");
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const load = () => api.get("/api/super-admin/firmware/releases").then((res) => setRows(res.data.releases || [])).catch(() => setError("Firmware releases unavailable"));
  useEffect(() => { void load(); }, []);
  const create = async () => {
    const parsed = JSON.parse(manifest);
    await api.post("/api/super-admin/firmware/releases", { manifest: parsed });
    setManifest("");
    await load();
  };
  return <section data-testid="firmware-releases" className="rounded-xl border border-gray-800 bg-gray-900/80 p-5 space-y-3">
    <h3 className="text-sm font-bold text-white">Firmware releases</h3>
    <p className="text-[11px] text-gray-500">A release stays DRAFT until it is approved. Society admins can install only an approved release, and only on a device that already runs the multi-file OTA agent.</p>
    <textarea data-testid="firmware-manifest" value={manifest} onChange={(event) => setManifest(event.target.value)} placeholder="Signed release manifest JSON" className="min-h-28 w-full rounded-md border border-gray-700 bg-gray-950 p-2 font-mono text-xs text-gray-100" />
    <button type="button" data-testid="firmware-create" disabled={!manifest.trim()} className="rounded-md border border-amber-500/40 px-3 py-2 text-xs font-bold text-amber-200"
      onClick={() => setConfirm({
        title: "Create firmware release?",
        body: "The manifest will be stored as DRAFT. It cannot be installed until a super admin approves it.",
        consequence: "Creating a draft does not contact any Raspberry Pi.",
        action: "Create Release",
        severity: "WARNING",
        failure: "The release was not created.",
        onConfirm: async () => { try { await create(); return true; } catch (err) { setError((err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || "Release creation failed"); return false; } },
      })}>Create draft release</button>
    {error && <p data-testid="firmware-release-error" role="alert" className="text-xs text-red-300">{error}</p>}
    <ul className="space-y-2">
      {rows.map((row) => <li key={row.version} data-testid={`firmware-release-${row.version}`} className="border border-gray-800 p-3 text-xs text-gray-300">
        <div className="font-semibold text-white">{row.version} · {row.status} · {row.hardware_profile}</div>
        <div className="font-mono break-all">SHA-256 {row.package_sha256}</div>
        {row.notes && <p className="mt-1 whitespace-pre-line">{row.notes}</p>}
        <div className="mt-2 flex gap-2">
          {row.status === "DRAFT" && <button type="button" data-testid={`firmware-approve-${row.version}`} className="rounded border border-red-500/50 px-2 py-1 text-red-200" onClick={() => setConfirm({
            title: "Approve firmware release?",
            body: `Version ${row.version} will become installable on compatible devices that already run the multi-file OTA agent.`,
            consequence: "Approval does not start an update. A separate install confirmation is required for each device.",
            action: "Approve Release",
            severity: "DANGER",
            failure: "The release was not approved.",
            onConfirm: async () => { await api.post(`/api/super-admin/firmware/releases/${encodeURIComponent(row.version)}/approve`, {}); await load(); },
          })}>Approve</button>}
          {row.status === "APPROVED" && <button type="button" data-testid={`firmware-revoke-${row.version}`} className="rounded border border-red-500/50 px-2 py-1 text-red-200" onClick={() => setConfirm({
            title: "Revoke firmware release?",
            body: `Version ${row.version} will no longer be installable.`,
            consequence: "A device that has already started this update keeps its current operation. New install requests are refused.",
            action: "Revoke Release",
            severity: "DANGER",
            failure: "The release was not revoked.",
            onConfirm: async () => { await api.post(`/api/super-admin/firmware/releases/${encodeURIComponent(row.version)}/revoke`, {}); await load(); },
          })}>Revoke</button>}
        </div>
      </li>)}
    </ul>
    <ConfirmDialog c={confirm} onClose={() => setConfirm(null)} />
  </section>;
}
