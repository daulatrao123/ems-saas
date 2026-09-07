const TONE: Record<string, string> = {
  APPLIED: "bg-emerald-500/15 text-emerald-400",
  ACTIVE: "bg-emerald-500/15 text-emerald-400",
  OK: "bg-emerald-500/15 text-emerald-400",
  PENDING_APPLY: "bg-amber-500/15 text-amber-400",
  HEALTH_CHECK: "bg-amber-500/15 text-amber-400",
  STAGED: "bg-amber-500/15 text-amber-400",
  ACTIVATING: "bg-amber-500/15 text-amber-400",
  WARNING: "bg-amber-500/15 text-amber-400",
  DESIRED: "bg-gray-500/15 text-gray-400",
  UNKNOWN: "bg-gray-500/15 text-gray-400",
  DRIFTED: "bg-orange-500/15 text-orange-400",
  CRITICAL: "bg-orange-500/15 text-orange-400",
  FAILED: "bg-red-500/15 text-red-400",
  ROLLED_BACK: "bg-red-500/15 text-red-400",
};

export function StateBadge({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null;
  return (
    <span data-testid={`badge-${label.toLowerCase()}-${value.toLowerCase()}`}
      className={`px-2 py-0.5 rounded-full text-[10px] font-bold tracking-wide ${TONE[value] || TONE.UNKNOWN}`}
      title={`${label}: ${value}`}>
      {label} {value}
    </span>
  );
}

export function DeviceStateBadges({ dev }: { dev: { config_state?: string | null; ota_state?: string | null; storage_state?: string | null } }) {
  return (
    <div className="flex flex-wrap gap-1.5" data-testid="device-state-badges">
      <StateBadge label="CONFIG" value={dev.config_state} />
      <StateBadge label="STORAGE" value={dev.storage_state} />
      <StateBadge label="OTA" value={dev.ota_state} />
    </div>
  );
}
