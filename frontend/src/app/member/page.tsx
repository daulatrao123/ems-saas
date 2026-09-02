"use client";

import { useState, useEffect, useCallback } from "react";
import api from "@/lib/api"; // Adjust import path as needed

export default function MemberDashboard() {
  const [piState, setPiState] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  const fetchPiState = useCallback(async () => {
    try {
      const res = await api.get("/api/member/dashboard");
      setPiState(res.data);
    } catch (error) {
      // CRITICAL FIX: If API fails, mark Pi as offline, but keep last known metrics
      setPiState((prev: any) => (prev ? { ...prev, connected: false } : null));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPiState();
    const interval = setInterval(fetchPiState, 10000); // Poll every 10s
    return () => clearInterval(interval);
  }, [fetchPiState]);

  if (loading) return <div>Loading Dashboard...</div>;
  if (!piState) return <div>No Pi data available.</div>;

  // CRITICAL FIX: Trust backend's 120s threshold completely
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

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {piState.wings && Object.entries(piState.wings).map(([wingId, wing]: [string, any]) => (
          <div key={wingId} className="p-4 border rounded-lg shadow-sm bg-white">
            <h3 className="text-lg font-bold mb-2">Wing {wingId}</h3>
            <p>Target Days: {wing.target_days}</p>
            <p>Used Days: {wing.used_days}</p>
            <p>Physical Toggle: {wing.physical_toggle}</p>
            {piState.active_wing === wingId && (
              <span className="inline-block mt-2 px-2 py-1 text-xs font-semibold bg-blue-100 text-blue-800 rounded">ACTIVE</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}