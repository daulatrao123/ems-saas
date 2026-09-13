"""Local daily energy ledger keyed by the EMS operating date (local calendar date; reset period follows
the existing reset-day convention: day >= reset_day -> current YYYY-MM, else previous month).
Measured quantities only (raw M1, per-wing attributed generation, per-wing consumption, availability).
Targets/balances/surplus are computed by the backend from these authoritative rows (E2).
Persisted atomically under <energy dir>/ledger.json: OPEN day + bounded CLOSED days awaiting sync."""
import json
import time
from datetime import date

from .energy_state import atomic_write_json, load_json

LEDGER_VERSION = 1
MAX_CLOSED_DAYS = 62
MAX_DAY_EVENTS = 50
WINGS = ("A", "B", "C", "D")
PHYSICAL = "PHYSICAL"
UNAVAILABLE = "UNAVAILABLE"


def reset_period_for(operating_date, reset_day):
    d = date.fromisoformat(operating_date)
    reset_day = max(1, min(28, int(reset_day)))
    if d.day >= reset_day:
        return f"{d.year:04d}-{d.month:02d}"
    y, m = (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)
    return f"{y:04d}-{m:02d}"


def new_day(operating_date, reset_day, now):
    return {
        "operating_date": operating_date,
        "reset_period": reset_period_for(operating_date, reset_day),
        "status": "OPEN",
        "opened_at": now, "closed_at": None, "updated_at": now,
        # raw common generation (M1) is kept separately from any attribution
        "generation_kwh": None, "generation_source": UNAVAILABLE,
        "wing_generation": {w: None for w in WINGS},
        "unattributed_generation_kwh": 0.0, "fault_generation_kwh": 0.0,
        "wing_consumption": {w: None for w in WINGS},
        "consumption_source": {w: UNAVAILABLE for w in WINGS},
        # availability: accepted samples vs poll attempts per meter, for completeness/confidence downstream
        "samples": {}, "attempts": {},
        "gap_kwh": {},          # energy that passed through a meter during a re-baseline gap (not attributed anywhere)
        "events": [],
    }


class DailyLedger:
    def __init__(self, path, operating_date_provider, reset_day_provider, write_allowed=lambda: True, now=time.time,
                 persist_interval_s=60.0):
        self.path = path
        self.operating_date = operating_date_provider
        self.reset_day = reset_day_provider
        self.write_allowed = write_allowed
        self.now = now
        self.persist_interval_s = persist_interval_s
        self.dirty = False
        self._last_persist = 0.0
        data = load_json(path, {})
        self.today = data.get("today") if data.get("version") == LEDGER_VERSION else None
        self.closed = list(data.get("closed", [])) if data.get("version") == LEDGER_VERSION else []
        self.roll()

    # ---------------------------------------------------------------- day handling
    def roll(self):
        """Close the OPEN day when the operating date changed; open the current one. Idempotent."""
        today = self.operating_date()
        if self.today is not None and self.today["operating_date"] != today:
            self.today["status"] = "CLOSED"
            self.today["closed_at"] = self.now()
            self.closed.append(self.today)
            self.closed = self.closed[-MAX_CLOSED_DAYS:]
            self.today = None
            self.dirty = True
        if self.today is None:
            self.today = new_day(today, self.reset_day(), self.now())
            self.dirty = True
        return self.today

    def _touch(self):
        self.today["updated_at"] = self.now()
        self.dirty = True

    def _add(self, container, key, delta):
        container[key] = round((container.get(key) or 0.0) + float(delta), 6)

    # ---------------------------------------------------------------- recording
    def record_attempt(self, meter_id, ok):
        day = self.roll()
        day["attempts"][meter_id] = int(day["attempts"].get(meter_id, 0)) + 1
        if ok:
            day["samples"][meter_id] = int(day["samples"].get(meter_id, 0)) + 1
        self._touch()

    def record_consumption(self, wing, delta_kwh):
        day = self.roll()
        self._add(day["wing_consumption"], wing, delta_kwh)
        day["consumption_source"][wing] = PHYSICAL
        self._touch()

    def record_generation(self, delta_kwh, wing, status):
        """Raw M1 delta always counted; attribution bucket from verified feedback at the interval start."""
        day = self.roll()
        day["generation_kwh"] = round((day["generation_kwh"] or 0.0) + float(delta_kwh), 6)
        day["generation_source"] = PHYSICAL
        if status == "ATTRIBUTED" and wing in WINGS:
            self._add(day["wing_generation"], wing, delta_kwh)
        elif status == "FAULT":
            day["fault_generation_kwh"] = round(day["fault_generation_kwh"] + float(delta_kwh), 6)
        else:
            day["unattributed_generation_kwh"] = round(day["unattributed_generation_kwh"] + float(delta_kwh), 6)
        self._touch()

    def record_gap(self, meter_id, skipped_kwh):
        day = self.roll()
        self._add(day["gap_kwh"], meter_id, skipped_kwh)
        self._touch()

    def record_event(self, event):
        day = self.roll()
        day["events"].append(event)
        day["events"] = day["events"][-MAX_DAY_EVENTS:]
        self._touch()

    # ---------------------------------------------------------------- sync
    def pending_days(self):
        """CLOSED days not yet acknowledged by the cloud (oldest first) — bounded."""
        return list(self.closed)

    def ack_synced(self, operating_dates):
        acked = set(operating_dates or ())
        before = len(self.closed)
        self.closed = [d for d in self.closed if d["operating_date"] not in acked]
        if len(self.closed) != before:
            self.dirty = True

    # ---------------------------------------------------------------- persistence
    def persist(self, force=False):
        if not self.dirty:
            return False
        now = self.now()
        if not force and now - self._last_persist < self.persist_interval_s:
            return False
        if not self.write_allowed():
            return False
        try:
            atomic_write_json(self.path, {"version": LEDGER_VERSION, "today": self.today, "closed": self.closed, "saved_at": now})
        except OSError:
            return False
        self.dirty = False
        self._last_persist = now
        return True

    def snapshot_today(self):
        return json.loads(json.dumps(self.roll()))


def energy_totals_consistent(day):
    """Invariant: raw M1 == sum(wing) + unattributed + fault (within rounding)."""
    if day.get("generation_kwh") is None:
        return True
    parts = sum(v or 0.0 for v in day["wing_generation"].values()) + day["unattributed_generation_kwh"] + day["fault_generation_kwh"]
    return abs(parts - day["generation_kwh"]) < 1e-4
