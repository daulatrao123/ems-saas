"use client";
import { useEffect, useRef, useState } from "react";
import { btn, tone } from "./DashboardHeader";
import { openSession, releaseAfterFailure, requestCancel, requestConfirm, type Session } from "./confirmationSession";

export type Severity = "INFO" | "WARNING" | "DANGER" | "CRITICAL";

export type Confirm = {
  title: string;
  body: string;
  action: string;
  consequence?: string;
  severity?: Severity;
  danger?: boolean;
  cancelLabel?: string;
  typed?: string;
  failure?: string;
  onConfirm: () => void | Promise<void | boolean>;
  onCancel?: () => void;
};

export function severityOf(c: Confirm): Severity {
  if (c.severity) return c.severity;
  return c.danger ? "DANGER" : "WARNING";
}

const SEVERITY_CLASS: Record<Severity, string> = {
  INFO: "border-cyan-500/50 text-cyan-200",
  WARNING: "border-amber-500/50 text-amber-200",
  DANGER: "border-red-500/60 text-red-300",
  CRITICAL: "border-red-400 text-red-200",
};

export function runConfirmed(
  ask: (c: Confirm) => void,
  spec: Omit<Confirm, "onConfirm" | "onCancel">,
  action: () => Promise<boolean | void> | boolean | void,
): Promise<"CONFIRMED" | "CANCELLED"> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value: "CONFIRMED" | "CANCELLED") => {
      if (!settled) { settled = true; resolve(value); }
    };
    ask({
      ...spec,
      onCancel: () => finish("CANCELLED"),
      onConfirm: async () => {
        const ok = await action();
        if (ok === false) return false;
        finish("CONFIRMED");
      },
    });
  });
}

export function ConfirmDialog({ c, onClose }: { c: Confirm | null; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const session = useRef<Session>(openSession());
  const [typed, setTyped] = useState("");
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState("");

  useEffect(() => {
    session.current = openSession();
    setTyped("");
    setPending(false);
    setFailure("");
    if (!c) return;
    const node = dialogRef.current;
    if (node && !node.open) node.showModal();
    cancelRef.current?.focus();
    return () => { if (node?.open) node.close(); };
  }, [c]);

  if (!c) return null;
  const severity = severityOf(c);
  const phraseMismatch = !!c.typed && typed !== c.typed;
  const dismiss = (cancelled: boolean) => {
    if (session.current.status === "running") return;
    if (cancelled && session.current.status === "open") {
      session.current = requestCancel(session.current).session;
      c.onCancel?.();
    }
    onClose();
  };
  const accept = async () => {
    session.current = { ...session.current, typed };
    const decision = requestConfirm(session.current, c.typed);
    session.current = decision.session;
    if (!decision.executed) return;
    setPending(true);
    setFailure("");
    try {
      const result = await c.onConfirm();
      if (result === false) {
        session.current = releaseAfterFailure(session.current);
        setPending(false);
        setFailure(c.failure || "The request failed. No change was confirmed.");
        return;
      }
      onClose();
    } catch (err) {
      session.current = releaseAfterFailure(session.current);
      setPending(false);
      setFailure(c.failure || (err instanceof Error && err.message ? err.message : "The request failed. No change was confirmed."));
    }
  };

  return (
    <dialog ref={dialogRef} data-testid="confirm-dialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-body"
      className={`w-[calc(100%-2rem)] max-w-md border bg-[#0f1520] p-5 text-white backdrop:bg-black/70 ${SEVERITY_CLASS[severity]}`}
      onCancel={(event) => { event.preventDefault(); dismiss(true); }}
      onClick={(event) => { if (event.target === event.currentTarget) dismiss(true); }}
      onKeyDown={(event) => { if (event.key === "Enter" && (event.target as HTMLElement).tagName !== "BUTTON") event.preventDefault(); }}>
      <p data-testid="confirm-severity" className="text-[10px] font-bold uppercase tracking-[0.14em]">{severity}</p>
      <h3 id="confirm-title" className="mt-1 text-lg font-bold text-white">{c.title}</h3>
      <p id="confirm-body" className="mt-2 whitespace-pre-line text-sm text-gray-200">{c.body}</p>
      {c.consequence && <p data-testid="confirm-consequence" className="mt-3 whitespace-pre-line border-l-2 border-current/40 pl-3 text-sm text-gray-300">{c.consequence}</p>}
      {c.typed && <label className="mt-4 block text-xs text-gray-400">Type {c.typed} to enable this action
        <input data-testid="confirm-typed" value={typed} autoComplete="off" spellCheck={false} onChange={(event) => setTyped(event.target.value)} className="mt-1 w-full border border-[#2a3646] bg-[#0a0f18] px-3 py-2 font-mono text-sm text-white" />
      </label>}
      {failure && <p data-testid="confirm-failure" role="alert" className="mt-3 text-sm text-red-300">{failure}</p>}
      <div className="mt-5 flex justify-end gap-2">
        <button ref={cancelRef} type="button" data-testid="confirm-cancel" disabled={pending} onClick={() => dismiss(true)} className={`${btn} ${tone.gray}`}>{c.cancelLabel || "Cancel"}</button>
        <button type="button" data-testid="confirm-accept" disabled={pending || phraseMismatch} onClick={() => void accept()} className={`${btn} ${severity === "INFO" || severity === "WARNING" ? tone.amber : tone.red}`}>
          {pending ? "Working…" : c.action}
        </button>
      </div>
    </dialog>
  );
}
