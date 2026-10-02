"""DAY_BASED cycle conversion, set_days batching, and apply status.

No live database. AllocationPolicy and energy_calculation_mode are not exercised here.
"""
from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "pi_firmware"))

from energy_api import day_allocation as DA  # noqa: E402
from energy_api.routes import create_router  # noqa: E402
from config_hash import canonical_device_config  # noqa: E402
from energy.allocation import verified_active  # noqa: E402
from energy.day_based import DayBasedStrategy  # noqa: E402
from energy.meter_manager import EnergyEngine  # noqa: E402

DID = "22222222-2222-2222-2222-222222222222"


class CycleMath(unittest.TestCase):
    def test_cycle_lengths_follow_reset_day(self):
        self.assertEqual(DA.cycle_length(date(2026, 4, 15), 15), 30)
        self.assertEqual(DA.cycle_length(date(2026, 3, 15), 15), 31)
        self.assertEqual(DA.cycle_length(date(2024, 2, 15), 15), 29)
        self.assertEqual(DA.cycle_length(date(2023, 2, 15), 15), 28)
        self.assertEqual(DA.cycle_day_index(date(2026, 4, 15), 15), 1)
        self.assertEqual(DA.cycle_day_index(date(2026, 5, 14), 15), 30)

    def test_largest_remainder_sums_to_cycle(self):
        days = DA.units_to_days({"A": 156.12, "B": 170.55, "C": 75.66, "D": 0}, 30)
        self.assertEqual(days, {"A": 11, "B": 13, "C": 6, "D": 0})
        self.assertEqual(sum(days.values()), 30)

    def test_direct_days_must_match_enabled_wings_only(self):
        enabled = ["A", "B", "D"]
        days = DA.resolve_days("DAYS", {"A": 10, "B": 12, "C": 0, "D": 8}, enabled, 30)
        self.assertEqual(days, {"A": 10, "B": 12, "D": 8})
        self.assertNotIn("C", days)
        with self.assertRaises(ValueError):
            DA.resolve_days("DAYS", {"A": 10, "B": 12, "D": 8}, enabled, 31)
        with self.assertRaises(ValueError):
            DA.resolve_days("DAYS", {"A": 10, "B": 12, "C": 4, "D": 8}, enabled, 30)
        with self.assertRaises(ValueError):
            DA.resolve_days("DAYS", {"A": True, "B": 12, "D": 8}, enabled, 30)
        self.assertEqual(DA.resolve_days("DAYS", {"A": 0, "B": 0, "C": 0, "D": 0}, ["A", "B", "C", "D"], 0), {"A": 0, "B": 0, "C": 0, "D": 0})

    def test_schedule_and_status(self):
        days = {"A": 10, "B": 12, "C": 8}
        self.assertEqual(DA.scheduled_wing(days, 10, ["A", "B", "C"]), "A")
        self.assertEqual(DA.scheduled_wing(days, 22, ["A", "B", "C"]), "B")
        self.assertEqual(DA.scheduled_wing(days, 23, ["A", "B", "C"]), "C")
        self.assertIsNone(DA.scheduled_wing(days, 23, ["A", "B"]))
        self.assertEqual(DA.application_status([{"status": "queued"}, {"status": "delivered"}]), "APPLYING")
        self.assertEqual(DA.application_status([{"status": "completed"}, {"status": "queued"}]), "PARTIALLY_APPLIED")
        self.assertEqual(DA.application_status([{"status": "completed"}, {"status": "acked"}]), "APPLIED")
        self.assertEqual(DA.application_status([{"status": "failed"}, {"status": "expired"}]), "FAILED")
        self.assertEqual(DA.application_status([{"status": "completed"}, {"status": "failed"}]), "PARTIALLY_APPLIED")
        self.assertIsNone(DA.application_status([]))


class QueueBatch(unittest.TestCase):
    def test_disabled_wing_is_not_inserted_and_zero_is(self):
        cur = RecordingCursor()
        created = DA.queue_set_days(cur, DID, {"A": 10, "B": 0, "D": 20}, "batch-1", "idem", datetime(2026, 4, 20, tzinfo=timezone.utc))
        slots = [row["slot"] for row in created]
        self.assertEqual(slots, ["A", "B", "D"])
        self.assertEqual([row["days"] for row in created], [10, 0, 20])
        inserted = [params for sql, params in cur.calls if "INSERT INTO pi_commands" in sql]
        self.assertEqual([params[2] for params in inserted], ["A", "B", "D"])
        self.assertEqual(inserted[1][3].obj, {"days": 0})
        self.assertNotIn("commit", " ".join(sql for sql, _params in cur.calls).lower())


