"""HARDWARE_SOURCE_OF_TRUTH.md §16: all 42 legacy assertions preserved.

REAL GPIOManager + EMSController; built-in mock gpiozero, cloud and storage
environment are MOCKED. No Raspberry Pi/HIL. Run as a separate process:
    python qa/hw_source_of_truth_test.py

The endurance harness still owns its simulated persistence clock. ONLY gm.time
gets a real-time facade, so feedback, qualification and deadlines share real
elapsed time. No accelerated clock drivers. Every feedback worker is stopped
and joined before fault injection or device teardown.
"""
import os
import sys
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import phase05_sim_common as H  # noqa: E402
import config  # noqa: E402
import gpio_manager as gm  # noqa: E402
import ems_controller as ec  # noqa: E402
from state import VerificationState, FeedbackState, SystemState  # noqa: E402

GPIO_TIME = SimpleNamespace(monotonic=H.REAL_MONOTONIC, time=H.REAL_TIME,
                            perf_counter=time.perf_counter, sleep=time.sleep)


class Api:
    reply = None
    def sync(self, snap): return Api.reply
    def push_ack(self, *a, **k): return True
    def close(self): pass


R = []
def check(n, c, d=""):
    R.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + (f"  [{d}]" if d else ""), flush=True)


P = config.HARDWARE_PROFILES["EMS-4CH-v1"]
CH = P["channels"]


def cfg(fb):
    return {"hardware_profile": "EMS-4CH-v1", "feedback_hardware_installed": fb, "slots": {s: {"feedback_enabled": fb, "disabled": False} for s in "ABCD"}}


def detect(g, slot, on):
    g.feedback_inputs[slot]._set(on)  # contactor ON pulls detect LOW == pressed


@contextmanager
def mirror(g, slots="ABCD", lag=0.03):
    """Owned contactor worker; mechanical lag and poll cadence are real time."""
    stop, started = threading.Event(), threading.Event()
    errors = []

    def worker():
        started.set()
        try:
            while not stop.is_set():
                for s in slots:
                    want = g.relays[s].is_active
                    if bool(g.feedback_inputs[s].is_pressed) != want:
                        if stop.wait(lag):
                            return
                        detect(g, s, g.relays[s].is_active)
                if stop.wait(0.005):
                    return
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker, name="HW-Truth-Feedback", daemon=False)
    thread.start()
    try:
        assert started.wait(3), "feedback worker must start (test watchdog)"
        yield
    finally:
        stop.set()
        thread.join(3)
        assert not thread.is_alive(), "feedback worker must stop before fault injection/teardown"
        assert not errors, f"feedback worker failed: {errors}"


def stop_gpio(g):
    g.stop()
    assert g.monitor_thread is None or not g.monitor_thread.is_alive(), "GPIO monitor must stop"
    g._release_devices()


@contextmanager
def gpio_fixture(fb, reconcile=True):
    sm, st, q = H.build_firmware()
    # Persisted state must not enable the monitor before this fixture reconciles.
    st.system_state = SystemState.BOOT
    g = gm.GPIOManager(st, cfg(fb))
    try:
        if reconcile:
            assert g.reconcile_hardware_state() is True, "clean fixture must reconcile"
            assert st.system_state == SystemState.READY, "clean fixture must reach READY"
        yield g, st
    finally:
        try:
            stop_gpio(g)
        finally:
            q.close()
            sm.stop()


