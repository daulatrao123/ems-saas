"use client";
import { useState } from "react";
import { QueueFn } from "./useOperations";
import { btn, input, label, panel, tone } from "./DashboardHeader";

const COLS = 16;
// lcd_display -> DISPLAY_UPDATED only if the firmware really drove a display; current firmware reports DISPLAY_UNAVAILABLE.
export function LcdControl({ deviceId, queue, isPending }: { deviceId: string; queue: QueueFn; isPending: (d: string, c: string, s?: string) => boolean }) {
  const [l1, setL1] = useState(""); const [l2, setL2] = useState(""); const [dur, setDur] = useState("10");
  const busy = isPending(deviceId, "lcd_display"); const d = Number(dur);
  const validDur = Number.isInteger(d) && d >= 1 && d <= 300;
  return (
    <section data-testid={`lcd-control-${deviceId}`} className={`${panel} p-4`}>
      <div className={label}>LCD Display · 16×2</div>
      <pre data-testid="lcd-preview" className="mt-3 inline-block border-4 border-[#1f3a1f] bg-[#0b2a12] px-3 py-2 font-mono text-[15px] leading-6 tracking-[0.18em] text-[#7CFC9A] shadow-[inset_0_0_18px_#000]">
        {l1.padEnd(COLS).slice(0, COLS)}{"\n"}{l2.padEnd(COLS).slice(0, COLS)}
      </pre>
      <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_1fr_80px]">
        <input data-testid={`lcd-line1-${deviceId}`} maxLength={COLS} placeholder="Line 1" value={l1} onChange={(e) => setL1(e.target.value)} className={input} />
        <input data-testid={`lcd-line2-${deviceId}`} maxLength={COLS} placeholder="Line 2" value={l2} onChange={(e) => setL2(e.target.value)} className={input} />
        <input data-testid={`lcd-duration-${deviceId}`} type="number" min={1} max={300} value={dur} onChange={(e) => setDur(e.target.value)} className={`${input} text-center`} title="Seconds (1-300)" />
      </div>
      <div className="mt-2 flex items-center gap-2">
        <button data-testid={`cmd-lcd_display-${deviceId}`} disabled={busy || (!l1 && !l2) || !validDur} onClick={() => queue(deviceId, "lcd_display", "", { line1: l1, line2: l2, duration: d })} className={`${btn} ${tone.cyan}`}>{busy ? "SENDING…" : "SEND"}</button>
        <button data-testid="lcd-clear" onClick={() => { setL1(""); setL2(""); }} className={`${btn} ${tone.gray}`}>CLEAR</button>
        <span className="font-mono text-[10px] text-gray-500">{l1.length}/{COLS} · {l2.length}/{COLS}</span>
      </div>
    </section>
  );
}
