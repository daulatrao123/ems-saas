"use client";
import { OperationalDashboard } from "@/components/ops/OperationalDashboard";
import { OpsShell, useRoleSession } from "@/components/ops/OpsShell";

// Members get the same operational view, read-only (no control panels rendered; backend rejects writes anyway).
export default function MemberDashboard() {
  const { session, ready } = useRoleSession(["member"]);
  if (!ready || !session) return <div className="flex h-screen items-center justify-center text-gray-500">Loading Dashboard...</div>;
  const sid = session.society_id != null ? String(session.society_id) : null;
  return (
    <OpsShell session={session}>
      <OperationalDashboard societyId={sid} readOnly backHref="/member" />
    </OpsShell>
  );
}
