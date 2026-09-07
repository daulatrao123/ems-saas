"use client";
import { QueueFn, SystemControls, ResetDayControl, LcdControl } from "./Controls";
import { DaysCalculator } from "./DaysCalculator";

type SlotMeta = { code: string; name: string; disabled: boolean };

// Device-wide operational controls. Commands are queued through the existing /api/admin/pi-command
// contract; convergence is shown by the existing config/OTA/storage badges — no polling here.
export function OperationalControls({ deviceId, slots, resetDay, queue }: { deviceId: string; slots: SlotMeta[]; resetDay: number | null; queue: QueueFn }) {
  return (
    <div className="mt-5 border-t border-gray-800 pt-4 space-y-3" data-testid={`operational-controls-${deviceId}`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-[10px] uppercase tracking-wide text-gray-500">Operational controls</span>
        <span className="text-[10px] text-gray-500">Queued commands run on the Pi&apos;s next sync. Physical state is confirmed only by verified feedback.</span>
      </div>
      <SystemControls deviceId={deviceId} queue={queue} />
      <ResetDayControl deviceId={deviceId} current={resetDay} queue={queue} />
      <LcdControl deviceId={deviceId} queue={queue} />
      <DaysCalculator deviceId={deviceId} slots={slots} queue={queue} />
    </div>
  );
}
