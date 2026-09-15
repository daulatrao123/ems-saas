"use client";
import { COMMAND_LABEL, CommandRow, LastResponse as LR, TERMINAL, fmtTime, statusTone } from "./types";
import { label, panel } from "./DashboardHeader";

function Row({ k, v, tone, scope }: { k: string; v: string; tone?: string; scope: string }) {
  return <><dt className="text-gray-500">{k}</dt><dd data-testid={`${scope}-${k.toLowerCase().replace(/ /g, "-")}`} className={`min-w-0 break-words ${tone || "text-gray-100"}`}>{v}</dd></>;
}

// Prefers the backend's stored command row (authoritative status/result/timestamps) over the submit response.
export function LastResponse({ last, row: storedRow, scope = "last-response", embedded = false }: { last: LR | null; row: CommandRow | null; scope?: string; embedded?: boolean }) {
  // A logical slot-config save is newer than any stored Pi command row -> show the save, not the stale row.
  const configNewer = !!last && last.kind === "queued" && last.command_id === "config" && (!storedRow || !storedRow.created_at || new Date(last.at) > new Date(storedRow.created_at));
  const row = configNewer ? null : storedRow;
  const contactorVerified = row?.result === "VERIFIED_ON" || row?.result === "VERIFIED_OFF";
  const gpioConfirmed = row?.result === "GPIO_CONFIRMED";
  const verificationLabel = contactorVerified ? "CONTACTOR VERIFIED" : gpioConfirmed ? "GPIO CONFIRMED" : "ACKNOWLEDGED";
  return (
    <section data-testid={scope} className={embedded ? "border-t border-[#1e2a3a] pt-3 min-w-0" : `${panel} p-4`}>
      <div data-testid={`${scope}-heading`} className={label}>{embedded ? "Last Command" : "Device Response"}</div>
      {!last && !row ? <div data-testid={`${scope}-empty`} className="mt-2 text-sm text-gray-500">{embedded ? "—" : "No device-level command issued."}</div> : (
        <>
          <div data-testid={`${scope}-command`} className="mt-2 text-sm font-bold text-white break-words">
            {COMMAND_LABEL[(row?.command || (last as LR).command)] || (row?.command || (last as LR).command).replace(/_/g, " ").toUpperCase()}
            {(row?.slot || (last && last.slot)) && <span className="text-gray-400 font-normal"> · Slot {row?.slot || last?.slot}</span>}
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 font-mono text-[11px]">
            {row ? (
              <>
                <Row scope={scope} k="SEQUENCE" v={`#${row.sequence_no}`} />
                <Row scope={scope} k="STATUS" v={`${row.status === "hardware_verified" ? verificationLabel : row.status.toUpperCase()}${TERMINAL.has(row.status) ? "" : " …"}`} tone={statusTone(row.status)} />
                <Row scope={scope} k="RESULT" v={row.result || "—"} tone={row.result ? "text-cyan-300" : "text-gray-500"} />
                {row.error && <Row scope={scope} k="ERROR" v={row.error} tone="text-red-400" />}
                <Row scope={scope} k="REQUESTED" v={fmtTime(row.created_at)} />
                <Row scope={scope} k="DELIVERED" v={fmtTime(row.delivered_at)} />
                {row.hardware_verified_at && <Row scope={scope} k={verificationLabel} v={fmtTime(row.hardware_verified_at)} tone={contactorVerified ? "text-emerald-300" : "text-gray-300"} />}
                <Row scope={scope} k="COMPLETED" v={fmtTime(row.completed_at)} />
                {Object.keys(row.params || {}).length > 0 && <Row scope={scope} k="PARAMS" v={JSON.stringify(row.params)} />}
              </>
            ) : last && last.kind === "error" ? (
              <>
                <Row scope={scope} k="STATUS" v="FAILED" tone="text-red-400" />
                <Row scope={scope} k="HTTP" v={last.http ? String(last.http) : "—"} />
                <Row scope={scope} k="ERROR" v={last.detail} tone="text-red-400" />
                <Row scope={scope} k="SENT" v={fmtTime(last.at)} />
              </>
            ) : last && last.command_id === "config" ? (
              <>
                <Row scope={scope} k="TYPE" v="LOGICAL SLOT CONFIG" />
                <Row scope={scope} k="STATUS" v="SAVED · config_version bumped" tone="text-emerald-400" />
                <Row scope={scope} k="SENT" v={fmtTime(last.at)} />
              </>
            ) : last && (
              <>
                <Row scope={scope} k="SEQUENCE" v={`#${last.sequence_no}`} />
                <Row scope={scope} k="STATUS" v={last.duplicate ? "DUPLICATE (already queued)" : "QUEUED …"} tone="text-amber-300" />
                <Row scope={scope} k="SENT" v={fmtTime(last.at)} />
              </>
            )}
          </dl>
          {last?.kind === "error" && row && <div data-testid={`${scope}-submit-error`} className="mt-2 font-mono text-[10px] text-red-400">Latest submit failed: {last.detail}</div>}
        </>
      )}
    </section>
  );
}
