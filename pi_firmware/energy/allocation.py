"""Energy allocation POLICY (E3): sequential A -> B -> C -> D generation-target allocation.
Pure decision layer: it never touches GPIO, the command queue or the controller system state. The controller executes
its decisions through the existing local transition path (same gates/FSM/verification as physical toggles).
Policy states (per operating day):
  IDLE -> RUNNING(wing) -> WAITING_PERSISTENCE -> next wing ... -> FINALIZING (final OFF requested) -> COMPLETED
  BLOCKED  : a global safety condition holds (M1 stale/offline/disabled, feedback unknown, multiple contactors,
             attribution FAULT, system FAULT). No hardware action, persistence timer cleared. When the condition
             clears the policy RECONCILES against verified physical state before doing anything.
  PAUSED   : operator wins / failure; no automatic hardware action until the next operating day.
  COMPLETED: only after the final deactivation was executed AND verified_active == None."""
import time

from .energy_state import atomic_write_json, load_json

IDLE, RUNNING, WAITING_PERSISTENCE, FINALIZING, PAUSED, COMPLETED, BLOCKED = (
    "IDLE", "RUNNING", "WAITING_PERSISTENCE", "FINALIZING", "PAUSED", "COMPLETED", "BLOCKED")
ACTIVE_STATES = (IDLE, RUNNING, WAITING_PERSISTENCE, FINALIZING)
MAX_FINALIZE_ATTEMPTS = 3
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
                "persistence_started_at": None, "completed": [], "skipped": [], "reason": None, "reconciled": True,
                "resume_status": None, "finalize_attempts": 0}

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
            if st["status"] != BLOCKED:
                self._set(BLOCKED, block, resume_status=st["status"], persistence_started_at=None)
                events.append(("energy_allocation_blocked", f"no action: {block} (was {st['resume_status']} wing={st['current_wing']}); persistence timer cleared"))
            elif st.get("reason") != block:
                st["reason"] = block; self.dirty = True
                events.append(("energy_allocation_blocked", f"no action: {block} (still blocked, wing={st['current_wing']})"))
            return {"action": None, "slot": None, "events": events}
        va = ctx["verified_active"]
        if st["status"] == BLOCKED:
            # Safety condition cleared: reconcile against VERIFIED physical state before any decision. Never energize here.
            resume = st.get("resume_status") or IDLE
            if resume in (RUNNING, WAITING_PERSISTENCE, FINALIZING) and va != st["current_wing"]:
                self._set(PAUSED, "INTERVENTION_WHILE_BLOCKED", resume_status=None)
                events.append(("energy_allocation_paused", f"after block cleared verified active={va or 'NONE'} expected={st['current_wing']}; paused for the day"))
                return {"action": None, "slot": None, "events": events}
            self._set(RUNNING if resume == WAITING_PERSISTENCE else resume, None, resume_status=None, persistence_started_at=None)
            events.append(("energy_allocation_resumed", f"safety condition cleared; reconciled verified active={va or 'NONE'}; state={st['status']}"))
            return {"action": None, "slot": None, "events": events}  # recovery never acts; next evaluation decides with fresh data
        if st.get("reason"):
            st["reason"] = None; self.dirty = True
        # ---- reboot / boot reconciliation: never energize based on stale policy state
        if not st.get("reconciled"):
            st["reconciled"] = True
            if st["status"] in (RUNNING, WAITING_PERSISTENCE, FINALIZING) and va != st["current_wing"]:
                self._set(PAUSED, "RECONCILIATION_MISMATCH"); events.append(("energy_allocation_paused", f"after restart verified active={va} expected={st['current_wing']}; paused for the day"))
                return {"action": None, "slot": None, "events": events}
        # ---- FINALIZING: the final OFF was requested but not yet verified -> retry (bounded) or fail safe
        if st["status"] == FINALIZING:
            if va is None:
                return self._complete(ctx, events)
            if va != st["current_wing"]:
                self._set(PAUSED, "MANUAL_INTERVENTION"); events.append(("energy_allocation_paused", f"verified active wing={va} differs from finalizing {st['current_wing']}; operator wins"))
                return {"action": None, "slot": None, "events": events}
            if st["finalize_attempts"] >= MAX_FINALIZE_ATTEMPTS:
                self._set(PAUSED, "FINAL_DEACTIVATION_FAILED"); events.append(("energy_allocation_fault", f"wing {va} still verified ON after {st['finalize_attempts']} deactivation attempts; NOT completed; paused"))
                return {"action": None, "slot": None, "events": events}
            st["finalize_attempts"] += 1; self.dirty = True
            events.append(("energy_allocation_transition_requested", f"deactivate wing {va} (final OFF, attempt {st['finalize_attempts']})"))
            return {"action": "DEACTIVATE", "slot": va, "events": events}
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
        if previous is None or ctx["verified_active"] is None:
            return self._complete(ctx, events)  # nothing is verified ON: complete without any hardware action
        self._set(FINALIZING, None, finalize_attempts=1)
        events.append(("energy_allocation_transition_requested", f"deactivate wing {previous} (final OFF, attempt 1)"))
        return {"action": "DEACTIVATE", "slot": previous, "events": events}

    def _complete(self, ctx, events):
        self._set(COMPLETED, None, current_wing=None)
        events.append(("energy_allocation_completed", f"all eligible wings done for {ctx['operating_date']}: completed={self.state['completed']} skipped={[s['wing'] for s in self.state['skipped']]}; all wings verified OFF"))
        return {"action": None, "slot": None, "events": events}

    def after_execution(self, action, slot, success, verified_active_now):
        """Controller feedback after the local transition ran through GPIOManager + verification."""
        if action == "ACTIVATE":
            if success and verified_active_now == slot:
                return [("energy_allocation_transition_verified", f"wing {slot} verified active")]
            self._set(PAUSED, "TRANSITION_FAILED", current_wing=None)
            return [("energy_allocation_fault", f"activate {slot} failed or unverified (verified active={verified_active_now}); allocation paused for the day")]
        if action == "DEACTIVATE":
            if success and verified_active_now is None:
                events = [("energy_allocation_transition_verified", f"wing {slot} verified OFF")]
                if self.state["status"] == FINALIZING:
                    self._set(COMPLETED, None, current_wing=None)
                    events.append(("energy_allocation_completed", f"all eligible wings done for {self.state['operating_date']}: completed={self.state['completed']} skipped={[s['wing'] for s in self.state['skipped']]}; all wings verified OFF"))
                return events
            # Physical state is authoritative: still ON (or failed) => NOT completed. FINALIZING retries (bounded) via evaluate().
            if self.state["status"] == FINALIZING and self.state["finalize_attempts"] >= MAX_FINALIZE_ATTEMPTS:
                self._set(PAUSED, "FINAL_DEACTIVATION_FAILED")
            return [("energy_allocation_fault", f"deactivate {slot} failed or unverified (success={success}, verified active={verified_active_now}); NOT completed")]
        return []
