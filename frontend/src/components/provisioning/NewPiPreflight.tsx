"use client";
import { useEffect, useState } from "react";
import api from "@/lib/api";
import { label, panel } from "@/components/ops/DashboardHeader";
import { errorText } from "@/components/ops/types";

type Item = {
  id: string;
  title: string;
  expectation: string;
  verification: string;
  detail: string;
  modules?: string[];
  conflicts?: string[];
  paths?: string[];
  runtime_files?: string[];
};

type Report = {
  device_inspected: boolean;
  compliant: boolean;
  summary: string;
  labels: { expected: string; verified: string; unverified: string };
  items: Item[];
};

function Badge({ text, kind }: { text: string; kind: "expected" | "unverified" | "verified" }) {
  const color = kind === "verified"
    ? "border-emerald-500/40 text-emerald-300"
    : kind === "expected"
      ? "border-amber-500/40 text-amber-200"
      : "border-gray-600 text-gray-300";
  return <span className={`border px-1.5 py-0.5 font-mono text-[10px] font-bold ${color}`}>{text}</span>;
}

export function NewPiPreflight() {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    api.get("/api/super-admin/provisioning-preflight")
      .then((r) => { if (live) setReport(r.data as Report); })
      .catch((e) => { if (live) setError(errorText(e).detail); });
    return () => { live = false; };
  }, []);
  return (
    <div data-testid="new-pi-preflight" className={`${panel} p-3 space-y-2`}>
      <div className={label}>New-Pi provisioning preflight</div>
      <p className="text-[11px] text-gray-400">Raspberry Pi Imager prepares Raspberry Pi OS. EMS is installed afterward with the existing provisioning ZIP and <span className="font-mono">sudo ./install.sh</span>. This list does not write a USB disk or build an image.</p>
      {error && <div data-testid="new-pi-preflight-error" role="alert" className="text-xs text-red-300">{error}</div>}
      {!report && !error && <div className="font-mono text-[11px] text-gray-500">Loading installer prerequisites…</div>}
      {report && (
        <>
          <p data-testid="new-pi-preflight-summary" className="text-[11px] text-gray-300">{report.summary}</p>
          <div className="flex flex-wrap gap-2" data-testid="new-pi-preflight-status">
            <Badge text={report.labels.expected} kind="expected" />
            <Badge text={report.device_inspected ? report.labels.verified : report.labels.unverified} kind={report.device_inspected ? "verified" : "unverified"} />
            <span className="font-mono text-[10px] text-gray-500">{report.compliant ? "COMPLIANT" : "NOT A COMPLIANCE RESULT"}</span>
          </div>
          <ol className="space-y-2">
            {report.items.map((item) => (
              <li key={item.id} data-testid={`new-pi-preflight-${item.id}`} className="border border-[#1e2a3a] px-2 py-1.5">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs font-bold text-white">{item.title}</span>
                  <Badge text={item.expectation} kind="expected" />
                  <Badge text={item.verification} kind={item.verification === report.labels.verified ? "verified" : "unverified"} />
                </div>
                <p className="mt-1 text-[11px] text-gray-400">{item.detail}</p>
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
}
