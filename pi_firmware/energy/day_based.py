"""Calendar day allocation. It decides only; the controller executes through the existing transition path.

AllocationPolicy is not used here. A verified wing that this strategy did not apply pauses the
operating day instead of being forced back to the schedule.
"""
import json
import time
from datetime import date

from .energy_state import atomic_write_json, load_json
import device_obs

WINGS = ("A", "B", "C", "D")


def period_bounds(operating_date, reset_day):
    if isinstance(reset_day, bool):
        raise ValueError("reset day must be 1-28")
    try:
        reset_day = int(reset_day)
    except (TypeError, ValueError):
        raise ValueError("reset day must be 1-28")
    if not 1 <= reset_day <= 28:
        raise ValueError("reset day must be 1-28")
    if operating_date.day >= reset_day:
        start = date(operating_date.year, operating_date.month, reset_day)
        end = date(start.year + 1, 1, reset_day) if start.month == 12 else date(start.year, start.month + 1, reset_day)
    else:
        start = date(operating_date.year - 1, 12, reset_day) if operating_date.month == 1 else date(operating_date.year, operating_date.month - 1, reset_day)
        end = date(operating_date.year, operating_date.month, reset_day)
    return start, end


def cycle_day_index(operating_date, reset_day):
    start, _end = period_bounds(operating_date, reset_day)
    return (operating_date - start).days + 1


def scheduled_wing(days, index, enabled):
    cursor = 1
    for wing in WINGS:
        if wing not in enabled:
            continue
        count = int(days.get(wing, 0) or 0)
        if count <= 0:
            continue
        if cursor <= index < cursor + count:
            return wing
        cursor += count
    return None


def scheduled_usage(operating_date, reset_day, days, enabled):
    """Days started in this cycle for each enabled wing, including the current cycle day.

    No contactor reading is consulted. Disabled wings are omitted. An enabled wing
    with no positive target is 0. The current day counts as soon as it starts.
    """
    if isinstance(operating_date, str):
        operating_date = date.fromisoformat(operating_date)
    index = cycle_day_index(operating_date, reset_day)
    usage = {}
    cursor = 1
    for wing in WINGS:
        if wing not in enabled:
            continue
        count = int(days.get(wing, 0) or 0)
        if count <= 0:
            usage[wing] = 0
            continue
        start = cursor
        end = cursor + count - 1
        cursor += count
        if index < start:
            usage[wing] = 0
        elif index > end:
            usage[wing] = count
        else:
            usage[wing] = index - start + 1
    return usage


def _written_form(state):
    """Bytes-equivalent form of the dict atomic_write_json would store."""
    return json.dumps(state, separators=(",", ":"))


class DayBasedStrategy:
    def __init__(self, path, write_allowed=lambda: True):
        self.path = path
        self.write_allowed = write_allowed
        stored = load_json(path, {})
        self.state = stored if stored.get("version") == 1 else {
            "version": 1, "operating_date": None, "paused": False, "last_applied": None, "scheduled": None}
        self._persisted_snapshot = dict(self.state)
        self.dirty = False

    def _save_state(self, **updates):
        self.state.update(updates)
        self.dirty = True

    def persist(self):
        started = time.perf_counter()
        previous = dict(self._persisted_snapshot) if isinstance(self._persisted_snapshot, dict) else None
        scheduled = self.state.get("scheduled")
        changed = _written_form(self.state) != _written_form(self._persisted_snapshot)
        if self.dirty and not changed:
            self.dirty = False
        if not self.dirty or not self.write_allowed():
            device_obs.safe_observe("day_based", lambda: device_obs.note_persist(
                False, False, scheduled, self.path, time.perf_counter() - started, previous))
            return False
        try:
            atomic_write_json(self.path, self.state)
        except OSError:
            device_obs.safe_observe("day_based", lambda: device_obs.note_persist(
                False, False, scheduled, self.path, time.perf_counter() - started, previous))
            return False
        self.dirty = False
        self._persisted_snapshot = dict(self.state)
        device_obs.safe_observe("day_based", lambda: device_obs.note_persist(
            changed, True, scheduled, self.path, time.perf_counter() - started, previous))
        return True

    def evaluate(self, ctx):
        events = []
        operating_date = ctx["operating_date"]
        if self.state.get("operating_date") != operating_date:
            self._save_state(operating_date=operating_date, paused=False, scheduled=None)
        # Pause is a verified-feedback override. Without feedback hardware the calendar stays in charge.
        if self.state.get("paused") and ctx.get("feedback_hardware_installed", True) is not False:
            return {"action": None, "slot": None, "events": events}
        enabled, days = [], {}
        for wing in WINGS:
            cfg = ctx["wings"].get(wing, {})
            if not cfg.get("ems_enabled", False):
                continue
            enabled.append(wing)
            days[wing] = int(cfg.get("target_days") or 0)
        try:
            index = cycle_day_index(date.fromisoformat(operating_date), ctx["reset_day"])
        except (TypeError, ValueError):
            return {"action": None, "slot": None, "events": events}
        scheduled = scheduled_wing(days, index, enabled)
        self._save_state(scheduled=scheduled)
        if ctx.get("feedback_hardware_installed", True) is False:
            return self._command_from_calendar(scheduled, events)
        verified = ctx.get("verified_active")
        if verified in ("MULTIPLE", "UNKNOWN"):
            return {"action": None, "slot": None, "events": events}
        last = self.state.get("last_applied")
        if verified == scheduled or (verified is None and scheduled is None):
            return {"action": None, "slot": None, "events": events}
        if verified is None and scheduled:
            return {"action": "ACTIVATE", "slot": scheduled, "events": events}
        if verified is not None and verified == last and verified != scheduled:
            return {"action": "DEACTIVATE", "slot": verified, "events": events}
        self._save_state(paused=True)
        events.append(("energy_allocation_paused", f"DAY_BASED manual override verified={verified or 'NONE'} scheduled={scheduled or 'NONE'}"))
        return {"action": None, "slot": None, "events": events}

    def _command_from_calendar(self, scheduled, events):
        """No feedback hardware: follow the schedule through the existing ACTIVATE/DEACTIVATE actions.

        last_applied is the last successful command, not a contactor reading. A different
        scheduled wing deactivates the previous wing first; the next evaluation activates it.
        """
        last = self.state.get("last_applied")
        if last == scheduled:
            return {"action": None, "slot": None, "events": events}
        if last:
            return {"action": "DEACTIVATE", "slot": last, "events": events}
        if scheduled:
            return {"action": "ACTIVATE", "slot": scheduled, "events": events}
        return {"action": None, "slot": None, "events": events}

    def after_execution(self, action, slot, success, verified_now, feedback_hardware_installed=True):
        if feedback_hardware_installed is False:
            if action == "ACTIVATE" and success:
                self._save_state(last_applied=slot)
                return [("energy_allocation_transition_verified", f"day-based wing {slot} commanded")]
            if action == "DEACTIVATE" and success:
                self._save_state(last_applied=None)
                return [("energy_allocation_transition_verified", f"day-based wing {slot} commanded OFF")]
            return []
        if action == "ACTIVATE" and success and verified_now == slot:
            self._save_state(last_applied=slot)
            return [("energy_allocation_transition_verified", f"day-based wing {slot} verified")]
        if action == "DEACTIVATE" and success and verified_now is None:
            self._save_state(last_applied=None)
            return [("energy_allocation_transition_verified", f"day-based wing {slot} verified OFF")]
        return []
