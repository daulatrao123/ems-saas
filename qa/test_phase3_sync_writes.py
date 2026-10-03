"""Phase 3: identical slot rows and an unchanged firmware string do not rewrite those values."""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("EMS_DEVICE_ID", "qa-device")
os.environ.setdefault("EMS_API_KEY", "qa-api-key")
os.environ.setdefault("EMS_API_BASE_URL", "http://127.0.0.1:8000/api")

from qa.test_cloud_limits_storage import SyncConnection, energy_payload, fixture, sync_function

DID = "11111111-1111-1111-1111-111111111111"
SLOT_FIELDS = ("physical_toggle", "toggle_input", "used_days", "clicks")


def _distinct(left, right):
    return left != right


class SlotCursor:
    """Applies the PostgreSQL ON CONFLICT DO UPDATE WHERE IS DISTINCT FROM rule."""

    def __init__(self, store):
        self.store = store
        self.slot_updates = 0
        self.slot_inserts = 0
        self.firmware_assignments = 0
        self.last_seen_assignments = 0

    def execute(self, sql, params=()):
        query = " ".join(sql.lower().split())
        if query.startswith("insert into slot_state"):
            if "is distinct from" not in query:
                raise AssertionError("slot_state update is unconditional")
            key = (params[0], params[1])
            incoming = dict(zip(SLOT_FIELDS, params[2:6]))
            current = self.store["slots"].get(key)
            if current is None:
                self.store["slots"][key] = incoming
                self.slot_inserts += 1
            elif any(_distinct(current[name], incoming[name]) for name in SLOT_FIELDS):
                current.update(incoming)
                self.slot_updates += 1
            return
        if query.startswith("update pi_devices set last_seen"):
            if "firmware_version is distinct from" not in query:
                raise AssertionError("firmware_version is assigned unconditionally")
            device = self.store["device"]
            device["last_seen"] = params[0]
            self.last_seen_assignments += 1
            if _distinct(device.get("firmware_version"), params[1]):
                device["firmware_version"] = params[1]
                self.firmware_assignments += 1
            return
        raise AssertionError(query)


def _slot_sql():
    source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    marker = "INSERT INTO slot_state (device_id, slot, physical_toggle"
    start = source.index(marker)
    return " ".join(source[start:source.index('"""', start)].split())


def _device_sql():
    source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    start = source.index("UPDATE pi_devices SET last_seen")
    return " ".join(source[start:source.index('"""', start)].split())


class SyncWriteTests(unittest.TestCase):
    def test_sql_guards_match_the_stored_business_columns(self):
        slot_sql = _slot_sql()
        for column in SLOT_FIELDS:
            self.assertIn(f"slot_state.{column} IS DISTINCT FROM EXCLUDED.{column}", slot_sql)
        device_sql = _device_sql()
        self.assertIn("SET last_seen = %s", device_sql)
        self.assertIn("WHEN firmware_version IS DISTINCT FROM %s THEN %s ELSE firmware_version END", device_sql)
        report = (ROOT / "backend" / "energy_api" / "target_delivery.py").read_text(encoding="utf-8")
        self.assertIn("reported_at=EXCLUDED.reported_at", report)
        sync = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        self.assertIn("last_sync=EXCLUDED.last_sync", sync)
        self.assertIn("UPDATE pi_commands SET status='delivered'", sync)

    def test_identical_slot_values_do_not_create_a_new_row_version(self):
        store = {"slots": {}, "device": {"firmware_version": "7.0.0", "last_seen": None}}
        cur = SlotCursor(store)
        first = ("dev", "A", "OFF", "UNKNOWN", 3, 1)
        cur.execute(_slot_sql(), first)
        self.assertEqual(cur.slot_inserts, 1)
        cur.execute(_slot_sql(), first)
        self.assertEqual(cur.slot_updates, 0)
        cur.execute(_slot_sql(), ("dev", "A", "ON", "UNKNOWN", 3, 1))
        self.assertEqual(cur.slot_updates, 1)
        self.assertEqual(store["slots"][("dev", "A")]["physical_toggle"], "ON")
        cur.execute(_slot_sql(), ("dev", "A", "ON", "UNKNOWN", 4, 1))
        self.assertEqual(cur.slot_updates, 2)
        self.assertEqual(store["slots"][("dev", "A")]["used_days"], 4)

    def test_last_seen_moves_and_firmware_changes_only_when_different(self):
        store = {"slots": {}, "device": {"firmware_version": "7.0.0", "last_seen": None}}
        cur = SlotCursor(store)
        sql = _device_sql()
        cur.execute(sql, ("t1", "7.0.0", "7.0.0", DID))
        cur.execute(sql, ("t2", "7.0.0", "7.0.0", DID))
        self.assertEqual(store["device"]["last_seen"], "t2")
        self.assertEqual(store["device"]["firmware_version"], "7.0.0")
        self.assertEqual(cur.last_seen_assignments, 2)
        self.assertEqual(cur.firmware_assignments, 0)
        cur.execute(sql, ("t3", "7.1.0", "7.1.0", DID))
        self.assertEqual(store["device"]["firmware_version"], "7.1.0")
        self.assertEqual(store["device"]["last_seen"], "t3")
        self.assertEqual(cur.firmware_assignments, 1)

    def test_successful_sync_still_commits_once_and_failure_rolls_back(self):
        conn = SyncConnection(fixture())
        result = sync_function(conn)(None, {"energy": energy_payload(False), "slots": {wing: {} for wing in "ABCD"}, "firmwareVersion": "7.0.0"})
        self.assertTrue(result["success"])
        self.assertEqual(conn.commits, 1)
        self.assertEqual(conn.rollbacks, 0)
        slot_sql = [query for query, _params in conn.trace if query.startswith("insert into slot_state")]
        self.assertEqual(len(slot_sql), 4)
        self.assertTrue(all("is distinct from" in query for query in slot_sql))
        device_sql = [query for query, _params in conn.trace if query.startswith("update pi_devices set last_seen")]
        self.assertEqual(len(device_sql), 1)
        self.assertIn("firmware_version is distinct from", device_sql[0])

        failed = SyncConnection(fixture(), fail_commit=True)
        with self.assertRaises(RuntimeError):
            sync_function(failed)(None, {"energy": energy_payload(False), "slots": {wing: {} for wing in "ABCD"}})
        self.assertEqual(failed.commits, 0)
        self.assertEqual(failed.rollbacks, 1)


if __name__ == "__main__":
    unittest.main()
