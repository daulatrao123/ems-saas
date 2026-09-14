"use client";
import { createContext, useContext } from "react";
import { btn, tone } from "../DashboardHeader";
import { WingCode } from "./types";

export const BillHistoryContext = createContext<{ open: (wing: WingCode) => void; readOnly: boolean } | null>(null);

export function BillHistoryButton({ wing }: { wing?: WingCode }) {
  const context = useContext(BillHistoryContext);
  if (!context) return null;
  return <button data-testid={`bills-open-${wing || "society"}`} onClick={() => context.open(wing || "A")} className={`${btn} ${tone.cyan} whitespace-normal max-w-full`}>
    {context.readOnly ? "VIEW BILLS / CONSUMPTION" : "ADD BILLS / CONSUMPTION"}
  </button>;
}