def map_and_inputs():
    # ---------------------------------------------------------------- §2 / §11 map
    check("all 4 channels exist (A B C D)", list(CH.keys()) == ["A", "B", "C", "D"] and P["slots"] == ["A", "B", "C", "D"])
    check("authoritative GPIO map", {s: (c["relay_gpio"], c["toggle_gpio"], c["detect_gpio"]) for s, c in CH.items()}
          == {"A": (17, 5, 19), "B": (27, 6, 16), "C": (23, 13, 20), "D": (22, 12, 21)})
    pins = [c[r] for c in CH.values() for r in ("relay_gpio", "toggle_gpio", "detect_gpio")]
    check("all 12 GPIOs are unique", len(pins) == 12 and len(set(pins)) == 12, str(sorted(pins)))
    check("polarity flags: relay ACTIVE-LOW, detect ACTIVE-LOW, toggle ACTIVE-HIGH", P["relay_active_low"] is True and P["toggle_active_low"] is False and P["detect_active_low"] is True)
    check("no legacy keys / no global polarity constant", "relay_gpio" not in P and "feedback_gpio" not in P and not hasattr(config, "FEEDBACK_ACTIVE_WHEN_PRESSED"))
    try:
        config.HARDWARE_PROFILES["DUP"] = {"channels": {"A": {"relay_gpio": 1, "toggle_gpio": 2, "detect_gpio": 3}, "B": {"relay_gpio": 3, "toggle_gpio": 4, "detect_gpio": 5}},
                                           "relay_active_low": True, "toggle_active_low": True, "detect_active_low": True}
        config._finalize_hardware_profiles(); dup_ok = False
    except RuntimeError as e:
        dup_ok = "GPIO3" in str(e)
    finally:
        del config.HARDWARE_PROFILES["DUP"]
    check("import-time guard rejects a GPIO used twice", dup_ok)

    # ---------------------------------------------------------------- §3 relays
    with gpio_fixture(False) as (g, st):
        check("relay active-low: OutputDevice(active_high=False) on every channel", all(r.active_high is False for r in g.relays.values()))
        check("relay startup OFF (initial_value=False -> pin HIGH)", all(not r.is_active and r.pin_level_high for r in g.relays.values()))
        check("relay pins wired per map", {s: r.pin for s, r in g.relays.items()} == {"A": 17, "B": 27, "C": 23, "D": 22})
        check("relay ON drives pin LOW", (g.relays["A"].on(), g.relays["A"].pin_level_high is False)[1]); g.relays["A"].off()

        # ---------------------------------------------------------------- §4 / §5 inputs
        check("toggle inputs: Button(pull_up=False [pull-down, HIGH=ON], bounce_time=0.05) on GPIO 5/6/13/12", {s: b.pin for s, b in g.toggles.items()} == {"A": 5, "B": 6, "C": 13, "D": 12}
              and all(b.pull_up is False and b.bounce_time == 0.05 for b in g.toggles.values()))
        check("detect inputs: Button(pull_up=True, bounce_time=0.05) on GPIO 19/16/20/21", {s: b.pin for s, b in g.feedback_inputs.items()} == {"A": 19, "B": 16, "C": 20, "D": 21}
              and all(b.pull_up is True and abs(b.bounce_time - 0.05) < 1e-9 for b in g.feedback_inputs.values()))
        check("toggle GPIO5 LOW reads OFF, GPIO5 HIGH reads ON (active-high)", g.toggle_inputs()["A"] is False and (g.toggles["A"]._set_level(True), g.toggle_inputs()["A"] is True)[1] and g.toggles["A"].level_high); g.toggles["A"]._set_level(False); g.pop_toggle_events()


