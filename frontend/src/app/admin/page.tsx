"use client";

import { useState, useEffect, useCallback } from "react";
import api from "@/lib/api"; // Adjust import path as needed

export default function AdminDashboard() {
  const [societyId, setSocietyId] = useState<string | null>(null);
  const [dashboardData, setDashboardData] = useState<any>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [lastEventId, setLastEventId] = useState<number>(0);

  // 1. Extract Society ID from JWT on component mount
  useEffect(() => {
    const token = localStorage.getItem("token");
    if (token) {
      try {
        const payload = JSON.parse(atob(token.split(".")[1]));
        if (payload.society_id) {
          setSocietyId(String(payload.society_id));
        } else {
          console.error("No society_id found in token");
        }
      } catch (e) {
        console.error("Failed to parse token", e);
      }
    }
  }, []);

  // 2. Fetch Dashboard Data using dynamic Society ID
  const fetchDashboard = useCallback(async () => {
    if (!societyId) return;
    try {
      const res = await api.get(`/api/admin/dashboard?society_id=${societyId}`);
      setDashboardData(res.data);
    } catch (error) {
      console.error("Failed to fetch dashboard", error);
    }
  }, [societyId]);

  // 3. Fetch Events using Cursor Pagination (last_id)
  const fetchEvents = useCallback(async () => {
    if (!societyId) return;
    try {
      const res = await api.get(`/api/admin/pi-events?society_id=${societyId}&last_id=${lastEventId}`);
      if (res.data.events.length > 0) {
        setEvents((prev) => [...prev, ...res.data.events].slice(-100)); // Keep last 100
        setLastEventId(res.data.last_id);
      }
    } catch (error) {
      console.error("Failed to fetch events", error);
    }
  }, [societyId, lastEventId]);

  useEffect(() => {
    fetchDashboard();
    fetchEvents();
    const interval = setInterval(() => {
      fetchDashboard();
      fetchEvents();
    }, 5000); // Poll every 5s for Admin
    return () => clearInterval(interval);
  }, [fetchDashboard, fetchEvents]);

  if (!societyId) return <div>Loading Admin Session...</div>;
  if (!dashboardData) return <div>Loading Dashboard...</div>;

  const isOnline = Boolean(dashboardData.connected);

  // Example command sender
  const sendCommand = async (command: string, wing?: string) => {
    try {
      await api.post("/api/admin/pi-command", {
        society_id: societyId, // Use dynamic ID
        command,
        wing: wing || "",
        params: {}
      });
      fetchDashboard(); // Refresh immediately
    } catch (error) {
      console.error("Command failed", error);
    }
  };

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-4">Admin Dashboard</h1>
      <div className={`p-4 rounded mb-6 ${isOnline ? "bg-green-100" : "bg-red-100"}`}>
        <h2 className="text-xl">Status: {isOnline ? "ONLINE" : "OFFLINE"}</h2>
        <p className="text-sm mt-1">Boots: {dashboardData.boot_count ?? "undefined"}</p>
        <p className="text-sm mt-1">Uptime: {Math.floor(dashboardData.uptime_seconds / 60)}m</p>
        <button onClick={() => sendCommand("off_all")} className="mt-2 bg-red-500 text-white px-4 py-2 rounded">OFF ALL</button>
      </div>

      <div className="mt-6">
        <h3 className="text-lg font-bold mb-2">Pi Events</h3>
        <div className="bg-gray-100 p-4 rounded h-64 overflow-y-auto">
          {events.map((ev: any, i: number) => (
            <div key={i} className="text-sm border-b py-1">
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