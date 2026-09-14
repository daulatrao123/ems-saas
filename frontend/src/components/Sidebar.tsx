"use client";
import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { logout } from "@/lib/auth";

export default function Sidebar({ role, name = "" }: { role: string; name?: string }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [seenPath, setSeenPath] = useState(pathname);
  if (pathname !== seenPath) { setSeenPath(pathname); setOpen(false); }  // close drawer on navigation
  useEffect(() => { document.body.style.overflow = open ? "hidden" : ""; return () => { document.body.style.overflow = ""; }; }, [open]);

  const superAdminLinks = [{ href: "/super-admin", label: "All Societies", icon: "\uD83C\uDFE2" }];
  const adminLinks = [{ href: "/admin", label: "Dashboard", icon: "\uD83D\uDCCA" }];
  const memberLinks = [{ href: "/member", label: "Status", icon: "\uD83D\uDCCA" }];
  const links = role === "super_admin" ? superAdminLinks : role === "member" ? memberLinks : adminLinks;
  const close = () => setOpen(false);
  const handleLogout = () => { void logout(); };
  const roleLabel = role === "super_admin" ? "Super Admin" : role === "society_admin" ? "Admin" : "Member";
  const normalizedName = name.replace(/[\s_]+/g, "").toLowerCase();
  const showName = name && ![roleLabel, role].some((value) => value.replace(/[\s_]+/g, "").toLowerCase() === normalizedName);

  return (
    <>
      <div className="fixed top-0 left-0 right-0 z-40 h-14 bg-gray-950 border-b border-gray-800 flex items-center px-4 gap-3">
        <button data-testid="navigation-open" aria-label="Open navigation" aria-expanded={open} onClick={() => setOpen(true)} className="p-2 -ml-2 text-gray-400 hover:text-white active:scale-90 transition-transform">
          <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" /></svg>
        </button>
        <div data-testid="app-brand" className="text-lg font-bold text-cyan-400 whitespace-nowrap">EMS Cloud</div>
        <div className="ml-auto flex min-w-0 items-center gap-3">
          {showName && <span data-testid="session-name" className="hidden sm:block truncate text-xs text-gray-500">{name}</span>}
          <span data-testid="session-role" className="shrink-0 text-[9px] px-2 py-0.5 rounded-full bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 font-semibold uppercase">{roleLabel}</span>
        </div>
      </div>
      {open && <><button aria-label="Close navigation overlay" data-testid="navigation-overlay" className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm" onClick={close} />
      <div data-testid="navigation-drawer" className="fixed top-0 left-0 z-50 h-full w-64 max-w-full bg-gray-950 border-r border-gray-800">
        <div className="h-14 border-b border-gray-800 flex items-center px-4 gap-3">
          <h2 data-testid="navigation-heading" className="text-sm font-bold text-gray-300">Navigation</h2>
          <button data-testid="navigation-close" aria-label="Close navigation" onClick={close} className="ml-auto p-2 text-gray-400 hover:text-white">×</button>
        </div>
        <div className="p-4">
          <nav className="space-y-1">
            {links.map((link) => (
              <Link key={link.href} data-testid={`navigation-link-${link.href.slice(1)}`} href={link.href} onClick={close} className={"flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors " + (pathname === link.href ? "bg-cyan-500/10 text-cyan-400 border border-cyan-500/20" : "text-gray-400 hover:text-white hover:bg-gray-800/50")}>
                <span>{link.icon}</span><span>{link.label}</span>
              </Link>
            ))}
          </nav>
        </div>
        <div className="absolute bottom-0 left-0 right-0 p-4 border-t border-gray-800">
          <button data-testid="navigation-logout" onClick={handleLogout} className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-gray-400 hover:text-red-400 hover:bg-red-500/5 transition-colors">
            <span>\uD83D\uDEAA</span><span>Logout</span>
          </button>
        </div>
      </div></>}
    </>
  );
}
