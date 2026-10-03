"""Phase 2C: day_based.json is replaced only when the persisted state changes."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi_firmware"))
os.environ.setdefault("EMS_DEVICE_ID", "qa-device")
os.environ.setdefault("EMS_API_KEY", "qa-api-key")
os.environ.setdefault("EMS_API_BASE_URL", "http://127.0.0.1:8000/api")

from energy import day_based as day_based_mod
from energy.day_based import DayBasedStrategy


def wings(days):
    return {code: {"ems_enabled": days.get(code, 0) > 0, "target_days": days.get(code, 0)} for code in "ABCD"}


class RecordingWrites:
    def __init__(self):
        self.payloads = []
        self.original = day_based_mod.atomic_write_json

    def __enter__(self):
        def wrapped(path, payload):
            self.payloads.append(json.loads(json.dumps(payload)))
            return self.original(path, payload)
        day_based_mod.atomic_write_json = wrapped
        return self

    def __exit__(self, *_exc):
        day_based_mod.atomic_write_json = self.original


class DayBasedWriteOnChangeTests(unittest.TestCase):
    def ctx(self, day, reset=15, verified=None, days=None):
        return {
            "operating_date": day,
            "reset_day": reset,
            "verified_active": verified,
            "wings": wings(days or {"A": 10, "B": 12, "C": 8, "D": 0}),
        }

    def test_writes_only_when_persisted_state_changes_and_reloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "day_based.json")
            strategy = DayBasedStrategy(path, lambda: True)
            with RecordingWrites() as writes:
                strategy.evaluate(self.ctx("2026-04-20"))
                self.assertTrue(strategy.persist())
                self.assertEqual(len(writes.payloads), 1)
                initial = dict(writes.payloads[0])
                self.assertEqual(initial["operating_date"], "2026-04-20")
                self.assertEqual(initial["scheduled"], "A")
                self.assertFalse(initial["paused"])
                self.assertIsNone(initial["last_applied"])

                stamp = os.stat(path).st_mtime_ns
                for _ in range(4):
                    same = strategy.evaluate(self.ctx("2026-04-20"))
                    self.assertEqual(same["action"], "ACTIVATE")
                    self.assertFalse(strategy.persist())
                self.assertEqual(len(writes.payloads), 1)
                self.assertEqual(os.stat(path).st_mtime_ns, stamp)
                self.assertEqual(json.loads(Path(path).read_text(encoding="utf-8")), initial)

                strategy.evaluate(self.ctx("2026-04-20", days={"A": 2, "B": 20, "C": 8, "D": 0}))
                self.assertTrue(strategy.persist())
                self.assertEqual(writes.payloads[-1]["scheduled"], "B")

                strategy.evaluate(self.ctx("2026-04-21", days={"A": 2, "B": 20, "C": 8, "D": 0}))
                self.assertTrue(strategy.persist())
                self.assertEqual(writes.payloads[-1]["operating_date"], "2026-04-21")

                strategy.evaluate(self.ctx("2026-04-21", verified="D", days={"A": 2, "B": 20, "C": 8, "D": 0}))
                self.assertTrue(strategy.state["paused"])
                self.assertTrue(strategy.persist())
                self.assertTrue(writes.payloads[-1]["paused"])

                strategy.evaluate(self.ctx("2026-04-22", days={"A": 2, "B": 20, "C": 8, "D": 0}))
                self.assertFalse(strategy.state["paused"])
                self.assertTrue(strategy.persist())
                self.assertFalse(writes.payloads[-1]["paused"])

                strategy.after_execution("ACTIVATE", "A", True, "A")
                self.assertTrue(strategy.persist())
                self.assertEqual(writes.payloads[-1]["last_applied"], "A")

                strategy.evaluate(self.ctx("2026-04-22", reset=22, verified="A", days={"A": 2, "B": 20, "C": 8, "D": 0}))
                self.assertTrue(strategy.persist())
                self.assertNotEqual(writes.payloads[-1]["scheduled"], writes.payloads[-2]["scheduled"])

            reloaded = DayBasedStrategy(path, lambda: True)
            self.assertEqual(reloaded.state["operating_date"], strategy.state["operating_date"])
            self.assertEqual(reloaded.state["paused"], strategy.state["paused"])
            self.assertEqual(reloaded.state["last_applied"], strategy.state["last_applied"])
            self.assertEqual(reloaded.state["scheduled"], strategy.state["scheduled"])
            self.assertEqual(reloaded.state["version"], 1)

    def test_blocked_write_is_retried_when_writes_are_allowed(self):
        allowed = {"value": False}
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "day_based.json")
            strategy = DayBasedStrategy(path, lambda: allowed["value"])
            with RecordingWrites() as writes:
                strategy.evaluate(self.ctx("2026-04-20"))
                self.assertFalse(strategy.persist())
                self.assertEqual(writes.payloads, [])
                self.assertTrue(strategy.dirty)
                allowed["value"] = True
                self.assertTrue(strategy.persist())
                self.assertEqual(len(writes.payloads), 1)
                self.assertFalse(strategy.dirty)


if __name__ == "__main__":
    unittest.main()
