"""P3 safety: REAL GPIOManager/state methods; GPIO, persistence and clock are MOCKED.

The canonical port preserves the eleven legacy assertions and adds regression
cases for positive qualification and pre-MAKE revalidation. Each fixture owns
its state/clock/devices. No accelerated-clock/background-thread races or HIL.
Run: python qa/p3_feedback_hardening_test.py
"""
import heapq
import os
import sys
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("EMS_DEVICE_ID", "p3-feedback-qa")
os.environ.setdefault("EMS_API_KEY", "p3-mocked-no-network")
sys.path.insert(0, str(ROOT / "pi_firmware"))
import config
import gpio_manager as gm
from state import (CommandedState, FeedbackState, PiStateManager, SlotState,
                   SystemState, VerificationState)

R = []


def check(name, condition, detail=""):
    R.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + name + (f" [{detail}]" if detail else ""), flush=True)


class Clock:
    """One clock for both deadlines and scheduled external feedback changes."""
    def __init__(self):
        self.now = 1.0
        self.events = []
        self.serial = 0
        self.gap = 0.0

    def at(self, when, callback):
        self.serial += 1
        heapq.heappush(self.events, (when, self.serial, callback))

    def sleep(self, seconds):
        end = self.now + seconds + (self.gap if seconds < 0.01 else 0)
        while self.events and self.events[0][0] <= end:
            when, _, callback = heapq.heappop(self.events)
            self.now = when
            callback()
        self.now = end

    def monotonic(self):
        return self.now

    perf_counter = monotonic
    time = monotonic


class MemoryState(PiStateManager):
    """Real state mutation methods; durability is covered by existing T7 suites."""
    def __init__(self):
        self.slots = {s: SlotState(s) for s in "ABCD"}
        self.system_state = SystemState.BOOT
        self.active_slot = None

    def save_state(self, immediate=False):
        return True


