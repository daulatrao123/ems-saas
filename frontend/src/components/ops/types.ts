// physical_toggle = CONTACTOR FEEDBACK (Pi telemetry, historical name); toggle_input = PHYSICAL TOGGLE input (Pi telemetry);
// disabled = LOGICAL slot enable (admin configuration). Three separate concepts — never derive one from another.
export type Slot = { display_name: string; target_days: number; used_days: number; physical_toggle: string; toggle_input?: string; disabled: boolean; visible?: boolean };
export type Telemetry = { cpu_temp: number | null; uptime_seconds: number | null; boot_count: number | null };
export type Device = {
  id: string; name: string; connected: boolean; active_slot: string | null; slots: Record<string, Slot>;
  config_state?: string | null; config_error?: string | null; ota_state?: string | null; storage_state?: string | null;
  firmware_version?: string | null; last_sync?: string | null; telemetry?: Telemetry; hardware_fault?: string | null;
};
export type SocietyMeta = { name: string; location: string | null; plan: string | null; society_code: string | null; status: string | null };
export type Dashboard = { society_id: number; society: SocietyMeta; reset_day: number; devices: Device[] };
export type CommandRow = {
  id: string; command: string; slot: string; params: Record<string, unknown>; sequence_no: number; status: string;
  result: string | null; error: string | null; attempt_count: number; created_at: string | null; delivered_at: string | null;
  executing_at: string | null; hardware_verified_at: string | null; completed_at: string | null; acked_at: string | null; expires_at: string | null;
};
export type EventRow = { id: number; ts: string; level: string; msg: string };
export type LastResponse =
  | { kind: "queued"; command: string; slot: string; command_id: string; sequence_no: number; duplicate: boolean; at: string }
  | { kind: "error"; command: string; slot: string; http: number | null; detail: string; at: string };

export const SLOT_CODES = ["A", "B", "C", "D"];
export const COMMAND_LABEL: Record<string, string> = {
  set_active_slot: "ACTIVATE", off_slot: "DEACTIVATE", off_all: "OFF ALL", set_days: "SET DAYS", set_reset_day: "SET RESET DAY",
  reset_days: "RESET DAYS", restart: "RESTART", reboot: "REBOOT PI", lcd_display: "LCD DISPLAY",
};
export const TERMINAL = new Set(["completed", "acked", "failed", "expired"]);

export const fmtTime = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleTimeString([], { hour12: false }) : "—");
export const fmtDateTime = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString([], { hour12: false }) : "—");
export const ago = (iso: string | null | undefined) => {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.floor(s / 60)}m ago` : s < 86400 ? `${Math.floor(s / 3600)}h ago` : `${Math.floor(s / 86400)}d ago`;
};
export const fmtUptime = (sec: number | null | undefined) => {
  if (sec == null || sec <= 0) return null;
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
};
// Next calendar date whose day-of-month equals reset_day (derived from the configured day, not telemetry).
export const nextResetDate = (day: number) => {
  const now = new Date(); let d = new Date(now.getFullYear(), now.getMonth(), day);
  if (d <= now) d = new Date(now.getFullYear(), now.getMonth() + 1, day);
  return d.toLocaleDateString([], { day: "2-digit", month: "short", year: "numeric" });
};
export const statusTone = (status: string) =>
  status === "completed" || status === "acked" ? "text-emerald-400" : status === "failed" || status === "expired" ? "text-red-400"
  : status === "executing" || status === "delivered" || status === "hardware_verified" ? "text-amber-300" : "text-gray-300";
export const errorText = (err: unknown): { http: number | null; detail: string } => {
  const e = err as { code?: string; response?: { status?: number; data?: { detail?: string; error?: string } } };
  const http = e?.response?.status ?? null;
  const detail = e?.response?.data?.detail || e?.response?.data?.error
    || (http === 401 ? "Session expired — sign in again" : http === 403 ? "Forbidden for this tenant/role" : http === 404 ? "Not found" : http === 409 ? "Conflict" : http && http >= 500 ? "Backend error" : e?.code === "ECONNABORTED" ? "Request timed out" : "Network error");
  return { http, detail };
};
