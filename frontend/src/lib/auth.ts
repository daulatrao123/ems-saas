import api from "./api";

export type Session = {
  id: number;
  role: "super_admin" | "society_admin" | "member";
  name: string;
  society_id: number | null;
};

export async function getSession(): Promise<Session> {
  const res = await api.get("/api/auth/me");
  return res.data as Session;
}

export async function logout(): Promise<void> {
  try { await api.post("/api/auth/logout"); } finally { window.location.href = "/login"; }
}
