"""Hardware GPIO contract. Retired toggle pins stay reserved and are not opened.

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

if not hasattr(os, "statvfs"):
    class _Vfs:
        f_bavail = 10**9
        f_blocks = 10**9
        f_frsize = 4096
        f_bfree = 10**9
        f_bsize = 4096
    os.statvfs = lambda _path: _Vfs()

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

        # ---------------------------------------------------------------- inputs: feedback is live; retired toggles are reserved and not claimed
        opened = {r.pin for r in g.relays.values()} | {b.pin for b in g.feedback_inputs.values()}
        reserved_toggles = {s: CH[s]["toggle_gpio"] for s in "ABCD"}
        check("GPIOManager does not open retired toggle pins", g.toggles == {} and g.toggle_inputs() == {s: None for s in "ABCD"})
        check("detect inputs: Button(pull_up=True, bounce_time=0.05) on GPIO 19/16/20/21", {s: b.pin for s, b in g.feedback_inputs.items()} == {"A": 19, "B": 16, "C": 20, "D": 21}
              and all(b.pull_up is True and abs(b.bounce_time - 0.05) < 1e-9 for b in g.feedback_inputs.values()))
        check("retired toggle pins stay reserved at BCM 5/6/13/12 and are not opened", reserved_toggles == {"A": 5, "B": 6, "C": 13, "D": 12}
              and set(reserved_toggles.values()).isdisjoint(opened))
        profile_pins = set(pins)
        protected = {0, 1, 2, 3, 7, 8, 9, 10, 11, 14, 15}
        check("LCD I2C BCM 2/3 and Pi ID/SPI/UART pins are not assigned or opened", {2, 3}.isdisjoint(profile_pins) and {2, 3}.isdisjoint(opened)
              and protected.isdisjoint(profile_pins) and opened == {17, 27, 23, 22, 19, 16, 20, 21})

    import gpio_input_diag as diag
    opened_by_diag = []

    class RecordingButton:
        def __init__(self, pin, pull_up=None, bounce_time=None):
            opened_by_diag.append(pin)
            self.is_pressed = False

        def close(self):
            return None

    report = diag.read_inputs(button_cls=RecordingButton, samples=1, interval_s=0)
    check("input diagnostic reports retired toggles and does not open them",
          opened_by_diag == [19, 16, 20, 21]
          and {s: r["gpio"] for s, r in report["toggles"].items()} == {"A": 5, "B": 6, "C": 13, "D": 12}
          and all(r["status"] == "RESERVED" and r["role"] == "RETIRED" and r["ownership"] == "NOT USED BY CONTROLLER" and r["opened"] is False
                  for r in report["toggles"].values())
          and {s: r["gpio"] for s, r in report["detects"].items()} == {"A": 19, "B": 16, "C": 20, "D": 21})


def controller_toggles():
    # Retired toggles must not become a second way to energize a relay.
    ctrl = ec.EMSController()
    try:
        ctrl.device_config.update(cfg(False)); ok = ctrl.boot()
        relays = {s: r.is_active for s, r in ctrl.gpio.relays.items()}
        check("controller boots READY with all relays OFF", ok and ctrl.state.system_state == SystemState.READY and relays == {s: False for s in "ABCD"})
        assert ok is True and ctrl.state.system_state == SystemState.READY, "clean controller must boot READY"
        check("booted controller opens relays 17/27/23/22 and feedback 19/16/20/21 only",
              {s: r.pin for s, r in ctrl.gpio.relays.items()} == {"A": 17, "B": 27, "C": 23, "D": 22}
              and {s: b.pin for s, b in ctrl.gpio.feedback_inputs.items()} == {"A": 19, "B": 16, "C": 20, "D": 21}
              and all(r.active_high is False for r in ctrl.gpio.relays.values())
              and all(b.pull_up is True for b in ctrl.gpio.feedback_inputs.values())
              and ctrl.gpio.toggles == {})
        n = ctrl.process_toggle_events()
        check("discarded toggle events do not energize a relay", n == 0 and {s: r.is_active for s, r in ctrl.gpio.relays.items()} == {s: False for s in "ABCD"} and ctrl.state.active_slot is None)
        snap = ctrl._build_snapshot()
        check("snapshot does not invent toggle or contactor state", snap["toggle_input"] == {s: None for s in "ABCD"}
              and all(snap["slots"][s]["physical_toggle"] == "UNKNOWN" for s in "ABCD") and snap["hardware_fault"] is None
              and not any(e["type"] in ("toggle", "toggle_rejected") for e in snap["events"]))
        ctrl.state.system_state = SystemState.FAULT
        n = ctrl.process_toggle_events()
        check("discarded toggle events do not clear FAULT or energize a relay", n == 0 and ctrl.state.system_state == SystemState.FAULT and not any(r.is_active for r in ctrl.gpio.relays.values()))
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


EXPECTED_CHECKS = 35


def run_hardware_contract():
    R.clear()
    print("GPIO/cloud/storage environment: MOCKED. GPIO timing: real. Raspberry Pi/HIL NOT performed.")
    with patch.object(gm, "time", GPIO_TIME), patch.object(ec, "ApiClient", Api), \
            patch.object(ec.EMSController, "_install_signal_handlers", lambda self: None):
        map_and_inputs()
        controller_toggles()
        feedback_and_interlock()
    return list(R)


def test_hardware_source_of_truth():
    results = run_hardware_contract()
    assert len(results) == EXPECTED_CHECKS, f"{len(results)} checks ran, contract requires {EXPECTED_CHECKS}"
    assert all(results), f"{sum(results)}/{len(results)} passed"


if __name__ == "__main__":
    results = run_hardware_contract()
    print(f"\n{sum(results)}/{len(results)} passed")
    if len(results) != EXPECTED_CHECKS:
        print(f"FAIL check count {len(results)} != {EXPECTED_CHECKS}")
        sys.exit(1)
    sys.exit(0 if results and all(results) else 1)