import { record, text, finite } from "../readRequest";

export function ClockQualificationNotice({ report }: { report: unknown }) {
  if (!record(report) || !["REJECTED", "WARNING"].includes(String(report.status))) return null;
  const issues = Array.isArray(report.rejected_rows) ? report.rejected_rows.filter(record).slice(0, 3) : [];
  return <div data-testid="energy-clock-warning" role="alert" className="border border-amber-500/40 bg-amber-500/5 px-4 py-2 text-xs text-amber-300 break-words">
    <div data-testid="energy-clock-warning-status">Last energy sync: {String(report.status)}
      {text(report.sampled_at) ? ` · ${report.sampled_at}` : ""}</div>
    {issues.map((issue, i) => <div key={i} data-testid={`energy-clock-rejected-${i}`}>
      {text(issue.operating_date) ? issue.operating_date : "Unknown date"} · {text(issue.reason) ? issue.reason : "Unqualified ledger row"}
    </div>)}
    {finite(report.open_days) && report.open_days > 1 && <div data-testid="energy-clock-open-backlog">{report.open_days} OPEN ledger days · latest qualified date displayed · reconciliation pending</div>}
    <div data-testid="energy-clock-date-policy">Unqualified rows are excluded from energy totals; reported dates have not been replaced.</div>
  </div>;
}