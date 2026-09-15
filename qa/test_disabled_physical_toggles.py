"""Offline AST-extracted regressions for retired physical-toggle behavior.

No firmware runtime imports/startup; methods are extracted from source and executed
against inert stubs only.
"""

from __future__ import annotations

import ast
import subprocess
import types
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TASK_START_SHA = "fc41d93750d3195f4a8e2b672187740521227d75"


def src_text(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def class_method_source(code: str, class_name: str, method_name: str) -> str:
    tree = ast.parse(code)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    segment = ast.get_source_segment(code, item)
                    if not segment:
                        raise AssertionError(f"Cannot extract {class_name}.{method_name}")
                    return segment
    raise AssertionError(f"Missing {class_name}.{method_name}")


def extract_class_method(rel_path: str, class_name: str, method_name: str, globs: dict):
    code = src_text(rel_path)
    method_src = class_method_source(code, class_name, method_name)
    mod = ast.parse(method_src)
    fn_node = mod.body[0]
    unit = ast.Module(body=[fn_node], type_ignores=[])
    ast.fix_missing_locations(unit)
    ns = dict(globs)
    exec(compile(unit, filename=f"{class_name}.{method_name}", mode="exec"), ns, ns)
    return ns[method_name], method_src


def extract_functions(rel_path: str, names: list[str], globs: dict):
    code = src_text(rel_path)
    tree = ast.parse(code)
    out_nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            # Keep constants used by LCD helpers (e.g., LCD_COLS).
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id in {"LCD_COLS", "LCD_ROWS"}:
                    out_nodes.append(node)
        if isinstance(node, ast.FunctionDef) and node.name in names:
            out_nodes.append(node)
    unit = ast.Module(body=out_nodes, type_ignores=[])
    ast.fix_missing_locations(unit)
    ns = dict(globs)
    exec(compile(unit, filename=rel_path, mode="exec"), ns, ns)
    return ns


@dataclass
class EnumLike:
    value: str


class ToggleDisableRegression(unittest.TestCase):
    # physical toggles are ignored; queue drained, no action/state-write side effects
    def test_process_toggle_events_discards_queue_only(self):
        fn, _ = extract_class_method("pi_firmware/ems_controller.py", "EMSController", "process_toggle_events", {})

        class G:
            def __init__(self):
                self.calls = 0

            def pop_toggle_events(self):
                self.calls += 1
                return [{"slot": "A", "on": True}, {"slot": "A", "on": False}]

        ctl = types.SimpleNamespace(gpio=G(), state=types.SimpleNamespace(saved=0), touched=False)
        out = fn(ctl)
        self.assertEqual(out, 0)
        self.assertEqual(ctl.gpio.calls, 1)
        self.assertEqual(getattr(ctl.state, "saved", 0), 0)

    # direct local origin TOGGLE rejected before writes; ENERGY path still uses safety gate
    def test_execute_local_transition_toggle_rejection_and_energy_gate(self):
        SystemState = types.SimpleNamespace(
            READY=EnumLike("READY"),
            CLOUD_OFFLINE=EnumLike("CLOUD_OFFLINE"),
            EXECUTING=EnumLike("EXECUTING"),
            FAULT=EnumLike("FAULT"),
        )
        logger = types.SimpleNamespace(critical=lambda *a, **k: None)
        fn, _ = extract_class_method(
            "pi_firmware/ems_controller.py",
            "EMSController",
            "_execute_local_transition",
            {"SystemState": SystemState, "logger": logger},
        )

        class State:
            def __init__(self, value="READY"):
                self.system_state = EnumLike(value)
                self.saved = 0
                self.slots = {"A": types.SimpleNamespace(verification_state=EnumLike("VERIFIED_OFF"))}

            def save_state(self, immediate=True):
                self.saved += 1
                return True

            def increment_clicks(self, slot, immediate=False):
                raise AssertionError("toggle clicks must not be incremented via disabled TOGGLE path")

        class Gpio:
            def __init__(self):
                self.transition_calls = 0
                self.deactivate_calls = 0

            def transition_slot(self, slot):
                self.transition_calls += 1
                return True

            def deactivate_slot(self, slot):
                self.deactivate_calls += 1
                return True

        ctl = types.SimpleNamespace(state=State("READY"), gpio=Gpio())

        success, reason = fn(ctl, "A", True, "TOGGLE")
        self.assertEqual((success, reason), (False, "PHYSICAL_TOGGLES_DISABLED"))
        self.assertEqual((ctl.state.system_state.value, ctl.state.saved), ("READY", 0))
        self.assertEqual((ctl.gpio.transition_calls, ctl.gpio.deactivate_calls), (0, 0))

        ctl_blocked = types.SimpleNamespace(state=State("SELF_TEST"), gpio=Gpio())
        success, reason = fn(ctl_blocked, "A", True, "ENERGY")
        self.assertEqual((success, reason), (False, "SYSTEM_SELF_TEST"))
        self.assertEqual(ctl_blocked.state.saved, 0)

        ctl_ok = types.SimpleNamespace(state=State("READY"), gpio=Gpio())
        success, reason = fn(ctl_ok, "A", True, "ENERGY")
        self.assertEqual((success, reason), (True, None))
        self.assertEqual(ctl_ok.gpio.transition_calls, 1)
        self.assertEqual(ctl_ok.state.saved, 2, "persist EXECUTING entry + restore prior state")

    # no-feedback usage visibility remains false even if some legacy toggle is hypothetically ON
    def test_slot_visible_uses_installed_contactor_feedback_not_toggle(self):
        fn, _ = extract_class_method("pi_firmware/ems_controller.py", "EMSController", "_slot_visible", {})

        slot_state_on = types.SimpleNamespace(feedback_state=EnumLike("ON"))
        slot_state_off = types.SimpleNamespace(feedback_state=EnumLike("OFF"))

        base = {
            "slots": {"A": {"target_days": 5, "disabled": False}},
            "feedback_hardware_installed": False,
        }
        ctl = types.SimpleNamespace(device_config=base, state=types.SimpleNamespace(slots={"A": slot_state_on}), gpio=types.SimpleNamespace(toggles={"A": True}))
        self.assertFalse(fn(ctl, "A"), "without installed feedback, usage eligibility must stay false")

        ctl.device_config["feedback_hardware_installed"] = True
        self.assertTrue(fn(ctl, "A"), "installed contactor ON remains eligible")

        ctl.state.slots["A"] = slot_state_off
        self.assertFalse(fn(ctl, "A"), "installed contactor OFF remains ineligible")

    # constructor creates only relay + detect devices; no toggle Button callbacks registered
    def test_gpio_constructor_has_no_toggle_devices_or_callbacks(self):
        created = {"relay": [], "detect": []}

        class OutputDevice:
            def __init__(self, pin, active_high=True, initial_value=False):
                created["relay"].append((pin, active_high, initial_value))
                self.pin = pin
                self.is_active = bool(initial_value)

            def close(self):
                return None

        class Button:
            def __init__(self, pin, pull_up=False, bounce_time=None):
                created["detect"].append((pin, pull_up, bounce_time))
                self.pin = pin
                self.pull_up = pull_up
                self.bounce_time = bounce_time
                self.when_pressed = None
                self.when_released = None

            def close(self):
                return None

        class ThreadFake:
            def __init__(self, target, name, daemon):
                self.target = target
                self.name = name
                self.daemon = daemon
                self.started = False

            def start(self):
                self.started = True

        def get_hardware_profile(_):
            return {
                "relay_active_low": True,
                "toggle_active_low": True,
                "detect_active_low": True,
                "slots": ["A", "B", "C", "D"],
                "channels": {
                    "A": {"relay_gpio": 1, "detect_gpio": 11},
                    "B": {"relay_gpio": 2, "detect_gpio": 12},
                    "C": {"relay_gpio": 3, "detect_gpio": 13},
                    "D": {"relay_gpio": 4, "detect_gpio": 14},
                },
            }

        logger = types.SimpleNamespace(critical=lambda *a, **k: None)
        threading_mod = types.SimpleNamespace(RLock=lambda: object(), Lock=lambda: object(), Thread=ThreadFake)
        collections_mod = types.SimpleNamespace(deque=lambda maxlen=64: [])

        init_fn, init_src = extract_class_method(
            "pi_firmware/gpio_manager.py",
            "GPIOManager",
            "__init__",
            {
                "get_hardware_profile": get_hardware_profile,
                "FEEDBACK_DEBOUNCE_MS": 50,
                "OutputDevice": OutputDevice,
                "Button": Button,
                "threading": threading_mod,
                "collections": collections_mod,
                "logger": logger,
            },
        )

        class DummyGPIO:
            __init__ = init_fn

            def _release_devices(self):
                self.relays = {}
                self.feedback_inputs = {}
                self.toggles = {}

            def _local_monitor_loop(self):
                return None

        mgr = DummyGPIO(state_manager=object(), device_config={"hardware_profile": "EMS-4CH-v1"})
        self.assertEqual(len(created["relay"]), 4)
        self.assertEqual(len(created["detect"]), 4)
        self.assertEqual(mgr.toggles, {})
        self.assertNotIn("self.toggles[slot]", init_src)
        self.assertNotIn("when_pressed", init_src)
        self.assertNotIn("when_released", init_src)
        self.assertTrue(getattr(mgr.monitor_thread, "started", False))

    # LCD row formatting removed TG label; contactor + days retained
    def test_lcd_wing_rows_no_tg_label_and_contactor_days_preserved(self):
        ns = extract_functions(
            "pi_firmware/lcd_display.py",
            ["_fit", "_lr", "fmt_days", "_onoff", "_slot_line", "render_wings"],
            {"textwrap": __import__("textwrap")},
        )
        render_wings = ns["render_wings"]
        lines = render_wings(
            {
                "slots": {
                    "A": {"disabled": False, "used_days": 2, "target_days": 5, "contactor": "ON"},
                    "B": {"disabled": False, "used_days": 0, "target_days": 7, "contactor": "OFF"},
                },
                "active_slot": "A",
            },
            ("A", "B"),
        )
        self.assertEqual(len(lines), 4)
        self.assertIn("A ON", lines[0])
        self.assertIn("2/5", lines[0])
        self.assertIn("CT A:ON", lines[2])
        self.assertNotIn("TG", "\n".join(lines), "toggle label must be absent")

    # task-start parity check: critical relay/qualification/reconciliation + cloud command methods unchanged
    def test_ast_method_parity_with_task_start_hash(self):
        def git_show(rel_path: str) -> str:
            return subprocess.check_output(
                ["git", "show", f"{TASK_START_SHA}:{rel_path}"],
                cwd=str(ROOT),
                text=True,
            )

        checks = [
            ("pi_firmware/gpio_manager.py", "GPIOManager", ["_read_feedback_raw", "_reconcile_hardware_state_locked", "transition_slot", "deactivate_slot"]),
            ("pi_firmware/ems_controller.py", "EMSController", ["_run_software_command", "_accept_cloud_command", "process_one_command"]),
        ]

        for rel_path, cls, methods in checks:
            now_code = src_text(rel_path)
            old_code = git_show(rel_path)
            for method in methods:
                with self.subTest(file=rel_path, method=method):
                    now = class_method_source(now_code, cls, method).strip()
                    old = class_method_source(old_code, cls, method).strip()
                    self.assertEqual(now, old)


if __name__ == "__main__":
    unittest.main(verbosity=2)
