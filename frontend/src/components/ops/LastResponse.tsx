"use client";
import { COMMAND_LABEL, CommandRow, LastResponse as LR, TERMINAL, fmtTime, statusTone } from "./types";
import { label, panel } from "./DashboardHeader";

function Row({ k, v, tone }: { k: string; v: string; tone?: string }) {
  return <><dt className="text-gray-500">{k}</dt><dd className={`truncate ${tone || "text-gray-100"}`}>{v}</dd></>;
}

// Prefers the backend's stored command row (authoritative status/result/timestamps) over the submit response.
export function LastResponse({ last, row: storedRow }: { last: LR | null; row: CommandRow | null }) {
  // A logical slot-config save is newer than any stored Pi command row -> show the save, not the stale row.
  const configNewer = !!last && last.kind === "queued" && last.command_id === "config" && (!storedRow || !storedRow.created_at || new Date(last.at) > new Date(storedRow.created_at));
  const row = configNewer ? null : storedRow;
  return (
    <section data-testid="last-response" className={`${panel} p-4`}>
      <div className={label}>Last Response</div>
      {!last && !row ? <div className="mt-3 text-sm text-gray-500">No command issued in this session.</div> : (
        <>
          <div className="mt-2 text-base font-bold text-white">
            {COMMAND_LABEL[(row?.command || (last as LR).command)] || (row?.command || (last as LR).command).replace(/_/g, " ").toUpperCase()}
            {(row?.slot || (last && last.slot)) && <span className="text-gray-400 font-normal"> · Slot {row?.slot || last?.slot}</span>}
          </div>
          <dl className="mt-3 grid grid-cols-[110px_1fr] gap-y-1.5 font-mono text-[11px]">
            {row ? (
              <>
                <Row k="SEQUENCE" v={`#${row.sequence_no}`} />
                <Row k="STATUS" v={`${row.status.toUpperCase()}${TERMINAL.has(row.status) ? "" : " …"}`} tone={statusTone(row.status)} />
                <Row k="RESULT" v={row.result || "—"} tone={row.result ? "text-cyan-300" : "text-gray-500"} />
                {row.error && <Row k="ERROR" v={row.error} tone="text-red-400" />}
                <Row k="REQUESTED" v={fmtTime(row.created_at)} />
                <Row k="DELIVERED" v={fmtTime(row.delivered_at)} />
                {row.hardware_verified_at && <Row k="HW VERIFIED" v={fmtTime(row.hardware_verified_at)} tone="text-emerald-300" />}
                <Row k="COMPLETED" v={fmtTime(row.completed_at)} />
                {Object.keys(row.params || {}).length > 0 && <Row k="PARAMS" v={JSON.stringify(row.params)} />}
              </>
            ) : last && last.kind === "error" ? (
              <>
                <Row k="STATUS" v="FAILED" tone="text-red-400" />
                <Row k="HTTP" v={last.http ? String(last.http) : "—"} />
                <Row k="ERROR" v={last.detail} tone="text-red-400" />
                <Row k="SENT" v={fmtTime(last.at)} />
              </>
            ) : last && last.command_id === "config" ? (
              <>
                <Row k="TYPE" v="LOGICAL SLOT CONFIG" />
                <Row k="STATUS" v="SAVED · config_version bumped" tone="text-emerald-400" />
                <Row k="SENT" v={fmtTime(last.at)} />
              </>
            ) : last && (
              <>
                <Row k="SEQUENCE" v={`#${last.sequence_no}`} />
                <Row k="STATUS" v={last.duplicate ? "DUPLICATE (already queued)" : "QUEUED …"} tone="text-amber-300" />
                <Row k="SENT" v={fmtTime(last.at)} />
              </>
            )}
          </dl>
          {last?.kind === "error" && row && <div className="mt-2 font-mono text-[10px] text-red-400">Latest submit failed: {last.detail}</div>}
        </>
      )}
    </section>
  );
}
