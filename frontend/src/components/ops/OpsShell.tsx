"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import { getSession, Session } from "@/lib/auth";

type Role = "super_admin" | "society_admin" | "member";

// Role gate + sidebar shell shared by the operational pages. Tenant comes from the session unless super_admin.
export function useRoleSession(allowed: Role[]) {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    getSession().then((s) => {
      if (!s || !allowed.includes(s.role as Role)) { router.push("/login"); return; }
      setSession(s); setReady(true);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router]);
  return { session, ready };
}

export function OpsShell({ session, children }: { session: Session; children: React.ReactNode }) {
  return (
    <div className="flex h-screen overflow-hidden bg-[#0a0e17]">
      <Sidebar role={session.role} name={session.name || ""} />
      <main className="flex-1 overflow-y-auto p-4 pt-20 lg:p-6 lg:pt-20">{children}</main>
    </div>
  );
}
