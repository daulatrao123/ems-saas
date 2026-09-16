"use client";
import { useParams } from "next/navigation";
import { EnergySetup } from "@/components/commissioning/EnergySetup";
import { OpsShell, useRoleSession } from "@/components/ops/OpsShell";

export default function EnergySetupPage() {
  const params = useParams<{ id: string; deviceId: string }>();
  const { session, ready } = useRoleSession(["super_admin", "society_admin"]);
  if (!ready || !session) return <p data-testid="setup-session-loading" className="p-8 text-gray-400">Loading setup…</p>;
  const sid = session.role === "super_admin" ? params.id : session.society_id != null ? String(session.society_id) : null;
  return <OpsShell session={session}>{sid ? <EnergySetup key={sid} societyId={sid} initialDeviceId={params.deviceId} /> : <p data-testid="setup-no-society">No society assigned.</p>}</OpsShell>;
}