def controller_toggles():
    # ---------------------------------------------------------------- §8 / §9 toggle events through the controller
    ctrl = ec.EMSController()
    try:
        ctrl.device_config.update(cfg(False)); ok = ctrl.boot()
        check("controller boots READY with all relays OFF", ok and ctrl.state.system_state == SystemState.READY and not any(r.is_active for r in ctrl.gpio.relays.values()))
        assert ok is True and ctrl.state.system_state == SystemState.READY, "clean controller must boot READY"
        def toggle(slot, on): ctrl.gpio.toggles[slot]._set_level(on)  # active-high: HIGH = ON
        def relays(): return {s: r.is_active for s, r in ctrl.gpio.relays.items()}
        def last_event(t="toggle"): ev = [e for e in ctrl._pending_events if e["type"] == t]; return ev[-1]["message"] if ev else ""
        for s in "ABCD":
            toggle(s, True); n = ctrl.process_toggle_events()
            check(f"toggle {s} event -> channel {s} ON via interlock FSM (n={n})", n == 1 and relays() == {x: x == s for x in "ABCD"} and ctrl.state.active_slot == s and f"TOGGLE {s} gpio={CH[s]['toggle_gpio']} edge=OFF->ON" in last_event() and "result=OK" in last_event(), str(relays()))
            if s != "D": toggle(s, False); ctrl.process_toggle_events()
        check("toggle A->B->C->D each performed break-before-make (only one relay ever ON)", relays() == {"A": False, "B": False, "C": False, "D": True})
        toggle("A", True); ctrl.process_toggle_events()
        check("D -> A transition via toggle while D active (break-before-make)", relays() == {"A": True, "B": False, "C": False, "D": False} and ctrl.state.active_slot == "A")
        toggle("B", False); n = ctrl.process_toggle_events()
        check("toggle OFF on a non-active channel is a silent no-op", n == 0 and relays()["A"] is True)
        toggle("A", False); n = ctrl.process_toggle_events()
        check("toggle OFF on the active channel -> channel OFF", n == 1 and not any(relays().values()) and ctrl.state.active_slot is None)
        # gpiozero applies bounce_time; the mock fires only on a state CHANGE.
        before = len(ctrl._pending_events); toggle("C", True); toggle("C", True); toggle("C", True); n = ctrl.process_toggle_events()
        check("toggle debounce: repeated identical levels = exactly one command", n == 1 and len(ctrl._pending_events) == before + 1 and relays()["C"])
        check("snapshot: top-level toggle_input bools, no per-slot duplicate, contactor UNKNOWN without feedback", ctrl._build_snapshot()["toggle_input"] == {"A": False, "B": False, "C": True, "D": True} and "toggle_input" not in ctrl._build_snapshot()["slots"]["C"] and ctrl._build_snapshot()["slots"]["C"]["physical_toggle"] == "UNKNOWN" and ctrl._build_snapshot()["hardware_fault"] is None)
        check("toggle event flows to cloud events[] stream", any(e["type"] == "toggle" for e in ctrl._build_snapshot()["events"]))
        ctrl.state.system_state = SystemState.CLOUD_OFFLINE; toggle("C", False); n = ctrl.process_toggle_events()
        check("toggles work while CLOUD_OFFLINE and restore that state", n == 1 and not relays()["C"] and ctrl.state.system_state == SystemState.CLOUD_OFFLINE)
        ctrl.state.system_state = SystemState.READY
        ctrl.state.system_state = SystemState.FAULT; toggle("B", True); n = ctrl.process_toggle_events()
        check("toggle while FAULT -> rejected, no relay change, toggle_rejected event", n == 0 and not any(relays().values()) and "reason=FAULT" in last_event("toggle_rejected") and "result=REJECTED" in last_event("toggle_rejected"))
    finally:
        ctrl.shutdown()
        stop_gpio(ctrl.gpio)


