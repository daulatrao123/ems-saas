import type { ReactNode } from "react";

export function SectionHeading({ id, number, title, context, children }: { id: string; number: string; title: string; context?: string; children?: ReactNode }) {
  return <div data-testid={`${id}-section-heading`} className="ops-section-heading">
    <div className="flex min-w-0 items-center gap-3"><span data-testid={`${id}-section-number`} className="ops-section-number">{number}</span><h2 data-testid={`${id}-section-title`} className="text-base md:text-lg font-semibold">{title}</h2>{context && <span data-testid={`${id}-section-context`} className="ops-category hidden sm:inline-flex">{context}</span>}</div>
    {children}
  </div>;
}

export function DashboardNavigation({ readOnly, hasDevice = true }: { readOnly: boolean; hasDevice?: boolean }) {
  const sections = [["overview", "Overview"], ["energy", "Energy & wings"], ["references", "References"], ["controls", readOnly ? "Days & evidence" : "Controls"], ["history", "History"]];
  return <nav data-testid="dashboard-navigation" aria-label="Dashboard sections" className="ops-navigation">
    {sections.filter(([key]) => hasDevice || key === "overview").map(([key, title]) => <a key={key} href={`#ops-${key}`} data-testid={`dashboard-nav-${key}`}>{title}<span aria-hidden="true">↓</span></a>)}
  </nav>;
}