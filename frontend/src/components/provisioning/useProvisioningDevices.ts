"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { SocietyDevice } from "./AdminDevices";
import { Dashboard, errorText } from "../ops/types";
import { readRequest, record, text } from "../ops/readRequest";

export function useProvisioningDevices(societyId: string) {
  const owner = useRef<string | null>(societyId);
  const request = useRef(0), controller = useRef(new AbortController());
  const [state, setState] = useState<{ sid: string; devices: SocietyDevice[]; error: string; loading: boolean } | null>(null);
  const load = useCallback(async () => {
    const token = ++request.current;
    controller.current.abort(); controller.current = new AbortController();
    const signal = controller.current.signal;
    const current = () => owner.current === societyId && token === request.current && !signal.aborted;
    setState((s) => ({ sid: societyId, devices: s?.sid === societyId ? s.devices : [], error: "", loading: true }));
    try {
      const { data } = await readRequest(`/api/admin/devices?society_id=${societyId}`, signal);
      if (!current()) return;
      if (!record(data) || String(data.society_id) !== societyId || !Array.isArray(data.devices)
        || !data.devices.every((d) => record(d) && text(d.id) && text(d.name) && typeof d.online === "boolean")) throw new Error("Invalid device inventory");
      setState({ sid: societyId, devices: data.devices as SocietyDevice[], loading: false, error: "" });
      // Ask operations for one current snapshot; it publishes the SAME sync
      // timestamp/link status to both panels. No timers or refresh event loop.
      window.dispatchEvent(new CustomEvent("ems-device-metadata-changed", { detail: { societyId } }));
    } catch (e) { if (current()) setState({ sid: societyId, devices: [], loading: false, error: `Device inventory unavailable: ${errorText(e).detail}` }); }
  }, [societyId]);
  useEffect(() => {
    const scope = owner, reads = controller, counter = request;
    scope.current = societyId;
    let active = true;
    Promise.resolve().then(() => { if (active) void load(); });
    const changed = (event: Event) => {
      const data = (event as CustomEvent<Dashboard>).detail;
      if (String(data?.society_id) !== societyId || !Array.isArray(data.devices)) return;
      setState((s) => s?.sid !== societyId ? s : { ...s, devices: s.devices.map((d) => {
        const snapshot = data.devices.find((v) => v.id === d.id);
        return snapshot ? { ...d, last_sync: snapshot.last_sync ?? null, online: snapshot.connected,
          config_state: snapshot.config_state ?? null, feedback_hardware_installed: snapshot.feedback_hardware_installed === true } : d;
      }) });
    };
    window.addEventListener("ems-dashboard-refreshed", changed);
    return () => { active = false; scope.current = null; ++counter.current; reads.current.abort(); window.removeEventListener("ems-dashboard-refreshed", changed); };
  }, [societyId, load]);
  const current = state?.sid === societyId;
  return { devices: current ? state.devices : [], error: current ? state.error : "", loading: !current || state.loading, load };
}