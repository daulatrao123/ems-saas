"""Offline controller-health regressions (AST/in-memory only, no live firmware/backend)."""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import tempfile
import threading
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(rel_path: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / rel_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


HT = load_module("pi_firmware/health_telemetry.py", "health_telemetry_test_mod")
HR = load_module("backend/health_read_model.py", "health_read_model_test_mod")


def extract_pi_state_methods(*names):
    code = (ROOT / "pi_firmware/state.py").read_text(encoding="utf-8")
    tree = ast.parse(code)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PiStateManager")
    selected = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in set(names)]
    unit = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(unit)
    ns = {
        "json": json,
        "hashlib": __import__("hashlib"),
        "os": __import__("os"),
        "datetime": datetime,
        "timezone": timezone,
        "STATE_VERSION": 4,
        "SUPPORTED_SLOTS": ["A", "B", "C", "D"],
        "logger": types.SimpleNamespace(critical=lambda *a, **k: None, error=lambda *a, **k: None),
    }
    exec(compile(unit, filename="pi_state_methods", mode="exec"), ns, ns)
    return ns


def make_state_stub(tmpdir: str):
    class SlotStub:
        def __init__(self):
            self.data = {
                "slot": "A",
                "commanded_state": "UNKNOWN",
                "gpio_output_state": "UNKNOWN",
                "feedback_state": "UNKNOWN",
                "verification_state": "NOT_CONFIGURED",
                "last_command_at": None,
                "used_days": 0,
                "clicks": 0,
            }

        def to_dict(self):
            return dict(self.data)

        def from_dict(self, payload):
            self.data = dict(payload)
            return True

    storage_calls = []
    io_calls = []
    storage = types.SimpleNamespace(
        is_write_allowed=lambda category: storage_calls.append(category) or True,
        io_meter=types.SimpleNamespace(record_ems_write=lambda c, b: io_calls.append((c, b))),
    )
    obj = types.SimpleNamespace(
        generation=0,
        system_state=types.SimpleNamespace(value="BOOT"),
        active_slot=None,
        last_usage_date=None,
        last_reset_period=None,
        health_boot=None,
        persisted_health_boot=None,
        health_state_unavailable=False,
        slots={k: SlotStub() for k in ("A", "B", "C", "D")},
        storage=storage,
        _save_lock=threading.Lock(),
        dirty=True,
        _storage_calls=storage_calls,
        _io_calls=io_calls,
        _tmpdir=tmpdir,
    )
    return obj


