"use client";
import { useParams } from "next/navigation";
import { OperationalDashboard } from "@/components/ops/OperationalDashboard";
import { OpsShell, useRoleSession } from "@/components/ops/OpsShell";
import { ProvisioningCenter } from "@/components/provisioning/ProvisioningCenter";

// Super admins open any society by id; everyone else is pinned to their own tenant (backend re-checks).
export default function SocietyOperations() {
  const params = useParams<{ id: string }>();
  const { session, ready } = useRoleSession(["super_admin", "society_admin", "member"]);
  if (!ready || !session) return <div className="flex h-screen items-center justify-center text-gray-500">Loading Operations...</div>;
  const sid = session.role === "super_admin" ? String(params.id) : session.society_id != null ? String(session.society_id) : null;
  const back = session.role === "super_admin" ? "/super-admin" : session.role === "member" ? "/member" : "/admin";
  return (
    <OpsShell session={session}>
      <OperationalDashboard key={sid || "none"} societyId={sid} readOnly={session.role === "member"} backHref={back} />
      {session.role === "super_admin" && sid && <div className="mt-3"><ProvisioningCenter societyId={sid} /></div>}
    </OpsShell>
  );
}
