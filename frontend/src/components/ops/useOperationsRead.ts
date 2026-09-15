"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { Dashboard, CommandRow, EventRow, errorText } from "./types";
import { readRequest, record } from "./readRequest";
import { dashboardValid, commandsValid, eventsValid } from "./operationsValidation";

export function useOperationsRead(societyId: string | null) {
  const owner = useRef(societyId);
  const generation = useRef(0), dashboardRequest = useRef(0);
  const controller = useRef(new AbortController());
  const panelRequests = useRef<Record<string, number>>({});
  const [loaded, setLoaded] = useState<string | null>(null);
  const [dash, setDash] = useState<Dashboard | null>(null);
  const [commands, setCommands] = useState<Record<string, CommandRow[]>>({});
  const [events, setEvents] = useState<EventRow[]>([]);
  const [error, setError] = useState("");
  const [panelErrors, setPanelErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const loadPanel = useCallback(async (sid: string, did?: string) => {
    const key = did || "events", seq = (panelRequests.current[key] || 0) + 1;
    panelRequests.current[key] = seq;
    const token = generation.current, signal = controller.current.signal;
    const current = () => owner.current === sid && generation.current === token && !signal.aborted && panelRequests.current[key] === seq;
    if (!current()) return;
    if (did) setCommands((prev) => ({ ...prev, [did]: [] })); else setEvents([]);
    try {
      const { data } = await readRequest(did ? `/api/admin/pi-commands?society_id=${sid}&device_id=${did}&limit=25` : `/api/admin/pi-events?society_id=${sid}&latest=50`, signal);
      if (!current()) return;
      if (!record(data) || (did ? !commandsValid(data.commands) : !eventsValid(data.events))) throw new Error("Malformed history");
      if (did) setCommands((prev) => ({ ...prev, [did]: data.commands as CommandRow[] })); else setEvents(data.events as EventRow[]);
      setPanelErrors((prev) => ({ ...prev, [key]: "" }));
    } catch (e) { if (current()) setPanelErrors((prev) => ({ ...prev, [key]: `${did ? "Command history" : "Event history"} unavailable: ${errorText(e).detail}` })); }
  }, []);
  const loadDashboard = useCallback(async (sid: string) => {
    if (owner.current !== sid) return null;
    const seq = ++dashboardRequest.current, token = generation.current, signal = controller.current.signal;
    const current = () => owner.current === sid && generation.current === token && dashboardRequest.current === seq && !signal.aborted;
    try {
      const { data } = await readRequest(`/api/admin/dashboard?society_id=${sid}`, signal);
      if (!current()) return null;
      if (!dashboardValid(data, sid)) throw new Error("Invalid dashboard response");
      setDash(data); setLoaded(sid); setError("");
      window.dispatchEvent(new CustomEvent("ems-dashboard-refreshed", { detail: data }));
      return data;
    } catch (e) { if (current()) { setDash(null); setLoaded(sid); setError(`Dashboard unavailable: ${errorText(e).detail}`); } return null; }
    finally { if (current()) setLoading(false); }
  }, []);
  const refreshAll = useCallback(async (sid: string) => {
    if (owner.current !== sid) return;
    controller.current.abort(); controller.current = new AbortController(); ++generation.current;
    setLoading(true); setPanelErrors({}); setCommands({}); setEvents([]);
    void loadPanel(sid);
    const data = await loadDashboard(sid);
    if (data) data.devices.forEach((d) => { void loadPanel(sid, d.id); });
  }, [loadDashboard, loadPanel]);
  useEffect(() => {
    const scope = owner, reads = controller, counter = generation;
    scope.current = societyId;
    let active = true;
    Promise.resolve().then(() => { if (active) { if (societyId) void refreshAll(societyId); else setLoading(false); } });
    const changed = (event: Event) => {
      if ((event as CustomEvent).detail?.societyId === societyId && societyId) void loadDashboard(societyId);
    };
    window.addEventListener("ems-device-metadata-changed", changed);
    return () => { active = false; scope.current = null; ++counter.current; reads.current.abort(); window.removeEventListener("ems-device-metadata-changed", changed); };
  }, [societyId, refreshAll, loadDashboard]);
  const current = loaded === societyId && !!societyId;
  return { dash: current ? dash : null, commands: current ? commands : {}, events: current ? events : [], error: current ? error : "",
    panelErrors: current ? Object.values(panelErrors).filter(Boolean) : [], loading: !!societyId && (loading || !current),
    loadDashboard, loadCommands: loadPanel, loadEvents: loadPanel, refreshAll };
}