"""Phase 2A software-command recovery. No GPIO, no live database, no firmware import."""
import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi import HTTPException

import cloud_obs
import command_reliability
from energy_api import day_allocation as DA

DID = "48a45eef-cc6b-4452-b0a6-33990b9b22ee"
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
A_ID = "11111111-1111-1111-1111-111111111111"
B_ID = "22222222-2222-2222-2222-222222222222"
C_ID = "33333333-3333-3333-3333-333333333333"
D_ID = "44444444-4444-4444-4444-444444444444"
OTHER_ID = "55555555-5555-5555-5555-555555555555"


def _ack_function():
    tree = ast.parse((ROOT / "backend" / "main.py").read_text(encoding="utf-8"))
    wanted = {"resolve_ack_path", "require_uuid", "pi_command_ack"}
    constants = {"COMMAND_TRANSITIONS", "ACK_STATUS_RANK", "SOFTWARE_RESULTS", "POSITIVE_VERIFICATION", "SLOTS", "DEFAULT_RESET_DAY"}
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            if node.name == "pi_command_ack":
                node.decorator_list = []
            body.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in constants for target in node.targets):
            body.append(node)
    ns = {
        "HTTPException": HTTPException,
        "datetime": datetime,
        "timezone": timezone,
        "time": __import__("time"),
        "uuid": __import__("uuid"),
        "cloud_obs": cloud_obs,
        "command_reliability": command_reliability,
        "publish_completed_batch": DA.publish_completed_batch,
        "Request": object,
        "Header": lambda *a, **k: None,
        "authenticate_pi": lambda *a: (DID, 7),
        "get_db": lambda: None,
        "dict_row": None,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), "offline_ack", "exec"), ns)
    return ns["pi_command_ack"]


class Store:
    def __init__(self):
        self.commands = {}
        self.slots = {wing: 1 for wing in "ABCD"}
        self.config_version = 4
        self.slot_writes = 0


class AckCursor:
    def __init__(self, store):
        self.store = store
        self.one = None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def fetchone(self):
        return self.one

    def fetchall(self):
        return []

    def execute(self, sql, params=()):
        query = " ".join(sql.lower().split())
        self.one = None
        self.rowcount = 0
        if query.startswith("select id, command, slot, params, status"):
            row = self.store.commands.get(str(params[0]))
            self.one = deepcopy(row) if row and row["device_id"] == params[1] else None
            return
        if "sequence_no >" in query and "command='set_days'" in query:
            device_id, slot, sequence_no = params
            self.one = next((
                {"stale": 1} for row in self.store.commands.values()
                if row["device_id"] == device_id and row["command"] == "set_days" and row["slot"] == slot
                and isinstance(row.get("sequence_no"), int) and row["sequence_no"] > sequence_no
                and row["status"] in ("completed", "acked")
            ), None)
            return
        if query.startswith("update slot_configs set target_days"):
            _days, _device, slot = params[0], params[1], params[2]
            self.store.slots[slot] = params[0]
            self.store.slot_writes += 1
            self.rowcount = 1
            return
        if query.startswith("update societies set config_version"):
            self.store.config_version += 1
            self.rowcount = 1
            return
        if query.startswith("update pi_commands set status="):
            new_status, expected = params[0], params[-1]
            cmd_id = next(str(value) for value in reversed(params) if str(value) in self.store.commands)
            row = self.store.commands[cmd_id]
            if row["status"] != expected:
                self.rowcount = 0
                return
            row["status"] = new_status
            row["result"] = params[2]
            self.rowcount = 1


class AckConnection:
    def __init__(self, store):
        self.store = store
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self, **_kwargs):
        return AckCursor(self.store)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def _command(cmd_id, command, slot, status, sequence_no, days=None, batch=None, attempt=1):
    return {
        "id": cmd_id,
        "device_id": DID,
        "command": command,
        "slot": slot,
        "params": {"days": days} if days is not None else {},
        "status": status,
        "attempt_count": attempt,
        "allocation_batch_id": batch,
        "sequence_no": sequence_no,
    }


class RecoveryCursor:
    def __init__(self, commands):
        self.commands = commands
        self.rows = []
        self.sql = ""

    def execute(self, sql, params=()):
        self.sql = sql
        device_id, now, allowed = params
        self.rows = []
        for row in self.commands:
            if row["device_id"] != device_id or row["status"] != "executing":
                continue
            if row["expires_at"] > now or row["command"] not in allowed:
                continue
            row["status"] = "expired"
            row["error"] = "SOFTWARE_EXECUTING_EXPIRED"
            self.rows.append(dict(row))

    def fetchall(self):
        return self.rows