class ControllerHealthTelemetryTests(unittest.TestCase):
    # CPU and watchdog samples are bounded/typed; no fabricated values.
    def test_cpu_readings_valid_zero_negative_missing_nan_oversize(self):
        fixed_now = datetime(2026, 2, 1, tzinfo=timezone.utc)
        state = types.SimpleNamespace(health_boot=None, persisted_health_boot=None, health_state_unavailable=False)
        responses = {
            "/proc/sys/kernel/random/boot_id": "11111111-1111-1111-1111-111111111111",
            "/sys/class/thermal/thermal_zone0/temp": "48234",
            "/sys/class/watchdog/watchdog0/state": "active",
            "/sys/class/watchdog/watchdog0/timeout": "15",
        }
        reader = lambda path: responses[path]
        svc = lambda: {"WatchdogUSec": "0", "ActiveState": "inactive"}
        telem = HT.HealthTelemetry(state, 60, reader=reader, service_reader=svc, clock=lambda: 0, now=lambda: fixed_now)

        pending = telem.snapshot()
        self.assertEqual(pending["cpu"]["celsius"], 48.2)
        self.assertEqual(pending["boot"]["status"], "UNKNOWN")
        self.assertEqual(pending["boot"]["reason"], "AWAITING_EXISTING_STATE_COMMIT")

        state.persisted_health_boot = state.health_boot
        responses["/sys/class/thermal/thermal_zone0/temp"] = "0"
        telem._sample()
        self.assertEqual(telem.snapshot()["cpu"]["celsius"], 0.0)

        responses["/sys/class/thermal/thermal_zone0/temp"] = "-5000"
        telem._sample()
        self.assertEqual(telem.snapshot()["cpu"]["celsius"], -5.0)

        responses["/sys/class/thermal/thermal_zone0/temp"] = "NaN"
        telem._sample()
        self.assertIsNone(telem.snapshot()["cpu"]["celsius"])

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "temp"
            p.write_text("x" * 257, encoding="ascii")
            with self.assertRaises(ValueError):
                HT.read_text(str(p))

    # Boot identity semantics: first boot pending, same id no increment, changed id increments once.
    def test_boot_counter_observation_and_restart_rules(self):
        fixed_now = datetime(2026, 2, 1, tzinfo=timezone.utc)
        state = types.SimpleNamespace(health_boot=None, persisted_health_boot=None, health_state_unavailable=False)
        boot = {"id": "11111111-1111-1111-1111-111111111111"}

        def reader(path):
            if path == "/proc/sys/kernel/random/boot_id":
                return boot["id"]
            if path.endswith("/temp"):
                return "41000"
            if path.endswith("/state"):
                return "inactive"
            if path.endswith("/timeout"):
                return "0"
            raise OSError(path)

        telem = HT.HealthTelemetry(
            state,
            60,
            reader=reader,
            service_reader=lambda: {"WatchdogUSec": "0", "ActiveState": "inactive"},
            clock=lambda: 0,
            now=lambda: fixed_now,
        )
        self.assertEqual(state.health_boot["count"], 1)
        self.assertEqual(telem.snapshot()["boot"]["status"], "UNKNOWN")

        state.persisted_health_boot = dict(state.health_boot)
        self.assertEqual(telem.snapshot()["boot"]["count"], 1)

        # service restart / same boot id
        state2 = types.SimpleNamespace(
            health_boot={**state.health_boot},
            persisted_health_boot={**state.health_boot},
            health_state_unavailable=False,
        )
        telem2 = HT.HealthTelemetry(state2, 60, reader=reader, service_reader=lambda: {}, clock=lambda: 0, now=lambda: fixed_now)
        state2.persisted_health_boot = dict(state2.health_boot)
        self.assertEqual(telem2.snapshot()["boot"]["count"], 1)

        # new OS boot id increments once
        boot["id"] = "22222222-2222-2222-2222-222222222222"
        telem3 = HT.HealthTelemetry(state2, 60, reader=reader, service_reader=lambda: {}, clock=lambda: 0, now=lambda: fixed_now)
        self.assertEqual(state2.health_boot["count"], 2)

    # Corrupt state / unreadable boot-id stays UNKNOWN and must not fabricate observed count.
    def test_boot_unknown_on_corrupt_state_or_unreadable_boot_id(self):
        fixed_now = datetime(2026, 2, 1, tzinfo=timezone.utc)
        bad = types.SimpleNamespace(
            health_boot={"boot_id": "11111111-1111-1111-1111-111111111111", "count": "x", "tracking_since": fixed_now.isoformat()},
            persisted_health_boot=None,
            health_state_unavailable=False,
        )
        t_bad = HT.HealthTelemetry(bad, 60, reader=lambda _p: "11111111-1111-1111-1111-111111111111", service_reader=lambda: {}, clock=lambda: 0, now=lambda: fixed_now)
        self.assertEqual(t_bad.snapshot()["boot"]["status"], "UNKNOWN")
        self.assertEqual(t_bad.snapshot()["boot"]["reason"], "BOOT_TRACKING_UNAVAILABLE")

        unavailable = types.SimpleNamespace(health_state_unavailable=False)
        t_legacy = HT.HealthTelemetry(unavailable, 60, reader=lambda _p: "11111111-1111-1111-1111-111111111111", service_reader=lambda: {}, clock=lambda: 0, now=lambda: fixed_now)
        self.assertEqual(t_legacy.snapshot()["boot"]["status"], "UNKNOWN")

        unreadable_state = types.SimpleNamespace(health_boot=None, persisted_health_boot=None, health_state_unavailable=False)
        t_unreadable = HT.HealthTelemetry(unreadable_state, 60, reader=lambda _p: (_ for _ in ()).throw(OSError("denied")), service_reader=lambda: {}, clock=lambda: 0, now=lambda: fixed_now)
        self.assertEqual(t_unreadable.snapshot()["boot"]["status"], "UNKNOWN")

    # Watchdog evidence is read-only and recovery is always NOT_VERIFIED.
    def test_watchdog_parsing_and_recovery_not_verified(self):
        fixed_now = datetime(2026, 2, 1, tzinfo=timezone.utc)
        state = types.SimpleNamespace(health_boot=None, persisted_health_boot=None, health_state_unavailable=False)
        reader_map = {
            "/proc/sys/kernel/random/boot_id": "11111111-1111-1111-1111-111111111111",
            "/sys/class/thermal/thermal_zone0/temp": "40000",
            "/sys/class/watchdog/watchdog0/state": "ACTIVE",
            "/sys/class/watchdog/watchdog0/timeout": "30",
        }
        telem = HT.HealthTelemetry(state, 60, reader=lambda p: reader_map[p], service_reader=lambda: {"WatchdogUSec": "2min 3s", "ActiveState": "active"}, clock=lambda: 0, now=lambda: fixed_now)
        state.persisted_health_boot = state.health_boot
        snap = telem.snapshot()
        self.assertEqual(snap["watchdog"]["service_timeout_us"], 123000000)
        self.assertEqual(snap["watchdog"]["service_state"], "ACTIVE")
        self.assertEqual(snap["watchdog"]["hardware_state"], "ACTIVE")
        self.assertEqual(snap["watchdog"]["recovery"], "NOT_VERIFIED")

        self.assertIsNone(HT.timeout_us("malformed value"))
        self.assertEqual(HT.timeout_us("0"), 0)
        self.assertNotIn("/dev/watchdog", (ROOT / "pi_firmware/health_telemetry.py").read_text(encoding="utf-8"))

    # Snapshot cache uses interval budget: no extra reads/systemctl calls inside 60 seconds.
    def test_snapshot_cache_limits_sensor_and_systemd_reads(self):
        now = datetime(2026, 2, 1, tzinfo=timezone.utc)
        mono = {"t": 0.0}
        state = types.SimpleNamespace(health_boot=None, persisted_health_boot=None, health_state_unavailable=False, save_state=lambda **_: (_ for _ in ()).throw(AssertionError("sampling must not save state")))
        counts = {"boot": 0, "temp": 0, "wd_state": 0, "wd_timeout": 0, "svc": 0}

        def reader(path):
            if path.endswith("boot_id"):
                counts["boot"] += 1
                return "11111111-1111-1111-1111-111111111111"
            if path.endswith("/temp"):
                counts["temp"] += 1
                return "42000"
            if path.endswith("/state"):
                counts["wd_state"] += 1
                return "inactive"
            if path.endswith("/timeout"):
                counts["wd_timeout"] += 1
                return "0"
            raise OSError(path)

        def service():
            counts["svc"] += 1
            return {"WatchdogUSec": "0", "ActiveState": "inactive"}

        telem = HT.HealthTelemetry(state, 60, reader=reader, service_reader=service, clock=lambda: mono["t"], now=lambda: now)
        state.persisted_health_boot = state.health_boot
        telem.snapshot()
        telem.snapshot()
        self.assertEqual(counts["boot"], 1, "boot id read exactly once at init")
        self.assertEqual(counts["svc"], 1)
        self.assertEqual(counts["temp"], 1)

        mono["t"] = 61.0
        telem.snapshot()
        self.assertEqual(counts["svc"], 2)
        self.assertEqual(counts["temp"], 2)