class RecordingCursor:
    def __init__(self):
        self.calls = []
        self.sequence = 3

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "SELECT next_command_sequence" in sql:
            self._row = {"next_command_sequence": self.sequence}

    def fetchone(self):
        return self._row


class ApplyRoute(unittest.TestCase):
    def setUp(self):
        self.conn = ApplyConn()
        self.user = {"role": "society_admin", "society_id": 7, "sub": "admin"}
        router = create_router(lambda: self.conn, lambda: self.user, lambda *args: None, lambda value, _name: value)
        self.apply = next(route.endpoint for route in router.routes if getattr(route, "path", "") == "/api/energy/day-allocation/apply")
        self.today = date(2026, 4, 20)

    def _call(self, payload, user=None):
        return self.apply(payload, user or self.user)

    def test_member_cannot_write(self):
        with self.assertRaises(HTTPException) as caught:
            self._call({"society_id": 7, "device_id": DID, "type": "DAYS", "wings": {}, "idempotency_key": "k"}, {"role": "member", "society_id": 7})
        self.assertEqual(caught.exception.status_code, 403)
        self.assertFalse(self.conn.committed)

    def test_other_society_is_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            self._call({"society_id": 9, "device_id": DID, "type": "DAYS", "wings": {}, "idempotency_key": "k"})
        self.assertEqual(caught.exception.status_code, 403)
        self.assertFalse(self.conn.committed)

    def test_super_admin_can_apply_for_another_society(self):
        import energy_api.queries as queries
        original = queries.device_today
        queries.device_today = lambda *args, **kwargs: self.today
        try:
            body = self._call({
                "society_id": 9, "device_id": DID, "type": "DAYS", "idempotency_key": "super-key",
                "wings": {"A": 10, "B": 0, "C": 0, "D": 20},
            }, {"role": "super_admin", "society_id": None})
        finally:
            queries.device_today = original
        self.assertEqual(body["status"], "APPLYING")
        self.assertEqual(self.conn.commits, 1)

    def test_apply_commits_once_and_reports_applying(self):
        import energy_api.queries as queries
        original = queries.device_today
        queries.device_today = lambda *args, **kwargs: self.today
        try:
            body = self._call({
                "society_id": 7, "device_id": DID, "type": "DAYS", "idempotency_key": "batch-key",
                "wings": {"A": 10, "B": 0, "C": 0, "D": 20},
            })
        finally:
            queries.device_today = original
        self.assertEqual(body["status"], "APPLYING")
        self.assertNotEqual(body["status"], "APPLIED")
        self.assertTrue(self.conn.committed)
        self.assertEqual(self.conn.commits, 1)
        slots = [params[2] for sql, params in self.conn.cur.calls if "INSERT INTO pi_commands" in sql]
        self.assertEqual(slots, ["A", "B", "D"])
        self.assertNotIn("C", slots)

    def test_failed_insert_rolls_back_the_batch(self):
        import energy_api.queries as queries
        original = queries.device_today
        queries.device_today = lambda *args, **kwargs: self.today
        self.conn.cur.fail_on_insert = 2
        try:
            with self.assertRaises(RuntimeError):
                self._call({
                    "society_id": 7, "device_id": DID, "type": "DAYS", "idempotency_key": "batch-key",
                    "wings": {"A": 10, "B": 0, "C": 0, "D": 20},
                })
        finally:
            queries.device_today = original
        self.assertFalse(self.conn.committed)
        self.assertGreaterEqual(self.conn.rollbacks, 1)


class ApplyConn:
    def __init__(self):
        self.cur = ApplyCursor()
        self.committed = False
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, row_factory=None):
        return self

    def __enter__(self):
        return self.cur

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        self.committed = True
        self.commits += 1

    def rollback(self):
        self.committed = False
        self.rollbacks += 1

    def close(self):
        return None


