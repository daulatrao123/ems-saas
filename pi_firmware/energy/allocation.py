"""Energy allocation POLICY (E3): sequential A -> B -> C -> D generation-target allocation.
Pure decision layer: it never touches GPIO, the command queue or the controller system state. The controller executes
its decisions through the existing local transition path (same gates/FSM/verification as physical toggles).
Policy states: IDLE, RUNNING, WAITING_PERSISTENCE, PAUSED, COMPLETED, BLOCKED (per operating day)."""
import time

from .energy_state import atomic_write_json, load_json

IDLE, RUNNING, WAITING_PERSISTENCE, PAUSED, COMPLETED, BLOCKED = "IDLE", "RUNNING", "WAITING_PERSISTENCE", "PAUSED", "COMPLETED", "BLOCKED"
DEFAULT_SEQUENCE = ("A", "B", "C", "D")
M1_STALE_S = 60.0
EVALUATE_INTERVAL_S = 5.0


def default_config():
    return {"enabled": False, "sequence": list(DEFAULT_SEQUENCE), "tolerance_kwh": 1.0, "persistence_s": 300.0, "wings": {}}


def verified_active(feedback):
    """Exactly one verified ON -> wing; none -> None; several -> 'MULTIPLE'; any UNKNOWN with none ON -> 'UNKNOWN'."""
    on = [w for w in DEFAULT_SEQUENCE if str((feedback or {}).get(w, "UNKNOWN")).upper() == "ON"]
    if len(on) > 1:
        return "MULTIPLE"
    if len(on) == 1:
        return on[0]
    if any(str((feedback or {}).get(w, "UNKNOWN")).upper() not in ("ON", "OFF") for w in DEFAULT_SEQUENCE):
        return "UNKNOWN"
    return None


