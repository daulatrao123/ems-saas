"""EnergyEngine: polls the five meters on the RS485 bus in a background thread, converts cumulative
readings to deltas, attributes M1 generation to the physically active wing and keeps the daily ledger.
It never touches GPIO, never enqueues commands and never changes the controller system state (E1 scope)."""
import json
import os
import threading
import time

from .attribution import ATTRIBUTED, FAULT, UNATTRIBUTED, attribute
from .energy_ledger import DailyLedger
from .energy_state import MeterBaselines, atomic_write_json, load_json
from .meter_bus import BusError, build_bus
from .meter_registry import GENERATION, RegistryError, build_registry
from .modbus_meter import ModbusMeter

POLL_INTERVAL_S = 5.0
OFFLINE_AFTER_FAILURES = 3
MAX_EVENTS = 50
MAX_CLOSED_DAYS_PER_SYNC = 7
DISABLED, NOT_CONFIGURED, ONLINE, DEGRADED, OFFLINE = "DISABLED", "NOT_CONFIGURED", "ONLINE", "DEGRADED", "OFFLINE"


class EnergyEngine:
    def __init__(self, data_dir, feedback_provider, operating_date_provider, reset_day_provider, write_allowed, logger,
                 bus_factory=build_bus, now=time.time, poll_interval_s=POLL_INTERVAL_S):
        self.dir = os.path.join(data_dir, "energy")
        self.feedback = feedback_provider
        self.write_allowed = write_allowed
        self.log = logger
        self.bus_factory = bus_factory
        self.now = now
        self.poll_interval_s = poll_interval_s
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self.baselines = MeterBaselines(os.path.join(self.dir, "meter_state.json"), self._writes_ok, now)
        self.ledger = DailyLedger(os.path.join(self.dir, "ledger.json"), operating_date_provider, reset_day_provider, self._writes_ok, now)
        self.config = {}
        self.config_version = None
        self.config_error = None
        self.registry = build_registry({})
        self.bus = None
        self.bus_error = "RS485 bus not configured"
        self.meters = {}
        self.health = {mid: {"comm_status": DISABLED, "last_seen": None, "last_kwh": None, "power_kw": None, "last_error": None, "consecutive_failures": 0}
                       for mid in self.registry}
        self.attribution = {"wing": None, "status": UNATTRIBUTED, "reason": "not polled yet", "ts": None}
        self._m1_interval_attr = None
        self._events = []
        self._events_in_flight = 0
        self._days_in_flight = []
        self._last_loop_error = 0.0
        stored = load_json(os.path.join(self.dir, "config.json"), None)
        if stored:
            self.apply_config(stored, persist=False)

    # ---------------------------------------------------------------- persistence helpers
    def _writes_ok(self):
        try:
            if not self.write_allowed():
                return False
            os.makedirs(self.dir, exist_ok=True)  # only reachable after the secondary volume was verified HEALTHY
            return True
        except OSError:
            return False

    # ---------------------------------------------------------------- configuration (backend-authoritative)
    def apply_config(self, cfg, persist=True):
        """cfg = {"version": n, "bus": {"port", "serial"}, "meters": {"M1": {...}, ...}}. Invalid -> keep previous."""
        if not isinstance(cfg, dict):
            return False, "energy config must be an object"
        with self._lock:
            try:
                registry = build_registry(cfg)
            except RegistryError as exc:
                self.config_error = str(exc)
                self.log.error("Energy config rejected: %s", exc)
                return False, str(exc)
            bus_cfg = cfg.get("bus") or {}
            if bus_cfg != (self.config.get("bus") or {}) or self.bus is None:
                self._close_bus()
                try:
                    self.bus = self.bus_factory(bus_cfg)
                    self.bus_error = None if self.bus is not None else "RS485 bus not configured"
                except BusError as exc:
                    self.bus, self.bus_error = None, str(exc)
                    self.log.error("RS485 bus unavailable: %s", exc)
            self.registry = registry
            self.meters = {mid: ModbusMeter(m, self.bus, self.now) for mid, m in registry.items() if m.pollable and self.bus is not None}
            for mid, m in registry.items():
                h = self.health[mid]
                if not m.enabled:
                    h.update({"comm_status": DISABLED, "last_error": None, "consecutive_failures": 0})
                elif m.config_problems:
                    h.update({"comm_status": NOT_CONFIGURED, "last_error": "; ".join(m.config_problems)})
                elif self.bus is None:
                    h.update({"comm_status": OFFLINE, "last_error": self.bus_error})
            self.config = cfg
            self.config_version = cfg.get("version")
            self.config_error = None
            if persist and self._writes_ok():
                try:
                    atomic_write_json(os.path.join(self.dir, "config.json"), cfg)
                except OSError as exc:
                    self.log.warning("Energy config not persisted: %s", exc)
            return True, None

    def _close_bus(self):
        if self.bus is not None:
            try:
                self.bus.close()
            except Exception:
                pass
        self.bus = None

    # ---------------------------------------------------------------- polling
    def start(self):
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="energy-poll", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        with self._lock:
            self.baselines.persist(force=True)
            self.ledger.persist(force=True)
            self._close_bus()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # metering must never take the controller down
                if self.now() - self._last_loop_error > 60:
                    self._last_loop_error = self.now()
                    self.log.error("Energy poll failure: %s", exc)
            self._stop.wait(self.poll_interval_s)

    def poll_once(self):
        """One pass over all meters. Bus I/O happens outside the lock so snapshot() never waits on RS485."""
        feedback = self.feedback()
        wing, status, reason = attribute(feedback)
        with self._lock:
            meters = list(self.meters.items())
        readings = [(mid, meter.read()) for mid, meter in meters]
        with self._lock:
            self.ledger.roll()
            ts = self.now()
            if (wing, status) != (self.attribution["wing"], self.attribution["status"]):
                self._event("ATTRIBUTION_CHANGED", f"{self.attribution['status']}:{self.attribution['wing']} -> {status}:{wing} ({reason or 'verified feedback'})", ts)
                if status == FAULT:
                    self.log.error("Generation attribution FAULT: %s", reason)
                m1 = dict(readings).get("M1")
                if self._m1_interval_attr is not None and (m1 is None or not m1.ok):
                    # The wing changed somewhere inside an M1 communication gap: that interval cannot be attributed.
                    self._m1_interval_attr = (None, UNATTRIBUTED)
            self.attribution = {"wing": wing, "status": status, "reason": reason, "ts": ts}
            for mid, reading in readings:
                self._ingest(mid, reading, (wing, status))
            self.baselines.persist()
            self.ledger.persist()

    def _ingest(self, mid, reading, current_attr):
        m = self.registry[mid]
        h = self.health[mid]
        self.ledger.record_attempt(mid, reading.ok)
        if not reading.ok:
            h["consecutive_failures"] += 1
            h["last_error"] = reading.error
            h["comm_status"] = OFFLINE if h["consecutive_failures"] >= OFFLINE_AFTER_FAILURES else DEGRADED
            if h["consecutive_failures"] == OFFLINE_AFTER_FAILURES:
                self._event("METER_OFFLINE", f"{mid} {reading.error}", reading.ts)
            return
        if h["comm_status"] == OFFLINE:
            self._event("METER_ONLINE", f"{mid} communication restored", reading.ts)
        h.update({"comm_status": ONLINE, "last_seen": reading.ts, "last_kwh": reading.cumulative_kwh, "power_kw": reading.power_kw,
                  "last_error": None, "consecutive_failures": 0})
        delta, event = self.baselines.accept(mid, m.serial, reading.cumulative_kwh, reading.ts, m.max_kw)
        if event:
            self.ledger.record_event(event)
            self._event(event["type"], f"{mid} {event['reason']}", reading.ts)
            if event["type"] == "GAP_REBASELINE":
                self.ledger.record_gap(mid, event["skipped_kwh"])
        if delta is not None:
            if m.role == GENERATION:
                start = self._m1_interval_attr or current_attr
                self.ledger.record_generation(delta, start[0], start[1])
            else:
                self.ledger.record_consumption(m.wing, delta)
        if m.role == GENERATION:
            self._m1_interval_attr = current_attr  # start of the next interval

    def _event(self, etype, message, ts):
        self._events.append({"type": etype, "message": message[:300], "ts": ts})
        self._events = self._events[-MAX_EVENTS:]

    # ---------------------------------------------------------------- cloud sync (existing /api/pi/sync only)
    def snapshot(self):
        with self._lock:
            days = self.ledger.pending_days()[:MAX_CLOSED_DAYS_PER_SYNC]
            self._days_in_flight = [d["operating_date"] for d in days]
            self._events_in_flight = len(self._events)
            return {
                "schema": 1,
                "config_version": self.config_version,
                "config_error": self.config_error,
                "bus": {"port": (self.config.get("bus") or {}).get("port"), "status": "OK" if self.bus is not None else "UNAVAILABLE", "error": self.bus_error},
                "meters": {mid: {**m.as_dict(), **self.health[mid]} for mid, m in self.registry.items()},
                "attribution": dict(self.attribution),
                "today": self.ledger.snapshot_today(),
                "closed_days": json.loads(json.dumps(days)),
                "events": list(self._events),
            }

    def sync_succeeded(self):
        with self._lock:
            self.ledger.ack_synced(self._days_in_flight)
            self._events = self._events[self._events_in_flight:]
            self._days_in_flight, self._events_in_flight = [], 0
            self.ledger.persist()

    def sync_failed(self):
        with self._lock:
            self._days_in_flight, self._events_in_flight = [], 0

    def status_view(self):
        """Tiny read-only view for the LCD/diagnostics (no I/O)."""
        with self._lock:
            return {"attribution": dict(self.attribution), "online": [mid for mid, h in self.health.items() if h["comm_status"] == ONLINE]}


__all__ = ["ATTRIBUTED", "FAULT", "UNATTRIBUTED", "EnergyEngine"]