class ApplyCursor:
    def __init__(self):
        self.calls = []
        self.fail_on_insert = None
        self.inserts = 0
        self.device = {"id": DID, "allocation_mode": "DAY_BASED", "allocation_mode_version": 1, "reset_day": 15, "day_allocation": None, "next_command_sequence": 4}

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self.sql = sql
        if "INSERT INTO pi_commands" in sql:
            self.inserts += 1
            if self.fail_on_insert == self.inserts:
                raise RuntimeError("insert failed")

    def fetchone(self):
        if "FROM pi_devices d JOIN societies" in self.sql:
            return self.device
        if "next_command_sequence" in self.sql and "SELECT" in self.sql:
            return {"next_command_sequence": self.device["next_command_sequence"]}
        if "idempotency_key" in self.sql:
            return None
        return None

    def fetchall(self):
        if "slot_configs" in self.sql:
            return [
                {"slot": "A", "disabled": False},
                {"slot": "B", "disabled": False},
                {"slot": "C", "disabled": True},
                {"slot": "D", "disabled": False},
            ]
        return []


class DayStrategy(unittest.TestCase):
    def test_schedule_pause_and_advance(self):
        with tempfile.TemporaryDirectory() as tmp:
            strategy = DayBasedStrategy(str(Path(tmp) / "day_based.json"), lambda: True)
            wings = {code: {"ems_enabled": code != "D", "target_days": {"A": 10, "B": 12, "C": 8, "D": 5}[code]} for code in "ABCD"}
            paused = strategy.evaluate({"operating_date": "2026-04-20", "reset_day": 15, "verified_active": "D", "wings": wings})
            self.assertIsNone(paused["action"])
            self.assertTrue(strategy.state["paused"])
            held = strategy.evaluate({"operating_date": "2026-04-20", "reset_day": 15, "verified_active": None, "wings": wings})
            self.assertIsNone(held["action"])
            nxt = strategy.evaluate({"operating_date": "2026-04-21", "reset_day": 15, "verified_active": None, "wings": wings})
            self.assertEqual(nxt, {"action": "ACTIVATE", "slot": "A", "events": []})
            strategy.after_execution("ACTIVATE", "A", True, "A")
            advance = strategy.evaluate({"operating_date": "2026-05-01", "reset_day": 15, "verified_active": "A", "wings": wings})
            self.assertEqual(advance["action"], "DEACTIVATE")
            self.assertEqual(advance["slot"], "A")

    def test_invalid_reset_day_does_not_schedule(self):
        with tempfile.TemporaryDirectory() as tmp:
            strategy = DayBasedStrategy(str(Path(tmp) / "day_based.json"), lambda: True)
            wings = {code: {"ems_enabled": True, "target_days": 30 if code == "A" else 0} for code in "ABCD"}
            ctx = {"operating_date": "2026-04-20", "verified_active": None, "wings": wings}
            for reset_day in (0, 29, 31, True, "nope"):
                decision = strategy.evaluate({**ctx, "reset_day": reset_day})
                self.assertIsNone(decision["action"], reset_day)
            valid = strategy.evaluate({**ctx, "reset_day": 15})
            self.assertEqual(valid["action"], "ACTIVATE")
            self.assertEqual(valid["slot"], "A")

    def test_unknown_feedback_does_not_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            strategy = DayBasedStrategy(str(Path(tmp) / "day_based.json"), lambda: True)
            wings = {code: {"ems_enabled": True, "target_days": 0} for code in "ABCD"}
            wings["A"]["target_days"] = 30
            decision = strategy.evaluate({"operating_date": "2026-04-20", "reset_day": 15, "verified_active": "UNKNOWN", "wings": wings})
            self.assertIsNone(decision["action"])


class _BatchCursor:
    def __init__(self, commands, slots):
        self.commands, self.slots, self.bumps, self._one, self._rows = commands, slots, 0, None, []

    def execute(self, sql, params=()):
        q = " ".join(sql.lower().split())
        self._one, self._rows = None, []
        if "from pi_commands" in q and "group by" not in q:
            self._rows = [row for row in self.commands if row["allocation_batch_id"] == params[1]]
        elif "group by allocation_batch_id" in q:
            device, batch, this_max = params
            newer = []
            ids = {row["allocation_batch_id"] for row in self.commands if row["allocation_batch_id"] not in (None, batch)}
            for bid in ids:
                rows = [row for row in self.commands if row["allocation_batch_id"] == bid]
                if rows and all(row["status"] in ("completed", "acked") for row in rows) and max(row["sequence_no"] for row in rows) > this_max:
                    newer.append(1)
            self._one = {"ok": 1} if newer else None
        elif q.startswith("select target_days"):
            self._one = {"target_days": self.slots[params[1]]}
        elif q.startswith("update slot_configs"):
            self.slots[params[2]] = params[0]
        elif q.startswith("update societies"):
            self.bumps += 1

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one