class StateMetadataAstTests(unittest.TestCase):
    # Existing STATE_VERSION=4 checksums still protect state docs with optional health_boot metadata.
    def test_state_build_load_flush_roundtrip_and_checksum(self):
        ns = extract_pi_state_methods("_build_document", "_verify_document", "_load_state", "_flush_to_disk")
        with tempfile.TemporaryDirectory() as td:
            class _SystemState:
                BOOT = types.SimpleNamespace(value="BOOT")

                def __new__(cls, value):
                    if value == "BOOT":
                        return cls.BOOT
                    raise ValueError(value)

            ns.update(
                {
                    "STATE_FILE": str(Path(td) / "current_state.json"),
                    "BACKUP_STATE_FILE": str(Path(td) / "backup_state.json"),
                    "RECOVERY_STATE_FILE": str(Path(td) / "recovery_state.json"),
                    "SystemState": _SystemState,
                }
            )
            obj = make_state_stub(td)
            obj._build_document = lambda: ns["_build_document"](obj)
            obj._verify_document = lambda data: ns["_verify_document"](data)

            obj.health_boot = {
                "boot_id": "11111111-1111-1111-1111-111111111111",
                "count": 1,
                "tracking_since": datetime.now(timezone.utc).isoformat(),
            }
            doc = ns["_build_document"](obj)
            self.assertEqual(doc["version"], 4)
            self.assertTrue(ns["_verify_document"](doc))

            ok = ns["_flush_to_disk"](obj)
            self.assertTrue(ok)
            self.assertEqual(obj.persisted_health_boot, obj.health_boot)
            self.assertEqual(obj._storage_calls, ["state"])
            self.assertEqual(len(obj._io_calls), 1)
            self.assertEqual(obj._io_calls[0][0], "state")

            # Load back with health_boot present.
            loaded = make_state_stub(td)
            loaded._verify_document = lambda d: ns["_verify_document"](d)
            self.assertTrue(ns["_load_state"](loaded))
            self.assertEqual(loaded.health_boot["count"], 1)
            self.assertEqual(loaded.persisted_health_boot["count"], 1)

            # Older state (without health_boot) still loads safely.
            old = dict(doc)
            old.pop("health_boot", None)
            old["sha256"] = __import__("hashlib").sha256(json.dumps({k: v for k, v in old.items() if k != "sha256"}, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            Path(ns["STATE_FILE"]).write_text(json.dumps(old), encoding="utf-8")
            loaded_old = make_state_stub(td)
            loaded_old._verify_document = lambda d: ns["_verify_document"](d)
            self.assertTrue(ns["_load_state"](loaded_old))
            self.assertIsNone(loaded_old.health_boot)

    # Corrupt state candidates set health_state_unavailable and never create fake boot metadata.
    def test_corrupt_state_main_and_backup_sets_unavailable(self):
        ns = extract_pi_state_methods("_verify_document", "_load_state")
        with tempfile.TemporaryDirectory() as td:
            ns.update(
                {
                    "STATE_FILE": str(Path(td) / "current_state.json"),
                    "BACKUP_STATE_FILE": str(Path(td) / "backup_state.json"),
                    "RECOVERY_STATE_FILE": str(Path(td) / "recovery_state.json"),
                    "SystemState": types.SimpleNamespace(BOOT=types.SimpleNamespace(value="BOOT")),
                }
            )
            Path(ns["STATE_FILE"]).write_text("not-json", encoding="utf-8")
            Path(ns["BACKUP_STATE_FILE"]).write_text("{}", encoding="utf-8")
            Path(ns["RECOVERY_STATE_FILE"]).write_text("[]", encoding="utf-8")
            obj = make_state_stub(td)
            obj._verify_document = lambda d: ns["_verify_document"](d)
            self.assertFalse(ns["_load_state"](obj))
            self.assertTrue(obj.health_state_unavailable)

    # State write gate / fsync-path failure must not mark pending health_boot as committed.
    def test_flush_gate_or_write_failure_never_commits_pending_count(self):
        ns = extract_pi_state_methods("_build_document", "_flush_to_disk")
        with tempfile.TemporaryDirectory() as td:
            ns.update(
                {
                    "STATE_FILE": str(Path(td) / "current_state.json"),
                    "BACKUP_STATE_FILE": str(Path(td) / "backup_state.json"),
                    "RECOVERY_STATE_FILE": str(Path(td) / "recovery_state.json"),
                }
            )
            obj = make_state_stub(td)
            obj._build_document = lambda: ns["_build_document"](obj)
            obj.health_boot = {
                "boot_id": "11111111-1111-1111-1111-111111111111",
                "count": 2,
                "tracking_since": datetime.now(timezone.utc).isoformat(),
            }
            obj.persisted_health_boot = {
                "boot_id": "11111111-1111-1111-1111-111111111111",
                "count": 1,
                "tracking_since": datetime.now(timezone.utc).isoformat(),
            }

            # write blocked
            obj.storage.is_write_allowed = lambda _c: False
            self.assertFalse(ns["_flush_to_disk"](obj))
            self.assertEqual(obj.persisted_health_boot["count"], 1)

            # write path exception
            obj.storage.is_write_allowed = lambda _c: True
            with patch("builtins.open", side_effect=OSError("write fail")):
                self.assertFalse(ns["_flush_to_disk"](obj))
            self.assertEqual(obj.persisted_health_boot["count"], 1)


class BackendHealthReadModelTests(unittest.TestCase):
    # Report normalization enforces finite types/ranges/timestamps; unknowns stay null-safe.
    def test_normalize_report_bounds_and_unknowns(self):
        now = datetime(2026, 2, 1, tzinfo=timezone.utc)
        raw = {
            "version": 1,
            "sampled_at": (now + timedelta(seconds=10)).isoformat(),
            "cpu": {"celsius": 48.2, "source": "LINUX_THERMAL"},
            "boot": {"count": 5, "tracking_since": (now - timedelta(days=1)).isoformat(), "status": "OBSERVED"},
            "watchdog": {
                "service_timeout_us": 0,
                "service_state": "ACTIVE",
                "hardware_state": "INACTIVE",
                "hardware_timeout_seconds": 20,
                "recovery": "PASSED",  # must be ignored
            },
        }
        out = HR.normalize_report(raw, now)
        self.assertEqual(out["cpu"]["celsius"], 48.2)
        self.assertEqual(out["boot"]["count"], 5)
        self.assertEqual(out["watchdog"]["recovery"], "NOT_VERIFIED")
        self.assertEqual(HR.watchdog_enabled(out), False)

        bad = HR.normalize_report({"version": 1, "sampled_at": "2099-01-01T00:00:00Z", "cpu": {"celsius": True, "source": "LINUX_THERMAL"}}, now)
        self.assertIsNone(bad["sampled_at"])
        self.assertIsNone(bad["cpu"]["celsius"])
        self.assertEqual(bad["boot"]["status"], "UNKNOWN")

    # Dashboard view freshness follows max-age; invalid/legacy payload clears stale claims.
    def test_dashboard_freshness_and_legacy_report_clearing(self):
        now = datetime(2026, 2, 1, tzinfo=timezone.utc)
        good = {
            "controller_health": {
                "version": 1,
                "sampled_at": (now - timedelta(seconds=10)).isoformat(),
                "cpu": {"celsius": 0, "source": "LINUX_THERMAL"},
                "boot": {"count": 1, "tracking_since": (now - timedelta(days=2)).isoformat(), "status": "OBSERVED"},
                "watchdog": {"service_timeout_us": 1000000, "service_state": "ACTIVE", "hardware_state": "ACTIVE", "hardware_timeout_seconds": 15, "recovery": "PASSED"},
            }
        }
        out = HR.dashboard_view(good, now, 120)
        self.assertEqual(out["status"], "CURRENT")
        self.assertEqual(out["cpu"]["celsius"], 0)

        stale = dict(good)
        stale["controller_health"] = {**stale["controller_health"], "sampled_at": (now - timedelta(seconds=300)).isoformat()}
        self.assertEqual(HR.dashboard_view(stale, now, 120)["status"], "STALE")

        legacy = {"controller_health": {"version": "legacy", "sampled_at": "2026-01-01T00:00:00Z", "cpu": {"celsius": 99, "source": "LEGACY"}}}
        cleared = HR.dashboard_view(legacy, now, 120)
        self.assertEqual(cleared["status"], "UNKNOWN")
        self.assertIsNone(cleared["cpu"]["celsius"])

    # SQL upsert contract keeps 29 bind params and existing JSONB merge path.
    def test_main_sync_sql_param_count_and_merge_contract(self):
        src = (ROOT / "backend/main.py").read_text(encoding="utf-8")
        m = re.search(
            r"INSERT INTO pi_state \(device_id, active_slot, reset_day, emergency_stop, uptime_seconds, cpu_temp, disk_free_mb, "
            r"last_sync, boot_count, last_shutdown_reason, clock_source, watchdog_enabled, last_reboot_reason, config_version,"
            r"\s*desired_config_hash, applied_config_version, applied_config_hash, applied_config_at, config_state, config_error,"
            r"\s*ota_desired_version, ota_state, ota_version, ota_attempts, ota_last_error, ota_updated_at, last_good_firmware_version, hardware_fault, storage_health\)"
            r"\s*VALUES \((.*?)\)\s*ON CONFLICT",
            src,
            flags=re.S,
        )
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).count("%s"), 29)
        self.assertIn("storage_health=COALESCE(pi_state.storage_health, '{}'::jsonb) || EXCLUDED.storage_health", src)

    # Execute the actual storage display expression. Only controller_health is
    # persisted by this protocol; controller_metadata is not a real field.
    def test_dashboard_keeps_storage_fields_and_separates_controller_health(self):
        tree = ast.parse((ROOT / "backend/main.py").read_text(encoding="utf-8"))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "admin_dashboard")
        expression = next(value for node in ast.walk(fn) if isinstance(node, ast.Dict)
                          for key, value in zip(node.keys, node.values)
                          if isinstance(key, ast.Constant) and key.value == "storage_health")
        compiled = compile(ast.Expression(expression), "actual_dashboard_storage", "eval")
        storage = {"health": "UNAVAILABLE", "secondary_state": "SECONDARY_HEALTHY", "free_bytes": 123}
        pi = {"storage_health": {**storage, "controller_health": {"version": 1}}}
        self.assertEqual(eval(compiled, {"pi": pi}), storage)
        self.assertIsNone(eval(compiled, {"pi": {"storage_health": {"controller_health": {"version": 1}}}}))
        self.assertIsNone(eval(compiled, {"pi": None}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
