import api from "./api";

export type Role = "super_admin" | "society_admin" | "member";

export type Session = {
  id: number;
  email: string;
  role: Role;
  name: string;
  society_id: number | null;
};

// The HttpOnly access cookie is opaque to the browser; /api/auth/me is the session source of truth.
export async function getSession(): Promise<Session | null> {
  try {
    const res = await api.get("/api/auth/me");
    return res.data as Session;
  } catch {
    return null;
  }
}

export function homeFor(role: Role): string {
  if (role === "super_admin") return "/super-admin";
  if (role === "society_admin") return "/admin";
  return "/member";
}

export async function logout(): Promise<void> {
  try { await api.post("/api/auth/logout"); } finally { window.location.href = "/login"; }
}