class AllocationPolicy:
    def __init__(self, path, write_allowed=lambda: True, now=time.time):
        self.path = path
        self.write_allowed = write_allowed
        self.now = now
        self.config = default_config()
        st = load_json(path, {})
        self.state = st if st.get("version") == 1 else self._fresh(None)
        self.state["reconciled"] = False  # after boot: verify physical state before any decision
        self.dirty = False

    # ---------------------------------------------------------------- state helpers
    def _fresh(self, operating_date):
        return {"version": 1, "operating_date": operating_date, "status": IDLE, "current_wing": None, "index": 0,
                "persistence_started_at": None, "completed": [], "skipped": [], "reason": None, "reconciled": True}

    def _set(self, status, reason=None, **kw):
        self.state.update({"status": status, "reason": reason, **kw})
        self.dirty = True

    def persist(self):
        if not self.dirty or not self.write_allowed():
            return False
        try:
            atomic_write_json(self.path, {k: v for k, v in self.state.items() if k != "reconciled"})
        except OSError:
            return False
        self.dirty = False
        return True

    def apply_config(self, cfg):
        merged = default_config()
        if isinstance(cfg, dict):
            merged["enabled"] = bool(cfg.get("enabled", False))
            seq = [str(w).upper() for w in (cfg.get("sequence") or DEFAULT_SEQUENCE)]
            merged["sequence"] = [w for w in seq if w in DEFAULT_SEQUENCE] or list(DEFAULT_SEQUENCE)
            merged["tolerance_kwh"] = max(0.0, float(cfg.get("tolerance_kwh", 1.0) or 0.0))
            merged["persistence_s"] = max(0.0, float(cfg.get("persistence_s", 300.0) or 0.0))
            merged["wings"] = cfg.get("wings") if isinstance(cfg.get("wings"), dict) else {}
        self.config = merged

    def view(self):
        return {k: v for k, v in self.state.items() if k != "version"} | {"enabled": self.config["enabled"]}

    # ---------------------------------------------------------------- eligibility
    def wing_target(self, wing, ctx):
        """Effective daily target or (None, reason). Consumption meter OFF -> only an explicit manual target counts."""
        wcfg = ctx["wings"].get(wing, {})
        acfg = self.config["wings"].get(wing, {}) if isinstance(self.config["wings"].get(wing), dict) else {}
        if not wcfg.get("ems_enabled", False):
            return None, "EMS_DISABLED"
        if not acfg.get("generation_attribution_enabled", True):
            return None, "ATTRIBUTION_DISABLED"
        manual = acfg.get("manual_target_kwh")
        if not wcfg.get("consumption_meter_enabled", False):
            if manual is None:
                return None, "CONSUMPTION_METER_OFF_NO_MANUAL_TARGET"
            return float(manual), None
        target = (ctx["targets"].get(wing) or {}).get("target_kwh_per_day")
        if target is None and manual is not None:
            target = manual
        if target is None or float(target) <= 0:
            return None, "NO_VALID_TARGET"
        return float(target), None

    def global_block(self, ctx):
        """Reason why NO hardware action may be taken right now (None = clear)."""
        m1 = ctx["m1"]
        if str(ctx.get("system_state")) == "FAULT":
            return "SYSTEM_FAULT"
        if not m1.get("enabled"):
            return "M1_DISABLED"
        if m1.get("comm_status") != "ONLINE" or m1.get("last_seen") is None or ctx["now"] - float(m1["last_seen"]) > M1_STALE_S:
            return "M1_STALE_OR_OFFLINE"
        va = ctx["verified_active"]
        if va == "MULTIPLE":
            return "MULTIPLE_CONTACTORS_ON"
        if va == "UNKNOWN":
            return "FEEDBACK_UNAVAILABLE"
        if ctx["attribution"].get("status") == "FAULT":
            return "ATTRIBUTION_FAULT"
        return None

    # ---------------------------------------------------------------- decision
    def evaluate(self, ctx):
        """-> {"action": None|"ACTIVATE"|"DEACTIVATE", "slot": w, "events": [(type, message), ...]}"""
        events = []
        st = self.state
        if st.get("operating_date") != ctx["operating_date"]:
            if st.get("status") not in (IDLE, None) and st.get("operating_date"):
                events.append(("energy_allocation_reset", f"new operating day {ctx['operating_date']} (was {st['operating_date']} {st['status']})"))
            self.state = self._fresh(ctx["operating_date"]); st = self.state; self.dirty = True
        if not self.config["enabled"]:
            if st["status"] in (RUNNING, WAITING_PERSISTENCE):
                self._set(PAUSED, "ALLOCATION_DISABLED"); events.append(("energy_allocation_paused", "allocation disabled by configuration; hardware left as is"))
            return {"action": None, "slot": None, "events": events}
        if st["status"] in (PAUSED, COMPLETED):
            return {"action": None, "slot": None, "events": events}
        block = self.global_block(ctx)
        if block:
            if st["status"] in (RUNNING, WAITING_PERSISTENCE):
                st["persistence_started_at"] = None  # never count time while data is untrusted
            if st.get("reason") != block:
                events.append(("energy_allocation_blocked", f"no action: {block} (state={st['status']} wing={st['current_wing']})"))
                st["reason"] = block; self.dirty = True
            return {"action": None, "slot": None, "events": events}
        if st.get("reason"):
            st["reason"] = None; self.dirty = True
        va = ctx["verified_active"]
        # ---- reboot / boot reconciliation: never energize based on stale policy state
        if not st.get("reconciled"):
            st["reconciled"] = True
            if st["status"] in (RUNNING, WAITING_PERSISTENCE) and va != st["current_wing"]:
                self._set(PAUSED, "RECONCILIATION_MISMATCH"); events.append(("energy_allocation_paused", f"after restart verified active={va} expected={st['current_wing']}; paused for the day"))
                return {"action": None, "slot": None, "events": events}
        # ---- manual intervention: the verified physical wing is not the allocator's
        if st["status"] in (RUNNING, WAITING_PERSISTENCE) and va != st["current_wing"]:
            self._set(PAUSED, "MANUAL_INTERVENTION"); events.append(("energy_allocation_paused", f"verified active wing={va or 'NONE'} differs from allocated {st['current_wing']}; operator wins for the rest of {ctx['operating_date']}"))
            return {"action": None, "slot": None, "events": events}
        if st["status"] == IDLE:
            if va is not None:
                self._set(PAUSED, "OPERATOR_ACTIVE_AT_START"); events.append(("energy_allocation_paused", f"wing {va} already active under manual control; allocation paused for {ctx['operating_date']}"))
                return {"action": None, "slot": None, "events": events}
            return self._advance(ctx, events, start=True)
        # ---- RUNNING / WAITING_PERSISTENCE on the allocated wing
        wing = st["current_wing"]
        target, _ = self.wing_target(wing, ctx)
        if target is None:
            self._set(PAUSED, "TARGET_LOST"); events.append(("energy_allocation_paused", f"wing {wing} lost its valid target during the day"))
            return {"action": None, "slot": None, "events": events}
        if ctx["attribution"].get("status") != "ATTRIBUTED" or ctx["attribution"].get("wing") != wing:
            st["persistence_started_at"] = None
            return {"action": None, "slot": None, "events": events}  # UNATTRIBUTED: wait, never guess
        generated = ctx["wing_generation"].get(wing)
        threshold = target + self.config["tolerance_kwh"]
        if generated is None or generated < threshold:
            if st["status"] == WAITING_PERSISTENCE:
                self._set(RUNNING, None, persistence_started_at=None); events.append(("energy_allocation_persistence_reset", f"wing {wing} {generated} kWh dropped below {threshold:.3f} kWh"))
            return {"action": None, "slot": None, "events": events}
        if st["persistence_started_at"] is None:
            self._set(WAITING_PERSISTENCE, None, persistence_started_at=ctx["now"]); events.append(("energy_allocation_threshold_met", f"wing {wing} {generated:.3f} >= {threshold:.3f} kWh; holding {self.config['persistence_s']:.0f}s"))
            return {"action": None, "slot": None, "events": events}
        if ctx["now"] - st["persistence_started_at"] < self.config["persistence_s"]:
            return {"action": None, "slot": None, "events": events}
        st["completed"].append(wing); st["index"] += 1; st["persistence_started_at"] = None; self.dirty = True
        events.append(("energy_allocation_target_reached", f"wing {wing} generated {generated:.3f} kWh >= target {target:.3f} + tolerance {self.config['tolerance_kwh']:.3f} for {self.config['persistence_s']:.0f}s"))
        return self._advance(ctx, events, start=False)

    def _advance(self, ctx, events, start):
        seq = self.config["sequence"]
        while self.state["index"] < len(seq):
            wing = seq[self.state["index"]]
            target, reason = self.wing_target(wing, ctx)
            if target is None:
                self.state["skipped"].append({"wing": wing, "reason": reason}); self.state["index"] += 1; self.dirty = True
                events.append(("energy_allocation_skipped", f"wing {wing} skipped: {reason}"))
                continue
            self._set(RUNNING, None, current_wing=wing, persistence_started_at=None)
            events.append(("energy_allocation_started" if start else "energy_allocation_transition_requested", f"activate wing {wing} target {target:.3f} kWh"))
            return {"action": "ACTIVATE", "slot": wing, "events": events}
        previous = self.state["current_wing"]
        self._set(COMPLETED, None, current_wing=None)
        events.append(("energy_allocation_completed", f"all eligible wings done for {ctx['operating_date']}: completed={self.state['completed']} skipped={[s['wing'] for s in self.state['skipped']]}; all wings OFF"))
        return {"action": "DEACTIVATE" if previous else None, "slot": previous, "events": events}

    def after_execution(self, action, slot, success, verified_active_now):
        """Controller feedback after the local transition ran through GPIOManager + verification."""
        if action == "ACTIVATE":
            if success and verified_active_now == slot:
                return [("energy_allocation_transition_verified", f"wing {slot} verified active")]
            self._set(PAUSED, "TRANSITION_FAILED", current_wing=None)
            return [("energy_allocation_fault", f"activate {slot} failed or unverified (verified active={verified_active_now}); allocation paused for the day")]
        if action == "DEACTIVATE" and not (success and verified_active_now is None):
            return [("energy_allocation_fault", f"deactivate {slot} failed or unverified (verified active={verified_active_now})")]
        return []