class Monitor:
    """Explicitly isolated monitor; the monitor body is exercised separately."""
    def __init__(self, **kwargs):
        self.alive = False

    def start(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.alive = False


@contextmanager
def fixture(installed=True, enabled=True, real_clock=False, reconcile=True):
    clock = time if real_clock else Clock()
    state, outputs, events = MemoryState(), {}, []

    class Output:
        def __init__(self, pin, active_high=True, initial_value=False):
            self.pin, self.active_high, self.is_active = pin, active_high, initial_value
            self.on_action = self.off_action = None
            outputs[pin] = self

        def on(self):
            self.is_active = True
            events.append((clock.monotonic(), self.pin, True, sum(x.is_active for x in outputs.values())))
            if self.on_action:
                self.on_action()

        def off(self):
            self.is_active = False
            events.append((clock.monotonic(), self.pin, False, sum(x.is_active for x in outputs.values())))
            if self.off_action:
                self.off_action()

        def close(self):
            pass

    class Input:
        def __init__(self, pin, pull_up=False, bounce_time=None):
            self.pin, self.pull_up, self.bounce_time = pin, pull_up, bounce_time
            self.signal = lambda: False
            self.when_pressed = self.when_released = None

        @property
        def is_pressed(self):
            return self.signal()

        def close(self):
            pass

    cfg = {"hardware_profile": "EMS-4CH-v1", "feedback_hardware_installed": installed,
           "slots": {s: {"feedback_enabled": installed and enabled} for s in "ABCD"}}
    isolated_threads = SimpleNamespace(Thread=Monitor, RLock=threading.RLock, Lock=threading.Lock)
    with patch.object(gm, "OutputDevice", Output), patch.object(gm, "Button", Input), \
            patch.object(gm, "threading", isolated_threads), patch.object(gm, "time", clock):
        g = gm.GPIOManager(state, cfg)
        try:
            if reconcile:
                ok = g.reconcile_hardware_state()
                if real_clock:
                    check("REAL-CLOCK clean startup: reconciliation succeeds and READY",
                          ok is True and state.system_state == SystemState.READY)
                assert ok is True, "clean fixture must reconcile"
                assert state.system_state == SystemState.READY, "clean fixture must reach READY"
                state.system_state = SystemState.EXECUTING  # Same command gate as EMSController.
            yield g, state, clock, events
        finally:
            g.stop()
            g._release_devices()
            assert not g.monitor_thread.is_alive(), "fixture monitor must be torn down"


def level(g, slot, value):
    g.feedback_inputs[slot].signal = lambda: value


def follows(g, clock, slot, delay=0.05):
    def schedule(on):
        clock.at(clock.now + delay, lambda: level(g, slot, on))
    g.relays[slot].on_action = lambda: schedule(True)
    g.relays[slot].off_action = lambda: schedule(False)


def pulse(g, clock, slot, initially_on=True):
    start = clock.monotonic()
    g.feedback_inputs[slot].signal = lambda: bool(int((clock.monotonic() - start + 1e-9) / 0.005) % 2) != initially_on


def legacy_assertions():
    check("GPIO contract (HARDWARE_SOURCE_OF_TRUTH): relays A/B/C/D = 17/27/23/22, detect = 19/16/20/21, toggles = 5/6/13/12",
          {s: (c["relay_gpio"], c["toggle_gpio"], c["detect_gpio"]) for s, c in config.HARDWARE_PROFILES["EMS-4CH-v1"]["channels"].items()}
          == {"A": (17, 5, 19), "B": (27, 6, 16), "C": (23, 13, 20), "D": (22, 12, 21)})
    with fixture(False, True) as (g, st, _, _):
        check("HW OFF: transition -> GPIO_CONFIRMED (relay read-back), not VERIFIED_ON", g.transition_slot("A") is True and st.slots["A"].verification_state == VerificationState.GPIO_CONFIRMED.value and g.relays["A"].is_active)
        check("HW OFF: verify_slot reports NOT_CONFIGURED", g.verify_slot("A", True) == VerificationState.NOT_CONFIGURED)
    with fixture(True, False) as (g, st, _, _):
        check("HW ON + slot feedback OFF: effective feedback disabled -> GPIO_CONFIRMED", g.transition_slot("B") is True and st.slots["B"].verification_state == VerificationState.GPIO_CONFIRMED.value)
    with fixture() as (g, st, clock, _):
        follows(g, clock, "A")
        check("FB ON: contactor closes -> VERIFIED_ON, command succeeds", g.transition_slot("A") is True and st.slots["A"].verification_state == VerificationState.VERIFIED_ON.value)
        check("FB ON: deactivate with contactor opening -> VERIFIED_OFF", g.deactivate_slot("A") is True and st.slots["A"].verification_state == VerificationState.VERIFIED_OFF.value and not g.relays["A"].is_active)
    with fixture() as (g, st, _, _):
        ok = g.transition_slot("C")
        check("FB ON: no feedback within FEEDBACK_TIMEOUT -> command FAILS (never completed), verification is a mismatch/pending, not VERIFIED_ON", ok is False and st.slots["C"].verification_state in (VerificationState.MISMATCH_ON_OFF.value, VerificationState.PENDING.value, VerificationState.TIMEOUT.value, VerificationState.TIMEOUT), str(st.slots["C"].verification_state))
        check("FB ON: failed activation leaves relay C de-energised (fail-safe)", not g.relays["C"].is_active)
    with fixture() as (g, st, clock, _):
        follows(g, clock, "D")
        okon = g.transition_slot("D")
        level(g, "D", True)
        g.relays["D"].off_action = None
        okd = g.deactivate_slot("D")
        check("FB ON: deactivate but contactor stays closed -> MISMATCH_OFF_ON/TIMEOUT, command FAILS (never VERIFIED_OFF)", okon is True and okd is False and str(st.slots["D"].verification_state).split(".")[-1] in ("MISMATCH_OFF_ON", "TIMEOUT"), f"{okd} {st.slots['D'].verification_state}")
    with fixture() as (g, st, clock, _):
        pulse(g, clock, "B")
        ok = g.transition_slot("B")
        check("FB ON: noisy/flickering feedback (5 ms pulses < 50 ms debounce) never yields VERIFIED_ON", ok is False and st.slots["B"].verification_state != VerificationState.VERIFIED_ON.value, st.slots["B"].verification_state)
    check("Backend positive set excludes NOT_CONFIGURED/PENDING/MISMATCH_* (only VERIFIED_ON/OFF/GPIO_CONFIRMED complete hardware commands)", (ROOT / "backend/main.py").read_text().count('POSITIVE_VERIFICATION = {"VERIFIED_ON", "VERIFIED_OFF", "GPIO_CONFIRMED"}') == 1)


def positive_qualification():
    for expected in (CommandedState.OFF, CommandedState.ON):
        for first, second in ((False, False), (False, True), (True, False), (True, True)):
            with fixture() as (g, st, clock, _):
                level(g, "A", first)
                clock.at(clock.now + 0.025, lambda: level(g, "A", second))
                result = g.verify_slot("A", expected)
                wanted = (VerificationState.VERIFIED_ON if first else VerificationState.VERIFIED_OFF) if first == second else VerificationState.PENDING
                if first == second and first != (expected == CommandedState.ON):
                    wanted = VerificationState.MISMATCH_ON_OFF if expected == CommandedState.ON else VerificationState.MISMATCH_OFF_ON
                check(f"QUALIFY {expected.value}: {int(first)}->{int(second)} => {wanted.value}", result == wanted, result.value)
                if first != second:
                    check("changing feedback is explicitly PENDING, not OFF/ON", st.slots["A"].feedback_state == FeedbackState.PENDING)
    for initially_on in (False, True):
        for expected in (CommandedState.OFF, CommandedState.ON):
            with fixture() as (g, _, clock, _):
                pulse(g, clock, "A", initially_on)
                result = g.verify_slot("A", expected)
                check(f"NOISE start={int(initially_on)} expected={expected.value}: never positive", result not in (VerificationState.VERIFIED_OFF, VerificationState.VERIFIED_ON), result.value)
    with fixture() as (g, st, clock, _):
        level(g, "A", None)
        check("unavailable input is PENDING, never false OFF", g.verify_slot("A", CommandedState.OFF) == VerificationState.PENDING and st.slots["A"].feedback_state == FeedbackState.PENDING)
    with fixture() as (g, st, clock, _):
        clock.gap = 0.020
        check("scheduler observation gap cannot qualify stable feedback", g.verify_slot("A", CommandedState.OFF) == VerificationState.PENDING)
        check("scheduler starvation times out safely without false ON", not g.transition_slot("C") and not g.relays["C"].is_active and st.system_state == SystemState.FAULT)


def transition_cases():
    with fixture() as (g, st, clock, events):
        follows(g, clock, "A", delay=0.2)
        start = clock.now
        check("delayed ON is not accepted until qualified", g.transition_slot("A") and clock.now - start >= 0.25)
        start = clock.now
        check("delayed OFF waits for positive qualification", g.deactivate_slot("A") and clock.now - start >= 0.25)
    with fixture() as (g, st, clock, _):
        follows(g, clock, "A", delay=config.FEEDBACK_TIMEOUT_MS / 1000 + 0.1)
        check("feedback arriving after the unchanged deadline fails safe", not g.transition_slot("A") and st.system_state == SystemState.FAULT and not g.relays["A"].is_active)
    with fixture() as (g, st, clock, events):
        follows(g, clock, "A", 0)
        follows(g, clock, "B", 0)
        assert g.transition_slot("A")
        g.relays["A"].off_action = lambda: level(g, "A", False)
        lock_owned = []
        follow_b = g.relays["B"].on_action
        g.relays["B"].on_action = lambda: (lock_owned.append(g._lock._is_owned()), follow_b())
        check("normal break-before-make transition succeeds", g.transition_slot("B"))
        check("MAKE remains inside the existing transition lock", lock_owned == [True])
        a_off = next(t for t, pin, on, _ in events if pin == 17 and not on)
        b_on = next(t for t, pin, on, _ in events if pin == 27 and on)
        check("break-before-make includes deadtime and fresh qualification", b_on - a_off >= (config.INTERLOCK_DELAY_MS + 2 * config.FEEDBACK_DEBOUNCE_MS) / 1000 - 1e-9)
        check("at most one relay output ON across every observed write", max(n for _, _, _, n in events) <= 1)
    for mode in ("stuck", "reasserted", "unstable"):
        with fixture() as (g, st, clock, events):
            follows(g, clock, "A", 0)
            follows(g, clock, "B", 0)
            assert g.transition_slot("A")
            def opening():
                if mode == "stuck":
                    return
                level(g, "A", False)
                # OFF qualifies at 50ms; feedback changes near the end of the 500ms deadtime.
                at = clock.now + (config.FEEDBACK_DEBOUNCE_MS + config.INTERLOCK_DELAY_MS) / 1000 - 0.01
                clock.at(at, lambda: level(g, "A", True) if mode == "reasserted" else pulse(g, clock, "A"))
            g.relays["A"].off_action = opening
            ok = g.transition_slot("B")
            check(f"PRE-MAKE {mode}: fails into FAULT", not ok and st.system_state == SystemState.FAULT)
            check(f"PRE-MAKE {mode}: target never energized", not g.relays["B"].is_active and not any(pin == 27 and on for _, pin, on, _ in events))
    with fixture() as (g, st, clock, _):
        pulse(g, clock, "A")
        check("reconciliation cannot map unstable feedback to OFF", not g.reconcile_hardware_state() and st.system_state == SystemState.FAULT and st.slots["A"].feedback_state == FeedbackState.PENDING)
    with fixture(False) as (g, st, _, _):
        check("disabled feedback stays UNKNOWN / NOT_CONFIGURED", st.slots["A"].feedback_state == FeedbackState.UNKNOWN and g.verify_slot("A", CommandedState.OFF) == VerificationState.NOT_CONFIGURED)
    with fixture() as (g, st, clock, _):
        st.system_state = SystemState.READY
        pulse(g, clock, "A")
        sleep = clock.sleep
        def monitor_step(seconds):
            if seconds == config.LOCAL_MONITOR_INTERVAL_S:
                g._running = False
            sleep(seconds)
        clock.sleep = monitor_step
        g._local_monitor_loop()
        check("monitor never retains a positive state for unstable feedback", st.system_state == SystemState.FAULT and st.slots["A"].verification_state == VerificationState.PENDING)


def reconciliation_cases():
    positive = (VerificationState.VERIFIED_ON, VerificationState.VERIFIED_OFF)
    limit = config.FEEDBACK_TIMEOUT_MS / 1000.0
    with fixture(reconcile=False) as (g, st, clock, events):
        with ExitStack() as forbidden:
            for relay in g.relays.values():
                for action in ("on", "off"):
                    forbidden.enter_context(patch.object(relay, action, side_effect=AssertionError("reconciliation must not actuate")))
            ok = g.reconcile_hardware_state()
        check("RECON A/J: constant OFF qualifies without any relay on/off call", ok and st.system_state == SystemState.READY and not events and all(st.slots[s].verification_state == VerificationState.VERIFIED_OFF for s in "ABCD"))

    with fixture(reconcile=False) as (g, st, clock, events):
        for s in "ABCD":
            st.slots[s].verification_state = VerificationState.VERIFIED_ON
            st.slots[s].feedback_state = FeedbackState.ON
        pending, owned = [], []
        def observe():
            pending.append(st.system_state == SystemState.HARDWARE_RECONCILIATION and all(st.slots[s].verification_state == VerificationState.PENDING and st.slots[s].feedback_state == FeedbackState.PENDING for s in "ABCD"))
            owned.append(g._lock._is_owned())
            return False
        for s in "ABCD":
            g.feedback_inputs[s].signal = observe
        clock.gap = 0.020
        clock.at(clock.now + 0.005, lambda: setattr(clock, "gap", 0))
        start = clock.now
        ok = g.reconcile_hardware_state()
        check("RECON B: transient 20ms observation gap recovers with fresh evidence", ok and 0.220 <= clock.now - start < limit)
        check("RECON B: no stale VERIFIED or partial feedback publication while pending", bool(pending) and all(pending))
        check("RECON B: all reads hold the existing GPIO RLock and never actuate", all(owned) and not events)

    with fixture(reconcile=False) as (g, st, clock, events):
        clock.gap = 0.020
        start = clock.now
        ok = g.reconcile_hardware_state()
        elapsed = clock.now - start
        # A modelled scheduler overrun can delay return, never restart the budget.
        check("RECON C: sustained starvation exhausts ONE overall 2000ms budget", not ok and st.system_state == SystemState.FAULT and limit <= elapsed <= limit + 0.022, f"{elapsed:.6f}s")
        check("RECON C/N: timeout has no positive verification or subsequent MAKE", all(st.slots[s].verification_state == VerificationState.PENDING for s in "ABCD") and not g.transition_slot("B") and not events and not any(r.is_active for r in g.relays.values()))

    for period, label in ((0.025, "mixed"), (0.005, "noise")):
        for phase in (False, True):
            with fixture(reconcile=False) as (g, st, clock, events):
                start = clock.now
                g.feedback_inputs["A"].signal = lambda: bool(int((clock.now - start + 1e-9) / period) % 2) != phase
                ok = g.reconcile_hardware_state()
                check(f"RECON D/E: repeated {label} phase={int(phase)} cannot verify", not ok and st.system_state == SystemState.FAULT and all(st.slots[s].verification_state not in positive for s in "ABCD") and not events)

    for scenario in ("agreement", "mismatch", "multiple-relays", "multiple-contactors"):
        with fixture(reconcile=False) as (g, st, clock, events):
            if scenario != "multiple-contactors":
                g.relays["A"].on()
            if scenario in ("agreement", "multiple-relays", "multiple-contactors"):
                level(g, "A", True)
            if scenario == "multiple-relays":
                g.relays["B"].on()
            if scenario in ("multiple-relays", "multiple-contactors"):
                level(g, "B", True)
            before = list(events)
            ok = g.reconcile_hardware_state()
            if scenario == "agreement":
                valid = ok and st.system_state == SystemState.READY and st.active_slot == "A" and st.slots["A"].verification_state == VerificationState.VERIFIED_ON
            else:
                valid = not ok and st.system_state == SystemState.FAULT and all(st.slots[s].verification_state not in positive for s in "ABCD")
            check(f"RECON F/G/H/I: {scenario}, no GPIO writes", valid and events == before)

    with fixture(reconcile=False) as (g, st, clock, events):
        first_b = True
        def disrupt_b():
            nonlocal first_b
            if first_b:
                first_b = False
                clock.gap = 0.020
                clock.at(clock.now + 0.005, lambda: setattr(clock, "gap", 0))
                clock.at(clock.now + 0.010, lambda: level(g, "A", True))
            return False
        g.feedback_inputs["B"].signal = disrupt_b
        check("RECON: discard earlier A=OFF after incomplete B, never reuse stale evidence", not g.reconcile_hardware_state() and st.system_state == SystemState.FAULT and st.slots["A"].verification_state == VerificationState.MISMATCH_OFF_ON and not events)

    with fixture(reconcile=False) as (g, st, clock, events):
        clock.at(clock.now + 0.075, lambda: level(g, "A", True))
        check("RECON: veto A feedback reasserted while later channels were sampled", not g.reconcile_hardware_state() and st.system_state == SystemState.FAULT and st.slots["A"].verification_state == VerificationState.MISMATCH_OFF_ON and not events)

    with fixture(reconcile=False) as (g, st, clock, events):
        # Every complete read is stable, but the bank cannot fit before this
        # same deadline; no channel gets its own fresh 2-second allowance.
        clock.at(clock.now + limit - 0.025, lambda: setattr(clock, "gap", 0))
        clock.gap = 0.020
        start = clock.now
        check("RECON: recovery too late for a full fresh pass must not report READY", not g.reconcile_hardware_state() and st.system_state == SystemState.FAULT and clock.now - start <= limit + 0.022 and all(st.slots[s].verification_state not in positive for s in "ABCD") and not events)


def reconciliation_lock_case():
    with fixture(reconcile=False) as (g, st, clock, events):
        entered, release, competing, completed = (threading.Event() for _ in range(4))
        results, owned = {}, []
        once = True
        def feedback():
            nonlocal once
            if once:
                once = False
                owned.append(g._lock._is_owned())
                entered.set()
                assert release.wait(5), "test barrier watchdog, not a firmware timeout"
            return False
        g.feedback_inputs["A"].signal = feedback
        def reconcile():
            results["reconcile"] = g.reconcile_hardware_state()
        def transition():
            competing.set()
            results["transition"] = g.transition_slot("B")
            completed.set()
        reader = threading.Thread(target=reconcile, daemon=True)
        contender = threading.Thread(target=transition, daemon=True)
        reader.start()
        try:
            assert entered.wait(5), "reconciliation thread must reach the barrier"
            acquired = g._lock.acquire(blocking=False)
            if acquired:
                g._lock.release()
            contender.start()
            assert competing.wait(5), "transition contender must start"
            check("RECON K: another thread cannot acquire GPIO lock or MAKE during qualification", not acquired and owned == [True] and not completed.is_set() and not events)
            clock.gap = 0.020  # Reconciliation must fail before the contender can run.
        finally:
            release.set()
            reader.join(5)
            if contender.ident is not None:
                contender.join(5)
        check("RECON K/N: queued transition refuses MAKE after reconciliation failure", not reader.is_alive() and not contender.is_alive() and results == {"reconcile": False, "transition": False} and st.system_state == SystemState.FAULT and not events and not any(r.is_active for r in g.relays.values()))


def real_clock_noise():
    # Poll a time-defined input waveform, NOT a Python thread pretending a 5ms
    # sleep always schedules on time. Descheduling must fail closed, not alias.
    with fixture(real_clock=True) as (g, _, clock, _):
        pulse(g, clock, "A")
        for expected in (CommandedState.OFF, CommandedState.ON):
            results = [g.verify_slot("A", expected) for _ in range(20)]
            check(f"REAL-CLOCK noise expected={expected.value}: 20/20 non-positive", all(r not in (VerificationState.VERIFIED_OFF, VerificationState.VERIFIED_ON) for r in results))


if __name__ == "__main__":
    print("GPIO / persistence / deterministic clock: MOCKED. Raspberry Pi/HIL NOT performed.")
    legacy_assertions()
    positive_qualification()
    transition_cases()
    reconciliation_cases()
    reconciliation_lock_case()
    real_clock_noise()
    print(f"\n{sum(R)}/{len(R)} passed")
    sys.exit(0 if all(R) else 1)