def _batch_rows(batch, days, status, sequence):
    return [{"slot": wing, "params": {"days": days[wing]}, "status": status[wing], "sequence_no": sequence[wing], "allocation_batch_id": batch} for wing in days]


class BatchPublication(unittest.TestCase):
    def test_complete_batch_including_zero_replaces_previous_targets_once(self):
        commands = _batch_rows("new", {"A": 0, "B": 2, "C": 28}, {"A": "completed", "B": "acked", "C": "completed"}, {"A": 4, "B": 5, "C": 6})
        cur = _BatchCursor(commands, {"A": 6, "B": 12, "C": 12})
        self.assertTrue(DA.publish_completed_batch(cur, DID, 1, "new"))
        self.assertEqual(cur.slots, {"A": 0, "B": 2, "C": 28})
        self.assertEqual(cur.bumps, 1)
        self.assertFalse(DA.publish_completed_batch(cur, DID, 1, "new"))
        self.assertEqual(cur.bumps, 1)

    def test_expired_wing_does_not_publish_a_mixed_schedule(self):
        commands = _batch_rows("new", {"A": 0, "B": 2, "C": 28}, {"A": "completed", "B": "completed", "C": "expired"}, {"A": 4, "B": 5, "C": 6})
        cur = _BatchCursor(commands, {"A": 6, "B": 12, "C": 12})
        self.assertFalse(DA.publish_completed_batch(cur, DID, 1, "new"))
        self.assertEqual(cur.slots, {"A": 6, "B": 12, "C": 12})
        self.assertEqual(cur.bumps, 0)

    def test_same_idempotency_key_does_not_create_another_batch(self):
        # The apply route returns the existing batch before queue_set_days. This locks that gate.
        routes = (ROOT / "backend" / "energy_api" / "routes.py").read_text(encoding="utf-8")
        self.assertIn('f"{idem}:{enabled[0]}"', routes)
        self.assertIn('"duplicate": True', routes)

    def test_older_complete_batch_cannot_overwrite_a_newer_complete_batch(self):
        old = _batch_rows("old", {"A": 6, "B": 12, "C": 12}, {"A": "completed", "B": "completed", "C": "completed"}, {"A": 1, "B": 2, "C": 3})
        new = _batch_rows("new", {"A": 0, "B": 2, "C": 28}, {"A": "completed", "B": "acked", "C": "completed"}, {"A": 4, "B": 5, "C": 6})
        cur = _BatchCursor(old + new, {"A": 0, "B": 2, "C": 28})
        self.assertFalse(DA.publish_completed_batch(cur, DID, 1, "old"))
        self.assertEqual(cur.slots, {"A": 0, "B": 2, "C": 28})
        self.assertEqual(cur.bumps, 0)

    def test_manual_set_days_update_stays_outside_the_batch_gate(self):
        main = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        gate = main[main.index('if status == "completed"'):main.index('elif cmd["command"] == "set_reset_day"')]
        self.assertIn('if not cmd.get("allocation_batch_id")', gate)
        self.assertIn("UPDATE slot_configs SET target_days = %s", gate)
        self.assertIn("publish_completed_batch", main)


