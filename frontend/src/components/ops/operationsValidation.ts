import { Dashboard, CommandRow, EventRow } from "./types";
import { record, finite, text } from "./readRequest";

export function dashboardValid(data: unknown, sid: string): data is Dashboard {
  return record(data) && String(data.society_id) === sid && record(data.society) && text(data.society.name)
    && finite(data.reset_day) && Array.isArray(data.devices) && data.devices.every((d) => record(d)
      && text(d.id) && text(d.name) && typeof d.connected === "boolean" && record(d.slots)
      && Object.values(d.slots).every((s) => record(s) && text(s.display_name) && typeof s.disabled === "boolean"
        && finite(s.used_days) && finite(s.target_days) && text(s.physical_toggle)));
}
export const commandsValid = (rows: unknown): rows is CommandRow[] => Array.isArray(rows) && rows.every((r) => record(r)
  && text(r.id) && text(r.command) && text(r.slot) && text(r.status) && finite(r.sequence_no)
  && (r.result === null || text(r.result)) && (r.error === null || text(r.error)) && record(r.params));
export const eventsValid = (rows: unknown): rows is EventRow[] => Array.isArray(rows) && rows.every((r) => record(r)
  && finite(r.id) && text(r.ts) && text(r.level) && text(r.msg));