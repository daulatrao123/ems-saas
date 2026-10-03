"""Fail-safe contactor feedback. Mocked GPIO only. No live coil."""
import os
import sys
from datetime import datetime, timedelta, timezone
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
import hw_source_of_truth_test as hw  # noqa: E402
import gpio_manager as gm  # noqa: E402
import lcd_display  # noqa: E402
from energy.allocation import verified_active  # noqa: E402
from state import FeedbackState, SystemState, VerificationState  # noqa: E402

GPIO_TIME = hw.GPIO_TIME
R = []


def check(name, condition, detail=""):
    R.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def pins_and_polarity():
    ch = hw.CH
    check("relay pins unchanged", {s: c["relay_gpio"] for s, c in ch.items()} == {"A": 17, "B": 27, "C": 23, "D": 22})
    check("feedback pins unchanged", {s: c["detect_gpio"] for s, c in ch.items()} == {"A": 19, "B": 16, "C": 20, "D": 21})
    reserved = {2, 3, 5, 6, 12, 13}
    used = {c[role] for c in ch.values() for role in ("relay_gpio", "toggle_gpio", "detect_gpio")}
    check("LCD and retired toggle pins are not reused as relays or feedback", reserved.isdisjoint({c["relay_gpio"] for c in ch.values()} | {c["detect_gpio"] for c in ch.values()}) and {5, 6, 12, 13} <= used)
    check("twelve assigned GPIOs stay unique", len(used) == 12)
    with hw.gpio_fixture(True, reconcile=False) as (g, st):
        check("detect pull-up active-low", all(b.pull_up is True for b in g.feedback_inputs.values()))
        hw.detect(g, "A", True)
        check("LOW feedback reads ON", g.feedback_inputs["A"].is_pressed is True and g.feedback_inputs["A"].level_high is False)
        hw.detect(g, "A", False)
        check("HIGH feedback reads OFF", g.feedback_inputs["A"].is_pressed is False and g.feedback_inputs["A"].level_high is True)


def verification_and_multiple():
    with hw.gpio_fixture(False) as (g, st):
        ok = g.transition_slot("A")
        check("feedback not installed never verifies ON", ok and st.slots["A"].verification_state == VerificationState.GPIO_CONFIRMED and st.slots["A"].feedback_state == FeedbackState.UNKNOWN)
    with hw.gpio_fixture(True) as (g, st):
        hw.detect(g, "A", False)
        turned = {"on": 0}
        orig = g.relays["B"].on
        def spy():
            turned["on"] += 1
            orig()
        g.relays["B"].on = spy
        g.state_manager.system_state = SystemState.FAULT
        check("FAULT blocks MAKE", g.transition_slot("B") is False and turned["on"] == 0 and not g.relays["B"].is_active)
    with hw.gpio_fixture(True, reconcile=False) as (g, st):
        hw.detect(g, "A", True)
        hw.detect(g, "B", True)
        check("two feedbacks ON fault and de-energize", g.reconcile_hardware_state() is False and st.system_state == SystemState.FAULT and not any(r.is_active for r in g.relays.values()))
        check("both physical feedbacks stay ON for MULTIPLE", st.slots["A"].feedback_state == FeedbackState.ON and st.slots["B"].feedback_state == FeedbackState.ON)
        check("verified_active is MULTIPLE", verified_active({s: st.slots[s].feedback_state.value for s in "ABCD"}) == "MULTIPLE")
        check("no MAKE while MULTIPLE fault holds", g.transition_slot("C") is False and not g.relays["C"].is_active)