class DayFeedbackScope(unittest.TestCase):
    def engine(self, tmp, feedback, mode="DAY_BASED"):
        engine = EnergyEngine(tmp, feedback_provider=lambda: feedback, operating_date_provider=lambda: "2026-04-20",
                              reset_day_provider=lambda: 15, write_allowed=lambda: True, logger=logging.getLogger("day-feedback"))
        ok, err = engine.apply_config({"version": 4, "allocation_mode": mode, "bus": {}, "meters": {}, "allocation": {"enabled": False}, "targets": {}})
        self.assertTrue(ok, err)
        self.assertFalse(engine.registry["M1"].enabled)
        self.assertTrue(all(not engine.registry[mid].enabled for mid in ("M2", "M3", "M4", "M5")))
        return engine

    def wings(self, disabled="D"):
        return {code: {"ems_enabled": code != disabled, "target_days": {"A": 10, "B": 12, "C": 8, "D": 0}[code]} for code in "ABCD"}

    def test_disabled_unknown_feedback_does_not_block_enabled_wings(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.engine(tmp, {"A": "OFF", "B": "OFF", "C": "OFF", "D": "UNKNOWN"})
            decision = engine.evaluate_allocation("READY", self.wings())
            self.assertEqual(decision["action"], "ACTIVATE")
            self.assertEqual(decision["slot"], "A")
            self.assertEqual(engine._last_strategy, "day")
            engine.feedback = lambda: {"A": "ON", "B": "OFF", "C": "OFF", "D": "UNKNOWN"}
            engine.allocation_result("ACTIVATE", "A", True)
            self.assertEqual(engine.day_strategy.state["last_applied"], "A")

    def test_unknown_feedback_on_an_enabled_wing_still_blocks(self):
        for disabled, feedback in (
            ("D", {"A": "UNKNOWN", "B": "OFF", "C": "OFF", "D": "OFF"}),
            ("", {"A": "OFF", "B": "OFF", "C": "OFF", "D": "UNKNOWN"}),
            ("", {"A": "ON", "B": "ON", "C": "OFF", "D": "OFF"}),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                engine = self.engine(tmp, feedback)
                decision = engine.evaluate_allocation("READY", self.wings(disabled or " "))
                self.assertIsNone(decision["action"], feedback)

    def test_auto_still_uses_every_wing_and_the_allocation_policy(self):
        feedback = {"A": "OFF", "B": "OFF", "C": "OFF", "D": "UNKNOWN"}
        self.assertEqual(verified_active(feedback), "UNKNOWN")
        self.assertIsNone(verified_active(feedback, ("A", "B", "C")))
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.engine(tmp, feedback, mode="AUTO")
            decision = engine.evaluate_allocation("READY", self.wings())
            self.assertEqual(engine._last_strategy, "policy")
            self.assertIsNone(decision["action"])
            manual = self.engine(tmp, feedback, mode="MANUAL")
            self.assertIsNone(manual.evaluate_allocation("READY", self.wings())["action"])
            self.assertEqual(manual._last_strategy, "manual")


class ContractText(unittest.TestCase):
    def test_set_days_accepts_zero_without_a_new_command(self):
        main = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        controller = (ROOT / "pi_firmware" / "ems_controller.py").read_text(encoding="utf-8")
        routes = (ROOT / "backend" / "energy_api" / "routes.py").read_text(encoding="utf-8")
        policy = (ROOT / "pi_firmware" / "energy" / "allocation.py").read_text(encoding="utf-8")
        self.assertIn("days must be 0-31", main)
        self.assertNotIn("days must be 1-31", main)
        self.assertIn("return None if 0 <= days <= 31 else \"INVALID_TARGET_DAYS\"", controller)
        self.assertNotIn("set_units", routes)
        self.assertNotIn("DAY_BASED", routes)
        self.assertIn('if mode not in ("AUTO", "MANUAL")', routes)
        self.assertNotIn("DAY_BASED", policy)
        self.assertIn('"status": "APPLYING"', routes)
        self.assertNotIn('"status": "APPLIED"', routes)
        ack = main[main.index('if status == "completed"'):main.index('elif cmd["command"] == "set_reset_day"')]
        self.assertIn("UPDATE slot_configs SET target_days = %s", ack)
        self.assertIn("UPDATE societies SET config_version = config_version + 1", ack)
        dashboard = (ROOT / "frontend" / "src" / "components" / "ops" / "OperationalDashboard.tsx").read_text(encoding="utf-8")
        self.assertNotIn("<UnitAllotment", dashboard)
        self.assertIn("<DayAllocationPanel", dashboard)
        self.assertIn("renderDayControls({}, true)", dashboard)

    def test_zero_target_replaces_previous_target_in_canonical_config(self):
        slots = {code: {"disabled": False, "display_name": f"Slot {code}", "feedback_enabled": False, "target_days": 12} for code in "ABCD"}
        before = canonical_device_config("EMS-4CH-v1", False, 15, slots)
        self.assertEqual(before["slots"]["A"]["target_days"], 12)
        slots["A"]["target_days"] = 0
        after = canonical_device_config("EMS-4CH-v1", False, 15, slots)
        self.assertEqual(after["slots"]["A"]["target_days"], 0)


if __name__ == "__main__":
    unittest.main()
