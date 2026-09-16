"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import api from "@/lib/api";
import { errorText } from "../ops/types";
import { Save } from "./types";

export function useRead<T>(url: string, valid: (v: T) => boolean) {
  const [tick, setTick] = useState(0), [entry, setEntry] = useState<{ url: string; data?: T; error: string; loading: boolean }>({ url, error: "", loading: true });
  useEffect(() => {
    const controller = new AbortController();
    Promise.resolve().then(async () => {
      if (controller.signal.aborted) return;
      setEntry(e => ({ url, data: e.url === url ? e.data : undefined, error: "", loading: true }));
      try {
        const { data } = await api.get(url, { signal: controller.signal });
        if (!valid(data)) throw new Error("Invalid configuration response");
        if (!controller.signal.aborted) setEntry({ url, data, error: "", loading: false });
      } catch (e) { if (!controller.signal.aborted) setEntry({ url, error: errorText(e).detail, loading: false }); }
    });
    return () => controller.abort();
  }, [url, tick, valid]);
  return { data: entry.url === url ? entry.data : undefined, error: entry.url === url ? entry.error : "", loading: entry.url !== url || entry.loading, refresh: useCallback(() => setTick(t => t + 1), []) };
}

export function useDraft<T>(initial: T, version: number, name: string, mark: (name: string, dirty: boolean) => void) {
  const [edit, setEdit] = useState<{ draft: T; base: number } | null>(null);
  const dirty = edit !== null;
  useEffect(() => { mark(name, dirty); return () => mark(name, false); }, [name, dirty, mark]);
  return { draft: edit?.draft ?? initial, dirty, base: edit?.base ?? version,
    change: (next: T) => setEdit(old => ({ draft: next, base: old?.base ?? version })), reset: () => setEdit(null), saved: () => setEdit(null) };
}

export function useSave(sid: string, did: string, refresh: () => void) {
  const lock = useRef(false), alive = useRef(true);
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState(""), [error, setError] = useState("");
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const save: Save = async (method, path, body, label) => {
    if (lock.current) return false;
    lock.current = true; setBusy(true); setNotice(""); setError("");
    try {
      const { data } = await api.request({ method, url: `/api/energy/${path}`, data: { ...body, society_id: sid, device_id: did } });
      if (data.success !== true) throw new Error("Save not confirmed");
      if (alive.current) { setNotice(`${label} saved. Controller evidence is reported separately.`); refresh(); }
      return true;
    } catch (e) { if (alive.current) setError(`${errorText(e).detail}. Save not confirmed; refresh before retrying.`); return false; }
    finally { lock.current = false; if (alive.current) setBusy(false); }
  };
  return { save, busy, notice, error };
}