def unstable_and_init():
    with hw.gpio_fixture(True) as (g, st):
        btn = g.feedback_inputs["A"]
        btn._state = True
        orig = gm.time.sleep
        def flip(_dt):
            btn._state = False
        gm.time.sleep = flip
        try:
            check("changing feedback is not ON", g._read_feedback_raw("A") is None)
        finally:
            gm.time.sleep = orig
        btn._state = None
        st.system_state = SystemState.READY
        st.slots["A"].commanded_state = st.slots["A"].commanded_state.__class__.ON
        g.relays["A"].on()
        result = g.verify_slot("A", st.slots["A"].commanded_state)
        check("unreadable feedback stays PENDING and is not treated as ON", result == VerificationState.PENDING)
        # Monitor path de-energizes. Drive one monitor decision directly.
        g.relays["A"].on()
        st.system_state = SystemState.READY
        from state import CommandedState
        st.set_commanded("A", CommandedState.ON)
        btn._state = None
        g._local_monitor_loop.__func__ if False else None
        # One locked pass of the monitor decision.
        with g._lock:
            st.system_state = SystemState.READY
            is_on = g._read_feedback_raw("A")
        check("unreadable sample stays unqualified", is_on is None)
        g.relays["A"].on()
        with patch.object(gm, "LOCAL_MONITOR_INTERVAL_S", 0.01):
            st.system_state = SystemState.READY
            g._running = True
            import threading
            t = threading.Thread(target=g._local_monitor_loop, daemon=True)
            t.start()
            for _ in range(50):
                if st.system_state == SystemState.FAULT:
                    break
                GPIO_TIME.sleep(0.02)
            g._running = False
            t.join(2)
        check("unreadable feedback faults and leaves relay OFF", st.system_state == SystemState.FAULT and not g.relays["A"].is_active)

    created = []
    class Boom(gm.Button):
        def __init__(self, *args, **kwargs):
            raise RuntimeError("gpiochip unavailable")
    class Tracking(gm.OutputDevice):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)
        def on(self):
            raise AssertionError("relay energized during failed init")
    sm, st, q = hw.H.build_firmware()
    try:
        with patch.object(gm, "Button", Boom), patch.object(gm, "OutputDevice", Tracking):
            g = gm.GPIOManager(st, hw.cfg(True))
        check("init failure is GPIO_INIT_FAILED and every created relay stayed OFF", str(g.hardware_fault).startswith("GPIO_INIT_FAILED") and created and all(not r.is_active for r in created))
    finally:
        q.close()
        sm.stop()


def cloud_and_lcd():
    import ast
    tree = ast.parse((os.path.join(os.path.dirname(__file__), "..", "backend", "main.py") and open(os.path.join(os.path.dirname(__file__), "..", "backend", "main.py"), encoding="utf-8").read()))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "normalize_physical_toggle")
    ns = {}
    exec(compile(ast.Module([fn], type_ignores=[]), "main.py", "exec"), ns)
    normalize = ns["normalize_physical_toggle"]
    check("physical_toggle rejects MULTIPLE and commanded words", normalize("MULTIPLE") == "UNKNOWN" and normalize("ON") == "ON" and normalize(False) == "OFF" and normalize(None) == "UNKNOWN" and normalize("B") == "UNKNOWN")
    view = {"active_slot": "B", "slots": {"A": {"disabled": False, "used_days": 0, "target_days": 1, "contactor": "OFF"}, "B": {"disabled": False, "used_days": 0, "target_days": 1, "contactor": "UNKNOWN"}}}
    lines = lcd_display.render_wings(view, ("A", "B"))
    check("LCD CT line is physical feedback, not the commanded wing", "CT A:OFF" in lines[2] and "B:UNKNOWN" in lines[2] and "B ON" in lines[1])


def command_during_fault():
    import ems_controller as ec
    with patch.object(ec, "ApiClient", hw.Api), patch.object(ec.EMSController, "_install_signal_handlers", lambda self: None), patch.object(gm, "time", GPIO_TIME):
        ctrl = ec.EMSController()
        try:
            ctrl.device_config.update(hw.cfg(True))
            assert ctrl.boot() is True
            ctrl.state.system_state = SystemState.FAULT
            now = datetime.now(timezone.utc)
            assert ctrl.queue.add_command("cmd-fault", "B", "ACTIVATE", now.isoformat(), (now + timedelta(minutes=5)).isoformat(), sequence_no=1)
            ctrl.process_one_command()
            check("allocation/cloud command during FAULT does not energize", not any(r.is_active for r in ctrl.gpio.relays.values()) and ctrl.state.system_state == SystemState.FAULT)
        finally:
            ctrl.shutdown()


def run_failsafe():
    R.clear()
    print("GPIO environment: MOCKED. No contactor coil is driven.")
    with patch.object(gm, "time", GPIO_TIME):
        pins_and_polarity()
        verification_and_multiple()
        unstable_and_init()
        cloud_and_lcd()
        command_during_fault()
    return list(R)


def test_feedback_failsafe():
    results = run_failsafe()
    assert results and all(results), f"{sum(results)}/{len(results)} passed"


if __name__ == "__main__":
    results = run_failsafe()
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if results and all(results) else 1)
