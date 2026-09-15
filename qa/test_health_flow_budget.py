"""Health reporting/storage budget proofs, no live firmware/DB/services."""
import ast
import json
import subprocess
import types
import unittest
from collections import Counter
from datetime import datetime, timezone
from unittest.mock import patch

from qa import test_controller_health as fixtures

ROOT, HT, HR = fixtures.ROOT, fixtures.HT, fixtures.HR
BASELINE = "3ffba339a47cec4dbceee425079a72b80a32b499"


class HealthBudgetTests(unittest.TestCase):
    def test_24h_snapshots_do_not_add_writes_or_exceed_existing_read_cadence(self):
        now = datetime(2026, 9, 15, tzinfo=timezone.utc)
        clock = {"value": 0}
        reads = Counter()
        writes = []
        state = types.SimpleNamespace(health_boot=None, persisted_health_boot=None, health_state_unavailable=False,
                                      save_state=lambda **kw: writes.append(kw))
        values = {"/proc/sys/kernel/random/boot_id": "11111111-1111-1111-1111-111111111111",
                  "/sys/class/thermal/thermal_zone0/temp": "48000",
                  "/sys/class/watchdog/watchdog0/state": "inactive",
                  "/sys/class/watchdog/watchdog0/timeout": "15"}
        def read(path):
            reads[path] += 1
            return values[path]
        def service():
            reads["systemctl"] += 1
            return {"WatchdogUSec": "0", "ActiveState": "active"}
        health = HT.HealthTelemetry(state, 60, reader=read, service_reader=service,
                                    clock=lambda: clock["value"], now=lambda: now)
        self.assertEqual(writes, [], "boot observation itself must never trigger a state write")
        state.persisted_health_boot = dict(state.health_boot)  # existing boot commit acknowledgement
        for second in range(86400):
            clock["value"] = second
            snapshot = health.snapshot()
        self.assertEqual(reads["/proc/sys/kernel/random/boot_id"], 1)
        self.assertEqual(reads["systemctl"], 1440)
        for name in values:
            self.assertEqual(reads[name], 1 if name.endswith("boot_id") else 1440)
        self.assertEqual(writes, [])
        self.assertLess(len(json.dumps(state.health_boot, separators=(",", ":")).encode()), 200)
        self.assertLess(len(json.dumps(HR.normalize_report(snapshot, now), separators=(",", ":")).encode()), 1024)

    def test_state_sync_control_and_budget_cadences_unchanged_against_task_start(self):
        baseline_state = ast.parse(subprocess.check_output(["git", "show", f"{BASELINE}:pi_firmware/state.py"], cwd=ROOT, text=True))
        current_state = ast.parse((ROOT / "pi_firmware/state.py").read_text())
        def method(tree, class_name, name):
            cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
            return next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
        # No new write scheduler/save API or changed safety-critical mutation methods.
        names = ("save_state", "set_last_usage_date", "set_last_reset_period", "increment_used_day", "increment_clicks", "reset_days")
        for name in names:
            old_cls = next(n for n in baseline_state.body if isinstance(n, ast.ClassDef) and n.name == "PiStateManager")
            if any(isinstance(n, ast.FunctionDef) and n.name == name for n in old_cls.body):
                self.assertEqual(ast.dump(method(baseline_state, "PiStateManager", name)), ast.dump(method(current_state, "PiStateManager", name)), name)
        def calls(node):
            write_ops = {"open", "fsync", "replace", "flush", "dump", "close", "record_ems_write", "is_write_allowed"}
            return [ast.dump(n) for n in ast.walk(node) if isinstance(n, ast.Call)
                    and (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) in write_ops]
        self.assertEqual(calls(method(baseline_state, "PiStateManager", "_flush_to_disk")), calls(method(current_state, "PiStateManager", "_flush_to_disk")))
        for path in ("pi_firmware/resource_guard.py", "pi_firmware/storage_io_manager.py", "pi_firmware/storage_manager.py", "pi_firmware/config.py", "pi_firmware/ems-controller.service"):
            old = subprocess.check_output(["git", "show", f"{BASELINE}:{path}"], cwd=ROOT)
            self.assertEqual(old, (ROOT / path).read_bytes(), path)
        old_controller = ast.parse(subprocess.check_output(["git", "show", f"{BASELINE}:pi_firmware/ems_controller.py"], cwd=ROOT, text=True))
        current_controller = ast.parse((ROOT / "pi_firmware/ems_controller.py").read_text())
        for name in ("boot", "sync_cloud", "run", "shutdown", "_execute_local_transition", "_slot_visible"):
            self.assertEqual(ast.dump(method(old_controller, "EMSController", name)), ast.dump(method(current_controller, "EMSController", name)), name)

    def test_service_sampler_runs_only_readonly_bounded_show(self):
        result = types.SimpleNamespace(returncode=0, stdout="WatchdogUSec=2s\nActiveState=active\n")
        with patch.object(HT.subprocess, "run", return_value=result) as run:
            self.assertEqual(HT.service_watchdog()["WatchdogUSec"], "2s")
        args, kwargs = run.call_args
        self.assertEqual(args[0][:3], ["systemctl", "show", "ems-controller.service"])
        self.assertEqual(kwargs["timeout"], 1)
        self.assertFalse(kwargs.get("shell", False))
        self.assertNotIn("/dev/watchdog", str(args))
        with patch.object(HT.subprocess, "run", side_effect=subprocess.TimeoutExpired("systemctl", 1)):
            state = types.SimpleNamespace(health_boot=None, persisted_health_boot=None)
            health = HT.HealthTelemetry(state, 60, reader=lambda p: "11111111-1111-1111-1111-111111111111" if p.endswith("boot_id") else "0")
            self.assertIsNone(health.snapshot()["watchdog"]["service_timeout_us"])