def feedback_and_interlock():
    # ---------------------------------------------------------------- §5 / §6 / §7 feedback + interlock
    with gpio_fixture(True) as (g, st), mirror(g):
        for s in "ABCD":
            ok = g.transition_slot(s)
            check(f"detect {s}: contactor pulls GPIO{CH[s]['detect_gpio']} LOW -> VERIFIED_ON", ok and st.slots[s].verification_state == VerificationState.VERIFIED_ON and st.slots[s].feedback_state == FeedbackState.ON, f"{ok} {st.system_state} {st.slots[s].verification_state}")
        check("only D relay ON after A->B->C->D with feedback (break-before-make, OFF confirmed each step)", {x: r.is_active for x, r in g.relays.items()} == {"A": False, "B": False, "C": False, "D": True} and st.system_state == SystemState.READY)
        ok = g.transition_slot("A")
        check("D -> A transition with feedback verified", ok and g.relays["A"].is_active and not g.relays["D"].is_active)
        order = []
        orig_on = g.relays["B"].on
        def spy_on(): order.append(("B_on", g.relays["A"].is_active, st.slots["A"].feedback_state)); orig_on()
        g.relays["B"].on = spy_on; ok = g.transition_slot("B")
        check("break-before-make enforced: B energised only after A relay OFF and A feedback OFF", ok and order and order[0][1] is False and order[0][2] == FeedbackState.OFF, str(order))

    # Welded contactor: establish verified ON, then stop/join the mirror before
    # holding A's detect ON. No duration-based worker expiration or guessed sleep.
    with gpio_fixture(True) as (g, st):
        with mirror(g, "A"):
            assert g.transition_slot("A") is True, "weld scenario must first activate A"
            assert st.slots["A"].verification_state == VerificationState.VERIFIED_ON
        detect(g, "A", True)
        ok = g.transition_slot("B")
        check("failed feedback OFF confirmation (A welded) -> transition refused, FAULT, B never energised", ok is False and st.system_state == SystemState.FAULT and not g.relays["B"].is_active)

    # Deliberately no mirror: contactor C never closes after a clean startup.
    with gpio_fixture(True) as (g, st):
        ok = g.transition_slot("C")
        check("failed feedback ON confirmation -> relay turned OFF + FAULT", ok is False and st.system_state == SystemState.FAULT and not g.relays["C"].is_active and st.slots["C"].verification_state == VerificationState.TIMEOUT)
        check("no automatic continuation after FAULT: transition/deactivate refused", g.transition_slot("A") is False and g.deactivate_slot("A") is False)

    # Deliberately inconsistent boot inputs: no clean-start reconciliation here.
    with gpio_fixture(False, reconcile=False) as (g, st):
        g.relays["A"].is_active = True; g.relays["B"].is_active = True
        check("multiple relay ON => FAULT", g.reconcile_hardware_state() is False and st.system_state == SystemState.FAULT)
    with gpio_fixture(True, reconcile=False) as (g, st):
        detect(g, "A", True); detect(g, "C", True)
        check("multiple detect ON => FAULT", g.reconcile_hardware_state() is False and st.system_state == SystemState.FAULT)
    with gpio_fixture(True, reconcile=False) as (g, st):
        detect(g, "B", True)
        check("relay OFF but detect ON (mismatch) => FAULT", g.reconcile_hardware_state() is False and st.system_state == SystemState.FAULT and st.slots["B"].verification_state == VerificationState.MISMATCH_OFF_ON)
    with gpio_fixture(False) as (g, st):
        g.transition_slot("A")
        check("feedback disabled => physical UNKNOWN, verification GPIO_CONFIRMED (never VERIFIED_ON)", st.slots["A"].feedback_state == FeedbackState.UNKNOWN and st.slots["A"].verification_state == VerificationState.GPIO_CONFIRMED and g.verify_slot("A", True) == VerificationState.NOT_CONFIGURED)

    # The real GPIO monitor remains enabled and owns its normal production cadence.
    with gpio_fixture(True) as (g, st):
        st.system_state = SystemState.CLOUD_OFFLINE; detect(g, "D", True)
        for _ in range(60):
            if st.system_state == SystemState.FAULT: break
            GPIO_TIME.sleep(0.1)
        check("safety monitor detects unexpected contactor ON while CLOUD_OFFLINE -> FAULT", st.system_state == SystemState.FAULT)


if __name__ == "__main__":
    print("GPIO/cloud/storage environment: MOCKED. GPIO timing: real. Raspberry Pi/HIL NOT performed.")
    with patch.object(gm, "time", GPIO_TIME), patch.object(ec, "ApiClient", Api), \
            patch.object(ec.EMSController, "_install_signal_handlers", lambda self: None):
        map_and_inputs()
        controller_toggles()
        feedback_and_interlock()
    assert len(R) == 42, "all 42 legacy assertions must execute"
    print(f"\n{sum(R)}/{len(R)} passed")
    sys.exit(0 if all(R) else 1)