"use client";
import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

export default function Sidebar({ role }: { role: string }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  useEffect(() => { setOpen(false); }, [pathname]);
  useEffect(() => { document.body.style.overflow = open ? "hidden" : ""; return () => { document.body.style.overflow = ""; }; }, [open]);

  const name = typeof window !== "undefined" ? localStorage.getItem("name") || "" : "";
  const superAdminLinks = [{ href: "/super-admin", label: "All Societies", icon: "\uD83C\uDFE2" }];
  const adminLinks = [{ href: "/admin", label: "Dashboard", icon: "\uD83D\uDCCA" }];
  const links = role === "super_admin" ? superAdminLinks : adminLinks;
  const close = () => setOpen(false);
  const handleLogout = () => { localStorage.clear(); window.location.href = "/login"; };

  return (
    <>
      <div className="fixed top-0 left-0 right-0 z-40 h-14 bg-gray-950 border-b border-gray-800 flex items-center px-4 gap-3">
        <button onClick={() => setOpen(true)} className="p-2 -ml-2 text-gray-400 hover:text-white active:scale-90 transition-transform">
          <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" /></svg>
        </button>
        <h1 className="text-lg font-bold text-cyan-400">EMS Cloud</h1>
        <div className="ml-auto flex items-center gap-3">
          <span className="text-xs text-gray-500">{name}</span>
          <span className="text-[9px] px-2 py-0.5 rounded-full bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 font-semibold uppercase">{role === "super_admin" ? "Super Admin" : role === "society_admin" ? "Admin" : "Member"}</span>
        </div>
      </div>
      {open && <div className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm" onClick={close} />}
      <div className={"fixed top-0 left-0 z-50 h-full w-64 bg-gray-950 border-r border-gray-800 transform transition-transform duration-300 " + (open ? "translate-x-0" : "-translate-x-full")}>
        <div className="h-14 border-b border-gray-800 flex items-center px-4 gap-3">
          <span className="text-xl">\u26A1</span>
          <h1 className="text-lg font-bold text-cyan-400">EMS Cloud</h1>
        </div>
        <div className="p-4">
          <div className="text-xs text-gray-500 uppercase tracking-wider mb-2">{role === "super_admin" ? "Super Admin" : "Society Admin"}</div>
          {name && <div className="text-sm text-gray-300 mb-4">{name}</div>}
          <nav className="space-y-1">
            {links.map((link) => (
              <Link key={link.href} href={link.href} onClick={close} className={"flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-all " + (pathname === link.href ? "bg-cyan-500/10 text-cyan-400 border border-cyan-500/20" : "text-gray-400 hover:text-white hover:bg-gray-800/50")}>
                <span>{link.icon}</span><span>{link.label}</span>
              </Link>
            ))}
          </nav>
        </div>
        <div className="absolute bottom-0 left-0 right-0 p-4 border-t border-gray-800">
          <button onClick={handleLogout} className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-gray-400 hover:text-red-400 hover:bg-red-500/5 transition-all">
            <span>\uD83D\uDEAA</span><span>Logout</span>
          </button>
        </div>
      </div>
    </>
  );
}