class Phase2ATests(unittest.TestCase):
    def setUp(self):
        self.ack = _ack_function()
        cloud_obs._counts.clear()
        cloud_obs._ring.clear()

    def _ack(self, store, cmd_id, status, verification="CONFIG_ACCEPTED"):
        conn = AckConnection(store)
        self.ack.__globals__["get_db"] = lambda: conn
        result = self.ack(None, {"command_id": cmd_id, "status": status, "verification_state": verification, "attempt": 1}, None, None)
        return result, conn

    def test_lost_completed_ack_expires_without_publishing_or_gpio(self):
        past = NOW - timedelta(seconds=1)
        future = NOW + timedelta(seconds=30)
        commands = [_command(A_ID, "set_days", "A", "executing", 10, days=9)]
        commands[0]["expires_at"] = future
        held = RecoveryCursor(commands)
        self.assertEqual(command_reliability.expire_stuck_software_commands(held, DID, NOW), [])
        self.assertEqual(commands[0]["status"], "executing")
        commands[0]["expires_at"] = past
        expired = command_reliability.expire_stuck_software_commands(RecoveryCursor(commands), DID, NOW)
        self.assertEqual(commands[0]["status"], "expired")
        self.assertEqual(commands[0]["error"], "SOFTWARE_EXECUTING_EXPIRED")
        self.assertEqual(expired[0]["command"], "set_days")
        self.assertNotIn("gpio", command_reliability.expire_stuck_software_commands.__code__.co_names)
        again = command_reliability.expire_stuck_software_commands(RecoveryCursor(commands), DID, NOW)
        self.assertEqual(again, [])
        self.assertEqual(commands[0]["status"], "expired")

    def test_duplicate_completed_ack_writes_target_days_once(self):
        store = Store()
        store.commands[A_ID] = _command(A_ID, "set_days", "A", "executing", 10, days=9)
        first, conn = self._ack(store, A_ID, "completed")
        self.assertEqual(first["status"], "completed")
        self.assertEqual(conn.commits, 1)
        self.assertEqual(store.slots["A"], 9)
        self.assertEqual(store.slot_writes, 1)
        second, conn2 = self._ack(store, A_ID, "completed")
        self.assertEqual(second["idempotent"], True)
        self.assertEqual(second["status"], "completed")
        self.assertEqual(store.slot_writes, 1)
        self.assertEqual(store.config_version, 5)
        self.assertEqual(conn2.rollbacks, 0)
        self.assertTrue(conn2.closed)

    def test_older_set_days_cannot_overwrite_newer_target(self):
        store = Store()
        store.commands[B_ID] = _command(B_ID, "set_days", "A", "executing", 11, days=12)
        store.commands[A_ID] = _command(A_ID, "set_days", "A", "executing", 10, days=9)
        self._ack(store, B_ID, "completed")
        self.assertEqual(store.slots["A"], 12)
        late, _conn = self._ack(store, A_ID, "completed")
        self.assertEqual(late["status"], "completed")
        self.assertEqual(store.slots["A"], 12)
        self.assertEqual(store.slot_writes, 1)

    def test_newer_set_days_updates_after_older(self):
        store = Store()
        store.commands[A_ID] = _command(A_ID, "set_days", "A", "executing", 10, days=9)
        store.commands[B_ID] = _command(B_ID, "set_days", "A", "executing", 11, days=12)
        self._ack(store, A_ID, "completed")
        self._ack(store, B_ID, "completed")
        self.assertEqual(store.slots["A"], 12)
        self.assertEqual(store.slot_writes, 2)

    def test_incomplete_day_based_batch_does_not_publish(self):
        rows = [
            {"slot": "A", "params": {"days": 9}, "status": "completed", "sequence_no": 1, "allocation_batch_id": "batch"},
            {"slot": "B", "params": {"days": 12}, "status": "completed", "sequence_no": 2, "allocation_batch_id": "batch"},
            {"slot": "C", "params": {"days": 9}, "status": "executing", "sequence_no": 3, "allocation_batch_id": "batch"},
            {"slot": "D", "params": {"days": 0}, "status": "completed", "sequence_no": 4, "allocation_batch_id": "batch"},
        ]
        slots = {"A": 6, "B": 12, "C": 12, "D": 0}

        class Cur:
            def __init__(self):
                self.one = None
                self.rows = []
                self.bumps = 0
                self.slots = dict(slots)

            def execute(self, sql, params=()):
                query = " ".join(sql.lower().split())
                self.one, self.rows = None, []
                if "from pi_commands" in query and "group by" not in query:
                    self.rows = rows
                elif query.startswith("update slot_configs"):
                    raise AssertionError("partial batch must not write target_days")
                elif query.startswith("update societies"):
                    self.bumps += 1

            def fetchall(self):
                return self.rows

            def fetchone(self):
                return self.one

        cur = Cur()
        self.assertFalse(DA.publish_completed_batch(cur, DID, 7, "batch"))
        self.assertEqual(cur.slots, slots)
        self.assertEqual(cur.bumps, 0)

    def test_stuck_batch_member_does_not_block_unrelated_queued_command(self):
        future = NOW + timedelta(minutes=5)
        commands = [
            {**_command(C_ID, "set_days", "C", "executing", 3, days=9, batch="batch"), "expires_at": future},
            {**_command(OTHER_ID, "set_reset_day", None, "queued", 8), "expires_at": future},
        ]
        command_reliability.expire_stuck_software_commands(RecoveryCursor(commands), DID, NOW)
        self.assertEqual(commands[0]["status"], "executing")
        queued = [row for row in commands if row["status"] == "queued"]
        self.assertEqual(queued[0]["id"], OTHER_ID)
        lease = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        self.assertIn("status = 'queued' ORDER BY sequence_no ASC", lease)
        self.assertIn("expire_stuck_software_commands", lease)

    def test_hardware_executing_is_not_requeued(self):
        past = NOW - timedelta(seconds=5)
        commands = []
        for cmd_id, command in (
            (A_ID, "set_active_slot"),
            (B_ID, "off_slot"),
            (C_ID, "off_all"),
        ):
            commands.append({**_command(cmd_id, command, "A", "executing", 1), "expires_at": past})
        recovered = command_reliability.expire_stuck_software_commands(RecoveryCursor(commands), DID, NOW)
        self.assertEqual(recovered, [])
        self.assertTrue(all(row["status"] == "executing" for row in commands))
        self.assertTrue(set(command_reliability.HARDWARE_COMMANDS).isdisjoint(command_reliability.SOFTWARE_COMMANDS))
        source = (ROOT / "backend" / "command_reliability.py").read_text(encoding="utf-8")
        self.assertNotIn("status='queued'", source)
        self.assertNotIn("gpio", source.lower())

    def test_duplicate_ack_order_is_idempotent(self):
        store = Store()
        store.commands[A_ID] = _command(A_ID, "set_days", "B", "delivered", 4, days=6)
        self._ack(store, A_ID, "executing", "PENDING")
        self.assertEqual(store.commands[A_ID]["status"], "executing")
        self.assertEqual(store.slot_writes, 0)
        done, _conn = self._ack(store, A_ID, "completed")
        again, _conn = self._ack(store, A_ID, "completed")
        replay, _conn = self._ack(store, A_ID, "executing", "PENDING")
        self.assertEqual(done["status"], "completed")
        self.assertTrue(again["idempotent"])
        self.assertTrue(replay["idempotent"])
        self.assertEqual(store.commands[A_ID]["status"], "completed")
        self.assertEqual(store.slots["B"], 6)
        self.assertEqual(store.slot_writes, 1)
        acked, _conn = self._ack(store, A_ID, "acked", "CONFIG_ACCEPTED")
        self.assertEqual(acked["status"], "acked")
        repeated, _conn = self._ack(store, A_ID, "acked", "CONFIG_ACCEPTED")
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(store.slot_writes, 1)

    def test_recovery_event_does_not_escape(self):
        past = NOW - timedelta(seconds=1)
        commands = [{**_command(A_ID, "set_days", "A", "executing", 2, days=9), "expires_at": past, "allocation_batch_id": "batch"}]
        original = cloud_obs.note_software_recovery
        cloud_obs.note_software_recovery = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("observer failed"))
        try:
            command_reliability.expire_stuck_software_commands(RecoveryCursor(commands), DID, NOW)
        finally:
            cloud_obs.note_software_recovery = original
        self.assertEqual(commands[0]["status"], "expired")
        self.assertGreater(cloud_obs._counts.get("observation_errors_total", 0), 0)


if __name__ == "__main__":
    unittest.main()
