"use client";

import { useState, useEffect, useCallback } from "react";
import api from "@/lib/api";

export default function MemberDashboard() {
  const [piState, setPiState] = useState<any>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [lastEventId, setLastEventId] = useState<number>(0);
  const [loading, setLoading] = useState(true);

  const fetchPiState = useCallback(async () => {
    try {
      const res = await api.get("/api/member/dashboard");
      setPiState(res.data);
    } catch (error) {
      setPiState((prev: any) => (prev ? { ...prev, connected: false } : null));
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchEvents = useCallback(async () => {
    try {
      const res = await api.get(`/api/member/events?last_id=${lastEventId}`);
      if (res.data.events.length > 0) {
        setEvents((prev) => {
          const existingIds = new Set(prev.map((e: any) => e.id));
          const newEvents = res.data.events.filter((ne: any) => !existingIds.has(ne.id));
          return [...prev, ...newEvents].slice(-50);
        });
        setLastEventId(res.data.last_id);
      }
    } catch (error) { console.error("Failed to fetch events", error); }
  }, [lastEventId]);

  useEffect(() => {
    fetchPiState();
    fetchEvents();
    const interval = setInterval(() => {
      fetchPiState();
      fetchEvents();
    }, 10000);
    return () => clearInterval(interval);
  }, [fetchPiState, fetchEvents]);

  if (loading) return <div>Loading Dashboard...</div>;
  if (!piState) return <div>No Pi data available.</div>;

  const isOnline = Boolean(piState?.connected);

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-4">Member Dashboard</h1>
      
      <div className={`p-4 rounded-lg shadow mb-6 ${isOnline ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}`}>
        <h2 className="text-xl font-semibold">Pi Status: {isOnline ? "Online" : "Offline"}</h2>
        {piState.last_sync && (
          <p className="text-sm">Last Sync: {new Date(piState.last_sync).toLocaleString()}</p>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        {piState.wings && Object.entries(piState.wings).map(([wingId, wing]: [string, any]) => (
          <div key={wingId} className="p-4 border rounded-lg shadow-sm bg-white">
            <h3 className="text-lg font-bold mb-2">{wing.name || `Wing ${wingId}`}</h3>
            <p>Target Days: {wing.target_days}</p>
            <p>Used Days: {wing.used_days}</p>
            <p>Physical Toggle: {wing.physical_toggle}</p>
            {piState.active_wing === wingId && (
              <span className="inline-block mt-2 px-2 py-1 text-xs font-semibold bg-blue-100 text-blue-800 rounded">ACTIVE</span>
            )}
          </div>
        ))}
      </div>

      <div className="mt-6">
        <h3 className="text-lg font-bold mb-2">Pi Events</h3>
        <div className="bg-gray-100 p-4 rounded h-64 overflow-y-auto">
          {events.map((ev: any, i: number) => (
            <div key={`${ev.id}-${i}`} className="text-sm border-b py-1">
              <span className="font-mono text-gray-500">{new Date(ev.ts).toLocaleTimeString()}</span> 
              <span className="ml-2 font-bold text-blue-600">[{ev.level}]</span> 
              <span className="ml-2">{ev.msg}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}