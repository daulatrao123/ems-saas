export type SessionStatus = "closed" | "open" | "running";

export type Session = { status: SessionStatus; typed: string; executions: number };

export function openSession(): Session {
  return { status: "open", typed: "", executions: 0 };
}

export function requestCancel(session: Session): { session: Session; executed: false } {
  if (session.status !== "open") return { session, executed: false };
  return { session: { ...session, status: "closed" }, executed: false };
}

export function requestBackdrop(session: Session): { session: Session; executed: false } {
  return requestCancel(session);
}

export function requestEscape(session: Session): { session: Session; executed: false } {
  return requestCancel(session);
}

export function requestConfirm(session: Session, requiredPhrase?: string): { session: Session; executed: boolean } {
  if (session.status !== "open") return { session, executed: false };
  if (requiredPhrase && session.typed !== requiredPhrase) return { session, executed: false };
  return { session: { ...session, status: "running", executions: session.executions + 1 }, executed: true };
}

export function releaseAfterFailure(session: Session): Session {
  if (session.status !== "running") return session;
  return { ...session, status: "open" };
}
