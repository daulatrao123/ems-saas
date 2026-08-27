"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import api from "@/lib/api";

const SOCIETY_ID = "1";

interface WingData {
  used_days: number;
  target_days: number;
  status: string;
  name: string;
  display_name: string;
  disabled: boolean;
  physical_toggle: string;
  clicks: number;
}

interface PendingCommand {
  id: string;
  command: string;
  status: string;
  queued_at: string;
  acked_at: string | null;
  error: string | null;
}

interface DashboardData {
  connected: boolean;
  active_wing: string | null;
  reset_day: number;
  wings: Record<string, WingData>;
  emergency_stop: boolean;
  watchdog_enabled: boolean;
  last_reboot_reason: string;
  firmware_version: string;
  cpu_temp: number;
  uptime_seconds: number;
  last_sync: string;
  pending_command: PendingCommand | null;
}

const CMD_LABELS: Record<string, string> = {
  set_active_wing: "Switch Wing",
  set_days: "Set Days",
  set_reset_day: "Set Reset Day",
  restart: "Restart",
  reboot: "Reboot Pi",
  reset_days: "Reset Days",
  off_wing: "Off Wing",
  off_all: "Off All",
  lcd_display: "LCD Message",
};

export default function AdminPage() {
  const router = useRouter();
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [cmdLoading, setCmdLoading] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  // Modal states
  const [showDaysModal, setShowDaysModal] = useState(false);
  const [showResetDayModal, setShowResetDayModal] = useState(false);
  const [showLcdModal, setShowLcdModal] = useState(false);
  const [daysTarget, setDaysTarget] = useState<Record<string, number>>({});
  const [resetDayVal, setResetDayVal] = useState(15);
  const [lcdLine1, setLcdLine1] = useState("");
  const [lcdLine2, setLcdLine2] = useState("");
  const [lcdDuration, setLcdDuration] = useState("10");

  const fetchDashboard = useCallback(async () => {
    try {
      const res = await api.get(
        `/api/admin/dashboard?society_id=${SOCIETY_ID}`
      );
      setData(res.data);
    } catch (err: any) {
      if (err?.response?.status === 401) {
        router.push("/login");
      }
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    fetchDashboard();
    const interval = setInterval(fetchDashboard, 5000);
    return () => clearInterval(interval);
  }, [fetchDashboard]);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  };

  const sendCmd = async (
    command: string,
    wing: string = "",
    params: Record<string, any> = {}
  ) => {
    setCmdLoading(command);
    try {
      const res = await api.post("/api/admin/pi-command", {
        society_id: SOCIETY_ID,
        command,
        wing: wing || undefined,
        params,
      });
      if (res.data.success) {
        showToast(
          `${CMD_LABELS[command] || command} — queued (${res.data.command_id?.slice(-6)})`
        );
      } else {
        showToast(res.data.message || "Command failed");
      }
    } catch (err: any) {
      const msg =
        err?.response?.data?.detail ||
        err?.response?.data?.message ||
        "Request failed";
      showToast(msg);
    } finally {
      setCmdLoading(null);
      setTimeout(fetchDashboard, 2000);
    }
  };

  const handleSetDays = async () => {
    setShowDaysModal(false);
    for (const [wing, days] of Object.entries(daysTarget)) {
      if (days > 0) {
        await sendCmd("set_days", wing, { days });
        await new Promise((r) => setTimeout(r, 1000));
      }
    }
    setDaysTarget({});
  };

  const handleSetResetDay = () => {
    setShowResetDayModal(false);
    sendCmd("set_reset_day", "", { day: resetDayVal });
  };

  const handleLcd = () => {
    setShowLcdModal(false);
    sendCmd("lcd_display", "", {
      line1: lcdLine1,
      line2: lcdLine2,
      duration: Number(lcdDuration) || 10,
    });
  };

  const logout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("role");
    localStorage.removeItem("name");
    localStorage.removeItem("society_id");
    router.push("/login");
  };

  const formatUptime = (s: number) => {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return `${h}h ${m}m`;
  };

  const wingEntries = data
    ? Object.entries(data.wings)
    : [];

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center">
        <div className="text-gray-400 text-lg">Loading dashboard...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-4 md:p-6">
      {/* Toast */}
      {toast && (
        <div className="fixed top-4 right-4 z-50 bg-gray-800 border border-gray-700 text-green-400 px-4 py-3 rounded-lg shadow-lg text-sm max-w-sm">
          {toast}
        </div>
      )}

      {/* Header */}
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">EMS Dashboard</h1>
          <p className="text-gray-500 text-sm mt-1">
            {data?.connected ? (
              <span className="text-green-400">● Pi Online</span>
            ) : (
              <span className="text-red-400">● Pi Offline</span>
            )}
            {data?.last_sync && (
              <span className="ml-3 text-gray-600">
                Last sync: {new Date(data.last_sync).toLocaleTimeString()}
              </span>
            )}
          </p>
        </div>
        <div className="flex gap-3">
          <button
            onClick={fetchDashboard}
            className="bg-gray-800 hover:bg-gray-700 px-4 py-2 rounded text-sm"
          >
            ↻ Refresh
          </button>
          <button
            onClick={logout}
            className="bg-red-900/50 hover:bg-red-900 px-4 py-2 rounded text-sm text-red-300"
          >
            Logout
          </button>
        </div>
      </div>

      {/* Pi Info Bar */}
      {data && (
        <div className="bg-gray-900 rounded-lg p-4 mb-6 flex flex-wrap gap-6 text-sm">
          <div>
            <span className="text-gray-500">Firmware</span>
            <span className="ml-2 text-white">{data.firmware_version}</span>
          </div>
          <div>
            <span className="text-gray-500">CPU</span>
            <span
              className={`ml-2 ${
                data.cpu_temp > 85
                  ? "text-red-400"
                  : data.cpu_temp > 70
                  ? "text-yellow-400"
                  : "text-green-400"
              }`}
            >
              {data.cpu_temp}°C
            </span>
          </div>
          <div>
            <span className="text-gray-500">Uptime</span>
            <span className="ml-2 text-white">
              {formatUptime(data.uptime_seconds)}
            </span>
          </div>
          <div>
            <span className="text-gray-500">Reset Day</span>
            <span className="ml-2 text-white">{data.reset_day}th</span>
          </div>
          {data.watchdog_enabled && (
            <div>
              <span className="text-gray-500">Watchdog</span>
              <span className="ml-2 text-green-400">Active</span>
            </div>
          )}
          {data.pending_command && (
            <div>
              <span className="text-gray-500">Command</span>
              <span
                className={`ml-2 ${
                  data.pending_command.status === "pending"
                    ? "text-yellow-400"
                    : data.pending_command.status === "acknowledged"
                    ? "text-green-400"
                    : "text-red-400"
                }`}
              >
                {data.pending_command.command} ({data.pending_command.status})
              </span>
            </div>
          )}
        </div>
      )}

      {/* System Controls */}
      <div className="mb-6">
        <h2 className="text-lg font-semibold text-gray-300 mb-3">
          System Controls
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <button
            onClick={() => sendCmd("reset_days")}
            disabled={cmdLoading === "reset_days"}
            className="bg-gray-800 hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed p-4 rounded-lg text-center border border-gray-700"
          >
            <div className="text-2xl mb-1">↻</div>
            <div className="text-sm">Reset Days</div>
          </button>
          <button
            onClick={() => sendCmd("off_all")}
            disabled={cmdLoading === "off_all"}
            className="bg-gray-800 hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed p-4 rounded-lg text-center border border-gray-700"
          >
            <div className="text-2xl mb-1">⏻</div>
            <div className="text-sm">OFF All</div>
          </button>
          <button
            onClick={() => sendCmd("restart")}
            disabled={cmdLoading === "restart"}
            className="bg-gray-800 hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed p-4 rounded-lg text-center border border-gray-700"
          >
            <div className="text-2xl mb-1">▶</div>
            <div className="text-sm">Restart</div>
          </button>
          <button
            onClick={() => sendCmd("reboot")}
            disabled={cmdLoading === "reboot"}
            className="bg-gray-800 hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed p-4 rounded-lg text-center border border-gray-700"
          >
            <div className="text-2xl mb-1">⟳</div>
            <div className="text-sm">Reboot Pi</div>
          </button>
        </div>
      </div>

      {/* Configuration */}
      <div className="mb-6">
        <h2 className="text-lg font-semibold text-gray-300 mb-3">
          Configuration
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <button
            onClick={() => {
              if (data) setDaysTarget(
                Object.fromEntries(
                  wingEntries.map(([id]) => [id, 0])
                )
              );
              setShowDaysModal(true);
            }}
            className="bg-gray-800 hover:bg-gray-700 p-4 rounded-lg text-left border border-gray-700"
          >
            <div className="text-sm font-medium">Set Days</div>
            <div className="text-xs text-gray-500 mt-1">
              Change target days per wing
            </div>
          </button>
          <button
            onClick={() => {
              if (data) setResetDayVal(data.reset_day);
              setShowResetDayModal(true);
            }}
            className="bg-gray-800 hover:bg-gray-700 p-4 rounded-lg text-left border border-gray-700"
          >
            <div className="text-sm font-medium">Set Reset Day</div>
            <div className="text-xs text-gray-500 mt-1">
              Current: {data?.reset_day}th
            </div>
          </button>
          <button
            onClick={() => setShowLcdModal(true)}
            className="bg-gray-800 hover:bg-gray-700 p-4 rounded-lg text-left border border-gray-700"
          >
            <div className="text-sm font-medium">LCD Message</div>
            <div className="text-xs text-gray-500 mt-1">
              Show custom text on Pi display
            </div>
          </button>
        </div>
      </div>

      {/* Wing Cards */}
      {wingEntries.length > 0 && (
        <div className="mb-6">
          <h2 className="text-lg font-semibold text-gray-300 mb-3">Wings</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {wingEntries.map(([id, w]) => {
              const isActive = data?.active_wing === id;
              const pct = w.target_days > 0
                ? Math.round((w.used_days / w.target_days) * 100)
                : 0;
              const isFull = w.used_days >= w.target_days;

              return (
                <div
                  key={id}
                  className={`rounded-lg border p-4 ${
                    isActive
                      ? "bg-green-950/30 border-green-800"
                      : isFull
                      ? "bg-red-950/20 border-red-900/50"
                      : "bg-gray-900 border-gray-800"
                  }`}
                >
                  <div className="flex justify-between items-start mb-3">
                    <div>
                      <h3 className="text-lg font-bold text-white">
                        {w.display_name || w.name}
                      </h3>
                      <span
                        className={`text-xs px-2 py-0.5 rounded ${
                          isActive
                            ? "bg-green-900 text-green-300"
                            : "bg-gray-800 text-gray-400"
                        }`}
                      >
                        {isActive ? "ACTIVE" : "IDLE"}
                      </span>
                    </div>
                    <div
                      className={`text-xs px-2 py-0.5 rounded ${
                        w.physical_toggle === "ON"
                          ? "bg-blue-900 text-blue-300"
                          : "bg-gray-800 text-gray-500"
                      }`}
                    >
                      Toggle: {w.physical_toggle}
                    </div>
                  </div>

                  {/* Progress */}
                  <div className="mb-3">
                    <div className="flex justify-between text-sm mb-1">
                      <span className="text-gray-400">
                        {w.used_days} / {w.target_days} days
                      </span>
                      <span
                        className={
                          isFull ? "text-red-400" : "text-gray-500"
                        }
                      >
                        {pct}%
                      </span>
                    </div>
                    <div className="w-full bg-gray-800 rounded-full h-2">
                      <div
                        className={`h-2 rounded-full transition-all ${
                          isFull ? "bg-red-500" : "bg-green-500"
                        }`}
                        style={{ width: `${Math.min(pct, 100)}%` }}
                      />
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex gap-2">
                    {!isActive && !isFull && (
                      <button
                        onClick={() => sendCmd("set_active_wing", id)}
                        disabled={cmdLoading === "set_active_wing"}
                        className="flex-1 bg-green-800 hover:bg-green-700 disabled:opacity-50 text-sm py-2 rounded font-medium"
                      >
                        ON
                      </button>
                    )}
                    {isActive && (
                      <button
                        onClick={() => sendCmd("off_wing", id)}
                        disabled={cmdLoading === "off_wing"}
                        className="flex-1 bg-red-800 hover:bg-red-700 disabled:opacity-50 text-sm py-2 rounded font-medium"
                      >
                        OFF
                      </button>
                    )}
                  </div>

                  <div className="text-xs text-gray-600 mt-2">
                    Clicks: {w.clicks}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {wingEntries.length === 0 && data && (
        <div className="bg-gray-900 rounded-lg p-8 text-center text-gray-500 mb-6">
          <div className="text-4xl mb-2">📡</div>
          <p>No active wings detected.</p>
          <p className="text-sm mt-1">
            Make sure physical toggle switches are ON for wings you want to control.
          </p>
        </div>
      )}

      {!data?.connected && (
        <div className="bg-red-950/20 border border-red-900/50 rounded-lg p-8 text-center text-red-400 mb-6">
          <div className="text-4xl mb-2">⚠️</div>
          <p className="font-medium">Pi is offline</p>
          <p className="text-sm mt-1 text-red-500">
            Commands will be queued and executed when the Pi reconnects.
          </p>
        </div>
      )}

      {/* ===== MODALS ===== */}

      {/* Set Days Modal */}
      {showDaysModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="bg-gray-900 border border-gray-700 rounded-lg p-6 w-full max-w-md mx-4">
            <h3 className="text-lg font-bold text-white mb-4">Set Target Days</h3>
            <div className="space-y-3">
              {wingEntries.map(([id, w]) => (
                <div key={id} className="flex items-center gap-3">
                  <label className="w-20 text-sm text-gray-400">
                    {w.display_name || id}
                  </label>
                  <input
                    type="number"
                    min="1"
                    max="31"
                    defaultValue={w.target_days}
                    onChange={(e) =>
                      setDaysTarget((prev) => ({
                        ...prev,
                        [id]: Number(e.target.value),
                      }))
                    }
                    className="flex-1 bg-gray-800 border border-gray-700 rounded px-3 py-2 text-white text-sm"
                  />
                  <span className="text-xs text-gray-600">days</span>
                </div>
              ))}
            </div>
            <div className="flex gap-3 mt-6">
              <button
                onClick={() => setShowDaysModal(false)}
                className="flex-1 bg-gray-800 hover:bg-gray-700 py-2 rounded text-sm"
              >
                Cancel
              </button>
              <button
                onClick={handleSetDays}
                className="flex-1 bg-green-700 hover:bg-green-600 py-2 rounded text-sm font-medium"
              >
                Apply
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Set Reset Day Modal */}
      {showResetDayModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="bg-gray-900 border border-gray-700 rounded-lg p-6 w-full max-w-sm mx-4">
            <h3 className="text-lg font-bold text-white mb-4">
              Set Reset Day
            </h3>
            <div className="flex items-center gap-3 mb-6">
              <span className="text-gray-400">Every month on the</span>
              <input
                type="number"
                min="1"
                max="28"
                value={resetDayVal}
                onChange={(e) => setResetDayVal(Number(e.target.value))}
                className="w-20 bg-gray-800 border border-gray-700 rounded px-3 py-2 text-white text-center text-sm"
              />
              <span className="text-gray-400">th</span>
            </div>
            <div className="flex gap-3">
              <button
                onClick={() => setShowResetDayModal(false)}
                className="flex-1 bg-gray-800 hover:bg-gray-700 py-2 rounded text-sm"
              >
                Cancel
              </button>
              <button
                onClick={handleSetResetDay}
                className="flex-1 bg-green-700 hover:bg-green-600 py-2 rounded text-sm font-medium"
              >
                Apply
              </button>
            </div>
          </div>
        </div>
      )}

      {/* LCD Modal */}
      {showLcdModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="bg-gray-900 border border-gray-700 rounded-lg p-6 w-full max-w-md mx-4">
            <h3 className="text-lg font-bold text-white mb-4">
              LCD Display Message
            </h3>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-gray-500 mb-1">
                  Line 1 (max 16 chars)
                </label>
                <input
                  type="text"
                  maxLength={16}
                  value={lcdLine1}
                  onChange={(e) => setLcdLine1(e.target.value)}
                  placeholder="MAINTENANCE"
                  className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-white text-sm font-mono"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500 mb-1">
                  Line 2 (max 16 chars)
                </label>
                <input
                  type="text"
                  maxLength={16}
                  value={lcdLine2}
                  onChange={(e) => setLcdLine2(e.target.value)}
                  placeholder="BACK IN 30MIN"
                  className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-white text-sm font-mono"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500 mb-1">
                  Duration (seconds)
                </label>
                <input
                  type="number"
                  min="1"
                  max="120"
                  value={lcdDuration}
                  onChange={(e) => setLcdDuration(e.target.value)}
                  className="w-24 bg-gray-800 border border-gray-700 rounded px-3 py-2 text-white text-sm"
                />
              </div>
            </div>
            {/* Preview */}
            {(lcdLine1 || lcdLine2) && (
              <div className="mt-4 bg-gray-950 border border-gray-800 rounded p-3">
                <div className="text-xs text-gray-600 mb-2">Preview</div>
                <div className="font-mono text-green-400 text-sm">
                  <div>{lcdLine1.padEnd(16)}</div>
                  <div>{lcdLine2.padEnd(16)}</div>
                </div>
              </div>
            )}
            <div className="flex gap-3 mt-6">
              <button
                onClick={() => setShowLcdModal(false)}
                className="flex-1 bg-gray-800 hover:bg-gray-700 py-2 rounded text-sm"
              >
                Cancel
              </button>
              <button
                onClick={handleLcd}
                disabled={!lcdLine1 && !lcdLine2}
                className="flex-1 bg-green-700 hover:bg-green-600 disabled:opacity-50 py-2 rounded text-sm font-medium"
              >
                Send to LCD
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}