class HealthSyncFlowTests(unittest.TestCase):
    def test_actual_sync_bindings_to_dashboard_preserve_unknown_and_storage_data(self):
        tree = ast.parse((ROOT / "backend/main.py").read_text())
        assignment = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == "health_report" for t in n.targets))
        storage_assignment = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                                  and any(isinstance(t, ast.Name) and t.id == "storage_health" for t in n.targets)
                                  and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name) and n.value.func.id == "Json")
        sync_call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                         and n.func.attr == "execute" and n.args and isinstance(n.args[0], ast.Constant)
                         and "INSERT INTO pi_state (device_id, active_slot, reset_day, emergency_stop" in str(n.args[0].value)
                         and "controller" not in str(n.args[0].value) and "storage_health" in str(n.args[0].value))
        storage_normalizer = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "normalize_storage_health")
        now = datetime(2026, 9, 15, tzinfo=timezone.utc)
        incoming = {"version": 1, "sampled_at": now.isoformat(), "cpu": {"celsius": 0, "source": "LINUX_THERMAL"},
                    "boot": {"count": 2, "tracking_since": now.isoformat(), "status": "OBSERVED"},
                    "watchdog": {"service_timeout_us": 0, "service_state": "ACTIVE", "hardware_state": "UNKNOWN", "recovery": "PASSED"}}
        old_storage = {"health": "UNAVAILABLE", "secondary_state": "SECONDARY_HEALTHY", "used_percent": 10}
        row = {"storage_health": dict(old_storage)}
        class Cursor:
            calls = []
            def execute(self, sql, params):
                self.calls.append((sql, params))
                self_params = params
                self.assertions = len(params) == sql.count("%s") == 29
                row.update(cpu_temp=params[5], boot_count=params[8], watchdog_enabled=params[11])
                row["storage_health"] = {**row["storage_health"], **self_params[-1]}
        cursor = Cursor()
        body = ast.Module(body=[storage_normalizer, assignment, storage_assignment, ast.Expr(sync_call)], type_ignores=[])
        code = compile(ast.fix_missing_locations(body), "actual_sync_health_binding_slice", "exec")
        for report, temperature, count in ((incoming, 0, 2), (None, None, None)):
            ns = {name.id: None for name in ast.walk(body) if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Load)}
            ns.update({"health_read_model": HR, "datetime": datetime, "timezone": timezone, "float": float, "int": int, "bool": bool,
                       "dict": dict, "isinstance": isinstance, "str": str, "Json": lambda value: value,
                       "STORAGE_HEALTH_STATES": {"GOOD", "WARNING", "FAILED", "UNAVAILABLE"}, "SMART_STATES": {"PASSED", "WARNING", "FAILED", "UNAVAILABLE"},
                       "SECONDARY_STATES": {"SECONDARY_HEALTHY", "SECONDARY_UNAVAILABLE", "SECONDARY_UNWRITABLE"},
                       "payload": {"controller_health": report, "cpuTemp": 99, "bootCount": 999, "watchdogEnabled": True},
                       "now": now, "cur": cursor, "DEFAULT_RESET_DAY": 15, "storage_health": None})
            exec(code, ns)
            self.assertTrue(cursor.assertions)
            self.assertEqual((row["cpu_temp"], row["boot_count"]), (temperature, count))
            self.assertIsNone(row["watchdog_enabled"])
            displayed = HR.dashboard_view(row["storage_health"], now, 120)
            self.assertEqual((displayed["cpu"]["celsius"], displayed["boot"]["count"]), (temperature, count))
            self.assertEqual(displayed["watchdog"]["recovery"], "NOT_VERIFIED")
            self.assertEqual({k: v for k, v in row["storage_health"].items() if k != "controller_health"}, old_storage)
        self.assertEqual(len(cursor.calls), 2, "one existing upsert per sync, no health-specific write")


if __name__ == "__main__":
    unittest.main(verbosity=2)