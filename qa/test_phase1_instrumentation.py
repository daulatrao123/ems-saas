"""Phase 1 instrumentation. Observes the existing DAY_BASED software path.

This does not energize GPIO and does not prove a physical contactor moved.
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "pi_firmware"))
os.environ.setdefault("EMS_DEVICE_ID", "qa-device")
os.environ.setdefault("EMS_API_KEY", "qa-api-key")
os.environ.setdefault("EMS_API_BASE_URL", "http://127.0.0.1:8000/api")

import cloud_obs  # noqa: E402
from energy_api import day_allocation as DA  # noqa: E402
from energy_api.routes import create_router  # noqa: E402
from energy.day_based import DayBasedStrategy  # noqa: E402
import device_obs  # noqa: E402
import offline_queue  # noqa: E402

DID = "22222222-2222-2222-2222-222222222222"
SECRET = "phase1-trace-key-do-not-leak"


class _Cursor:
    def __init__(self, device):
        self.calls = []
        self.device = device
        self.sql = ""

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self.sql = sql

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
            return [{"slot": wing, "disabled": False} for wing in "ABCD"]
        return []


class _Conn:
    def __init__(self, device):
        self.cur = _Cursor(device)
        self.commits = 0

    def cursor(self, row_factory=None):
        return self

    def __enter__(self):
        return self.cur

    def __exit__(self, *args):
        return False

    def commit(self):
        self.commits += 1

    def rollback(self):
        return None

    def close(self):
        return None


class _BatchCursor:
    def __init__(self, commands, slots):
        self.commands, self.slots, self.bumps = commands, slots, 0
        self._one, self._rows = None, []

    def execute(self, sql, params=()):
        q = " ".join(sql.lower().split())
        self._one, self._rows = None, []
        if "from pi_commands" in q and "group by" not in q:
            self._rows = [row for row in self.commands if row["allocation_batch_id"] == params[1]]
        elif "group by allocation_batch_id" in q:
            self._one = None
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


class Instrumentation(unittest.TestCase):
    def test_four_wing_trace_and_bounded_secret_free_metrics(self):
        cloud_obs._counts.clear()
        cloud_obs._dims.clear()
        cloud_obs._ring.clear()
        self.assertEqual(DA.cycle_length(date(2026, 4, 20), 15), 30)
        days = {"A": 6, "B": 12, "C": 12, "D": 0}
        self.assertEqual(sum(DA.resolve_days("DAYS", days, list("ABCD"), 30).values()), 30)

        device = {"id": DID, "allocation_mode": "DAY_BASED", "allocation_mode_version": 1,
                  "reset_day": 15, "day_allocation": None, "next_command_sequence": 10}
        conn = _Conn(device)
        user = {"role": "society_admin", "society_id": 7, "sub": "admin"}
        router = create_router(lambda: conn, lambda: user, lambda *args: None, lambda value, _name: value)
        apply = next(route.endpoint for route in router.routes if getattr(route, "path", "") == "/api/energy/day-allocation/apply")
        calculate = next(route.endpoint for route in router.routes if getattr(route, "path", "") == "/api/energy/day-allocation/calculate")
        import energy_api.queries as queries
        original = queries.device_today
        queries.device_today = lambda *args, **kwargs: date(2026, 4, 20)
        try:
            calculated = calculate({"society_id": 7, "device_id": DID, "type": "DAYS", "wings": days}, user)
            applied = apply({"society_id": 7, "device_id": DID, "type": "DAYS", "wings": days, "idempotency_key": SECRET}, user)
        finally:
            queries.device_today = original
        self.assertEqual(calculated["days"], days)
        self.assertEqual(applied["status"], "APPLYING")
        self.assertEqual(conn.commits, 1)
        commands = applied["commands"]
        self.assertEqual([row["slot"] for row in commands], ["A", "B", "C", "D"])
        self.assertEqual([row["days"] for row in commands], [6, 12, 12, 0])

        batch = applied["batch_id"]
        rows = [{"slot": row["slot"], "params": {"days": row["days"]}, "status": "completed",
                 "sequence_no": row["sequence_no"], "allocation_batch_id": batch} for row in commands]
        cur = _BatchCursor(rows, {"A": 1, "B": 1, "C": 1, "D": 1})
        self.assertTrue(DA.publish_completed_batch(cur, DID, 7, batch))
        self.assertEqual(cur.slots, days)
        self.assertEqual(cur.bumps, 1)
        self.assertFalse(DA.publish_completed_batch(cur, DID, 7, batch))
        self.assertEqual(cur.bumps, 1)

        cloud_obs.finish_sync(DID, 7, 200, 0.01, True, {"statements": 12, "rows": {"pi_state": 1}}, {
            "command_id": commands[0]["command_id"], "command": "set_days", "slot": "A",
            "allocation_batch_id": batch, "sequence_no": commands[0]["sequence_no"],
        }, {"config_version": 4, "config_hash": "abc"})
        for hop in ("executing", "completed", "acked"):
            cloud_obs.record_ack({
                "device_id": DID, "society_id": 7, "command_id": commands[0]["command_id"],
                "command": "set_days", "slot": "A", "allocation_batch_id": batch,
                "previous_status": "delivered" if hop == "executing" else "executing" if hop == "completed" else "completed",
                "incoming_status": hop, "resulting_status": hop, "result_token": "CONFIG_ACCEPTED",
            }, True, 200, 0.01)
        cloud_obs.record_ack({
            "device_id": DID, "command_id": commands[0]["command_id"], "command": "set_days",
            "previous_status": "acked", "incoming_status": "acked", "resulting_status": "acked",
        }, False, 200, 0.001, idempotent=True)

        with tempfile.TemporaryDirectory() as tmp:
            strategy = DayBasedStrategy(str(Path(tmp) / "day_based.json"), lambda: True)
            before = device_obs._counts["persist_attempts"]
            strategy.evaluate({"operating_date": "2026-04-20", "reset_day": 15, "verified_active": None,
                               "wings": {wing: {"ems_enabled": True, "target_days": days[wing]} for wing in "ABCD"}})
            self.assertTrue(strategy.persist())
            self.assertGreater(device_obs._counts["persist_attempts"], before)
            unchanged = device_obs._counts["persist_state_unchanged"]
            strategy.persist()
            self.assertGreater(device_obs._counts["persist_state_unchanged"], unchanged)
            device_obs.config_apply(DID, 4, 3, "newhash", "oldhash", "changed", None, True, 0.002)
            self.assertEqual(device_obs._counts["config_apply_changed"], device_obs._counts["config_apply_changed"])

        kinds = [row["kind"] for row in cloud_obs.snapshot()["recent"]]
        for kind in ("allocation_calculate", "allocation_apply", "batch_publication", "sync", "ack"):
            self.assertIn(kind, kinds)
        published = [row for row in cloud_obs.snapshot()["recent"] if row["kind"] == "batch_publication" and row.get("published")]
        self.assertEqual(published[-1]["targets"][0]["new_target_days"], 6)
        self.assertTrue(published[-1]["config_version_incremented"])
        self.assertEqual(published[-1]["config_version_before"], "UNKNOWN")
        blob = json.dumps(cloud_obs.snapshot())
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("api_key", blob.lower())
        for label in list(cloud_obs._dims.get("command_delivery_by_type", {})):
            self.assertIn(label, cloud_obs.COMMANDS)

        cloud_obs.finish_sync(DID, 7, 500, 0.01, False, {"statements": 4, "rows": {"pi_state": 2}}, None, None)
        failed = [row for row in cloud_obs.snapshot()["recent"] if row["kind"] == "sync" and row["http_status"] == 500][-1]
        self.assertEqual(failed["transaction"], "rolled_back")
        self.assertEqual(failed["rows_committed"], {})
        self.assertEqual(failed["rows_attempted"]["pi_state"], 2)

    def test_claim_next_empty_and_real_are_counted_without_changing_claim(self):
        device_obs._counts["claim_next_total"] = 0
        device_obs._counts["claim_next_empty"] = 0
        device_obs._counts["claim_next_command"] = 0
        device_obs._counts["claim_next_commit"] = 0
        device_obs._counts["claim_next_rollback"] = 0

        class Storage:
            def is_write_allowed(self, _category):
                return True

        with tempfile.TemporaryDirectory() as tmp:
            offline_queue.DB_FILE = str(Path(tmp) / "queue.sqlite")
            queue = offline_queue.OfflineQueue(Storage())
            self.assertIsNone(queue.claim_next())
            self.assertEqual(device_obs._counts["claim_next_empty"], 1)
            self.assertEqual(device_obs._counts["claim_next_commit"], 1)
            self.assertEqual(device_obs._counts["claim_next_command"], 0)
            self.assertTrue(queue.add_command("cmd-1", "A", "ACTIVATE", "2026-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00"))
            self.assertEqual(queue.claim_next(), ("cmd-1", "A", "ACTIVATE"))
            self.assertEqual(device_obs._counts["claim_next_command"], 1)
            self.assertEqual(queue.claim_next(), None)
            queue.close()

    def test_statement_counter_does_not_rewrite_sql(self):
        seen = []

        class Cur:
            def execute(self, query, params=None, *, prepare=None, binary=None):
                seen.append((query, params))
                self.rowcount = 1
                return self

        cur = Cur()
        stats = cloud_obs.attach_statement_counter(cur)
        cur.execute("UPDATE pi_devices SET last_seen = %s WHERE id = %s", ("t", "d"))
        cur.execute("SELECT 1")
        self.assertEqual(stats["statements"], 2)
        self.assertEqual(stats["rows"]["pi_devices"], 1)
        self.assertEqual(seen[0][0], "UPDATE pi_devices SET last_seen = %s WHERE id = %s")
        self.assertNotIn("SELECT", stats["rows"])

    def test_observer_failures_do_not_change_operations(self):
        def boom(*args, **kwargs):
            raise RuntimeError("observer failed")

        commands = _batch_rows = [
            {"slot": wing, "params": {"days": days}, "status": "completed", "sequence_no": index, "allocation_batch_id": "batch"}
            for index, (wing, days) in enumerate({"A": 6, "B": 12, "C": 12, "D": 0}.items(), start=1)
        ]
        cur = _BatchCursor(commands, {"A": 1, "B": 1, "C": 1, "D": 1})
        original_batch = cloud_obs.note_batch
        cloud_obs.note_batch = boom
        try:
            self.assertTrue(DA.publish_completed_batch(cur, DID, 7, "batch"))
        finally:
            cloud_obs.note_batch = original_batch
        self.assertEqual(cur.slots, {"A": 6, "B": 12, "C": 12, "D": 0})
        self.assertEqual(cur.bumps, 1)
        self.assertGreater(cloud_obs._counts.get("observation_errors_total", 0), 0)

        class Conn:
            def __init__(self):
                self.closed = False
                self.commits = 0
                self.rollbacks = 0

            def commit(self):
                self.commits += 1

            def rollback(self):
                self.rollbacks += 1

            def close(self):
                self.closed = True

        def sync_like(conn):
            reply = {"success": True, "command": None}
            try:
                conn.commit()
                return reply
            finally:
                try:
                    cloud_obs.safe_observe("sync", boom)
                    cloud_obs.safe_observe("pi_report", boom)
                except Exception:
                    cloud_obs.note_observation_error("sync")
                conn.close()

        conn = Conn()
        self.assertEqual(sync_like(conn)["success"], True)
        self.assertEqual(conn.commits, 1)
        self.assertEqual(conn.rollbacks, 0)
        self.assertTrue(conn.closed)

        def ack_like(conn):
            try:
                conn.commit()
                return {"success": True, "status": "acked"}
            except Exception:
                conn.rollback()
                raise
            finally:
                try:
                    cloud_obs.safe_observe("ack", boom)
                except Exception:
                    cloud_obs.note_observation_error("ack")
                conn.close()

        ack_conn = Conn()
        self.assertEqual(ack_like(ack_conn)["status"], "acked")
        self.assertEqual(ack_conn.commits, 1)
        self.assertTrue(ack_conn.closed)

        def failed_ack(conn):
            try:
                raise RuntimeError("application")
            except Exception:
                conn.rollback()
                raise
            finally:
                cloud_obs.safe_observe("ack", boom)
                conn.close()

        failed = Conn()
        with self.assertRaisesRegex(RuntimeError, "application"):
            failed_ack(failed)
        self.assertEqual(failed.rollbacks, 1)
        self.assertTrue(failed.closed)

        class Storage:
            def is_write_allowed(self, _category):
                return True

        with tempfile.TemporaryDirectory() as tmp:
            offline_queue.DB_FILE = str(Path(tmp) / "queue.sqlite")
            queue = offline_queue.OfflineQueue(Storage())
            original_empty = device_obs.claim_empty
            device_obs.claim_empty = boom
            try:
                self.assertIsNone(queue.claim_next())
            finally:
                device_obs.claim_empty = original_empty
            original_command = device_obs.claim_command
            device_obs.claim_command = boom
            try:
                self.assertTrue(queue.add_command("cmd-obs", "B", "ACTIVATE", "2026-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00"))
                self.assertEqual(queue.claim_next(), ("cmd-obs", "B", "ACTIVATE"))
                status = queue.conn.execute("SELECT status FROM commands WHERE id=?", ("cmd-obs",)).fetchone()[0]
                self.assertEqual(status, "EXECUTING")
            finally:
                device_obs.claim_command = original_command
            queue.close()

        with tempfile.TemporaryDirectory() as tmp:
            strategy = DayBasedStrategy(str(Path(tmp) / "day_based.json"), lambda: True)
            original_persist = device_obs.note_persist
            device_obs.note_persist = boom
            try:
                decision = strategy.evaluate({
                    "operating_date": "2026-04-20", "reset_day": 15, "verified_active": None,
                    "wings": {wing: {"ems_enabled": True, "target_days": {"A": 6, "B": 12, "C": 12, "D": 0}[wing]} for wing in "ABCD"},
                })
                self.assertTrue(strategy.persist())
            finally:
                device_obs.note_persist = original_persist
            self.assertEqual(decision["action"], "ACTIVATE")
            self.assertTrue((Path(tmp) / "day_based.json").is_file())

        for component in ("sync", "ack", "batch", "pi_report", "queue", "day_based"):
            self.assertIsNone(cloud_obs.safe_observe(component, boom))
            self.assertIsNone(device_obs.safe_observe(component, boom))
        self.assertGreaterEqual(cloud_obs._counts["observation_errors_total"], 6)
        self.assertGreaterEqual(device_obs._counts["observation_errors_total"], 6)
        labels = set(cloud_obs._dims.get("observation_errors_by_component", {}))
        self.assertTrue(labels <= cloud_obs.COMPONENTS)

        main = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        self.assertIn("safe_observe", main[main.index("cloud_obs.finish_sync(") - 120:main.index("cloud_obs.finish_sync(")])
        self.assertIn("safe_observe", main[main.index("cloud_obs.record_ack(") - 120:main.index("cloud_obs.record_ack(")])


if __name__ == "__main__":
    unittest.main()
