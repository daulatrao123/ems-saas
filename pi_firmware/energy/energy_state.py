"""Persisted per-meter baselines: cumulative kWh -> accepted delta kWh.
Rules: first reading / replaced meter (serial change) => new baseline, no delta.
Backward reading (reset, rollover, replacement without serial change) => new baseline, no delta, event.
Implausible forward jump (> max_kw * elapsed * 1.5 when max_kw configured) => new baseline, no delta, event.
Never a negative delta, never an invented positive delta."""
import json
import os
import time

STATE_VERSION = 1
BACKWARD_TOLERANCE_KWH = 0.0005   # register jitter below the meter resolution is not a "reset"
PLAUSIBILITY_FACTOR = 1.5
MAX_GAP_FOR_DELTA_S = 6 * 3600    # longer communication gaps re-baseline instead of attributing a huge lump


def atomic_write_json(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_json(path, default):
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else default
    except (OSError, ValueError):
        return default


class MeterBaselines:
    """RAM copy + throttled atomic persistence of the last ACCEPTED cumulative reading per meter."""

    def __init__(self, path, write_allowed=lambda: True, now=time.time, persist_interval_s=60.0):
        self.path = path
        self.write_allowed = write_allowed
        self.now = now
        self.persist_interval_s = persist_interval_s
        self.meters = {}
        self.dirty = False
        self._last_persist = 0.0
        data = load_json(path, {})
        if data.get("version") == STATE_VERSION and isinstance(data.get("meters"), dict):
            self.meters = data["meters"]

    def get(self, meter_id):
        return self.meters.get(meter_id)

    def accept(self, meter_id, serial, cumulative_kwh, ts, max_kw=None):
        """-> (delta_kwh or None, event or None). Updates the baseline for the meter in all cases."""
        prev = self.meters.get(meter_id)
        event = None
        delta = None
        if prev is None:
            event = {"type": "BASELINE_SET", "reason": "first accepted reading"}
        elif prev.get("serial") != serial:
            event = {"type": "METER_REPLACED", "reason": f"serial {prev.get('serial')} -> {serial}", "previous_kwh": prev.get("kwh")}
        elif cumulative_kwh < float(prev["kwh"]) - BACKWARD_TOLERANCE_KWH:
            event = {"type": "METER_RESET_OR_ROLLOVER", "reason": f"cumulative decreased {prev['kwh']} -> {cumulative_kwh}", "previous_kwh": prev.get("kwh")}
        elif ts - float(prev["ts"]) > MAX_GAP_FOR_DELTA_S:
            event = {"type": "GAP_REBASELINE", "reason": f"{int(ts - float(prev['ts']))}s since last accepted reading", "skipped_kwh": round(cumulative_kwh - float(prev["kwh"]), 6)}
        else:
            delta = max(0.0, cumulative_kwh - float(prev["kwh"]))
            elapsed_h = max(ts - float(prev["ts"]), 0.0) / 3600.0
            if max_kw and elapsed_h > 0 and delta > float(max_kw) * elapsed_h * PLAUSIBILITY_FACTOR + BACKWARD_TOLERANCE_KWH:
                event = {"type": "IMPLAUSIBLE_JUMP", "reason": f"+{delta:.4f} kWh in {elapsed_h * 3600:.0f}s exceeds max_kw={max_kw}", "previous_kwh": prev.get("kwh")}
                delta = None
        self.meters[meter_id] = {"serial": serial, "kwh": float(cumulative_kwh), "ts": float(ts), "baseline_events": int((prev or {}).get("baseline_events", 0)) + (1 if event else 0)}
        self.dirty = True
        if event:
            event.update({"meter_id": meter_id, "ts": ts, "kwh": float(cumulative_kwh)})
        return delta, event

    def persist(self, force=False):
        """Atomic write, throttled; skipped (kept dirty) while writes are not allowed."""
        if not self.dirty:
            return False
        now = self.now()
        if not force and now - self._last_persist < self.persist_interval_s:
            return False
        if not self.write_allowed():
            return False
        try:
            atomic_write_json(self.path, {"version": STATE_VERSION, "meters": self.meters, "saved_at": now})
        except OSError:
            return False
        self.dirty = False
        self._last_persist = now
        return True
