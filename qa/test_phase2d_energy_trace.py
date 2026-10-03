"""Phase 2D energy-config trace. Observation only. No live database and no GPIO."""
from datetime import date, datetime, timezone
import logging
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "pi_firmware"))

import cloud_obs
import device_obs
from energy.meter_manager import EnergyEngine
from energy_api import ingest
from energy_api.target_delivery import record_report
from qa.test_cloud_limits_storage import SyncConnection, fixture

DID = "48a45eef-cc6b-4452-b0a6-33990b9b22ee"
NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)


def _events(kind):
    return [row for row in cloud_obs.snapshot()["recent"] if row.get("kind") == kind]


class ReportCursor:
    def __init__(self, previous=None):
        self.previous = previous
        self.stored = "unset"
        self.stamp = None

    def execute(self, sql, params=()):
        query = " ".join(sql.split())
        if query.startswith("SELECT reported_config_version FROM"):
            self.one = {"reported_config_version": self.previous}
            return
        if query.startswith("INSERT INTO energy_sync_state"):
            self.stored = params[1]
            self.stamp = params[2]
            self.previous = params[1]
            return
        raise AssertionError(query)

    def fetchone(self):
        return self.one


class EnergyTraceTests(unittest.TestCase):
    def setUp(self):
        cloud_obs._counts.clear()
        cloud_obs._dims.clear()
        cloud_obs._ring.clear()
        device_obs._counts["energy_config_received_total"] = 0
        device_obs._counts["energy_config_accepted_total"] = 0
        device_obs._counts["energy_config_rejected_total"] = 0
        device_obs._last_energy_received = None
        device_obs._last_energy_report = None

    def _device(self, version=3, open_day=None):
        db = fixture()
        db["devices"][DID] = dict(db["devices"][list(db["devices"])[0]])
        db["devices"][DID]["id"] = DID
        db["devices"][DID]["energy_config_version"] = version
        db["meters"][DID] = db["meters"][list(db["meters"])[0]]
        if open_day is not None:
            db["daily"][(DID, open_day)] = {"status": "OPEN"}
        return SyncConnection(db)

    def test_version_3_is_attached_when_report_is_missing(self):
        conn = self._device(3)
        cfg = ingest.config_reply(conn.cursor(), DID, {"schema": 1}, NOW, society_id=7)
        self.assertEqual(cfg["version"], 3)
        event = _events("energy_config_delivery")[-1]
        self.assertEqual(event["desired_version"], 3)
        self.assertIsNone(event["reported_version"])
        self.assertTrue(event["attached"])
        self.assertEqual(event["reason"], "REPORT_MISSING")
        stored = _events("energy_config_report_stored")[-1]
        self.assertEqual(stored["received_category"], "missing")
        self.assertFalse(stored["accepted"])
        self.assertEqual(stored["reason"], "NON_INTEGER_OR_MISSING")
        self.assertIsNone(stored["resulting_reported_version"])

    def test_matching_report_does_not_attach(self):
        conn = self._device(3)
        cfg = ingest.config_reply(conn.cursor(), DID, {"config_version": 3}, NOW, society_id=7)
        self.assertIsNone(cfg)
        event = _events("energy_config_delivery")[-1]
        self.assertFalse(event["attached"])
        self.assertEqual(event["reason"], "ALREADY_REPORTED")
        self.assertEqual(event["desired_version"], 3)

    def test_operating_day_failure_records_withhold_reason(self):
        conn = self._device(3, open_day=date(2099, 1, 1))
        cfg = ingest.config_reply(conn.cursor(), DID, {"config_version": None}, NOW, society_id=7)
        self.assertIsNone(cfg)
        event = _events("energy_config_delivery")[-1]
        self.assertFalse(event["attached"])
        self.assertEqual(event["reason"], "OPERATING_DAY")
        self.assertEqual(event["desired_version"], 3)

    def test_pi_accepts_version_3_and_snapshot_reports_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = EnergyEngine(tmp, lambda: {}, lambda: "2026-10-03", lambda: 15, lambda: True, logging.getLogger("phase2d"))
            ok, err = engine.apply_config({"version": 3, "bus": {}, "meters": {}})
            self.assertTrue(ok, err)
            self.assertEqual(engine.config_version, 3)
            received = device_obs.snapshot()["last_energy_config_received"]
            self.assertEqual(received["validation"], "accepted")
            self.assertTrue(received["applied"])
            self.assertEqual(received["version"], 3)
            snap = engine.snapshot()
            self.assertEqual(snap["config_version"], 3)
            report = device_obs.snapshot()["last_energy_config_report"]
            self.assertEqual(report["config_version"], 3)
            self.assertTrue(report["valid_integer"])

    def test_non_object_config_does_not_replace_previous_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = EnergyEngine(tmp, lambda: {}, lambda: "2026-10-03", lambda: 15, lambda: True, logging.getLogger("phase2d"))
            engine.config_version = 2
            ok, err = engine.apply_config(["not", "a", "config"])
            self.assertFalse(ok)
            self.assertEqual(engine.config_version, 2)
            self.assertEqual(engine.snapshot()["config_version"], 2)
            received = device_obs.snapshot()["last_energy_config_received"]
            self.assertEqual(received["validation"], "rejected")
            self.assertEqual(received["reason"], "NOT_OBJECT")
            self.assertFalse(received["applied"])
            self.assertFalse(received["persisted"])

    def test_invalid_registry_does_not_report_the_rejected_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = EnergyEngine(tmp, lambda: {}, lambda: "2026-10-03", lambda: 15, lambda: True, logging.getLogger("phase2d"))
            engine.config_version = 2
            ok, err = engine.apply_config({"version": 9, "meters": {"M9": {}}})
            self.assertFalse(ok)
            self.assertEqual(engine.config_version, 2)
            self.assertEqual(engine.snapshot()["config_version"], 2)
            received = device_obs.snapshot()["last_energy_config_received"]
            self.assertEqual(received["validation"], "rejected")
            self.assertEqual(received["reason"], "REGISTRY_REJECTED")
            self.assertFalse(received["applied"])
            report = device_obs.snapshot()["last_energy_config_report"]
            self.assertEqual(report["config_version"], 2)
            self.assertTrue(report["valid_integer"])

    def test_null_snapshot_version_stays_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = EnergyEngine(tmp, lambda: {}, lambda: "2026-10-03", lambda: 15, lambda: True, logging.getLogger("phase2d"))
            self.assertIsNone(engine.config_version)
            snap = engine.snapshot()
            self.assertIsNone(snap["config_version"])
            report = device_obs.snapshot()["last_energy_config_report"]
            self.assertIsNone(report["config_version"])
            self.assertFalse(report["valid_integer"])

    def test_record_report_stores_only_integers(self):
        cur = ReportCursor(previous=None)
        self.assertEqual(record_report(cur, DID, {"config_version": 3}, NOW, society_id=7), 3)
        self.assertEqual(cur.stored, 3)
        stored = _events("energy_config_report_stored")[-1]
        self.assertEqual(stored["previous_reported_version"], None)
        self.assertEqual(stored["resulting_reported_version"], 3)
        self.assertEqual(stored["reason"], "ACCEPTED")
        self.assertIsNone(record_report(cur, DID, {"config_version": None}, NOW, society_id=7))
        self.assertIsNone(cur.stored)
        rejected = _events("energy_config_report_stored")[-1]
        self.assertEqual(rejected["previous_reported_version"], 3)
        self.assertIsNone(rejected["resulting_reported_version"])
        self.assertEqual(rejected["reason"], "NON_INTEGER_OR_MISSING")
        self.assertIsNone(record_report(cur, DID, {"config_version": True}, NOW, society_id=7))
        self.assertIsNone(cur.stored)
        self.assertEqual(_events("energy_config_report_stored")[-1]["received_category"], "bool")

    def test_observer_failures_do_not_change_operations(self):
        def boom(*_args, **_kwargs):
            raise RuntimeError("observer failed")

        original = cloud_obs.note_energy_delivery
        cloud_obs.note_energy_delivery = boom
        try:
            conn = self._device(3)
            cfg = ingest.config_reply(conn.cursor(), DID, {}, NOW, society_id=7)
        finally:
            cloud_obs.note_energy_delivery = original
        self.assertEqual(cfg["version"], 3)
        self.assertGreater(cloud_obs._counts.get("observation_errors_total", 0), 0)

        with tempfile.TemporaryDirectory() as tmp:
            engine = EnergyEngine(tmp, lambda: {}, lambda: "2026-10-03", lambda: 15, lambda: True, logging.getLogger("phase2d"))
            original_pi = device_obs.note_energy_config_received
            device_obs.note_energy_config_received = boom
            try:
                ok, err = engine.apply_config({"version": 3, "bus": {}, "meters": {}})
            finally:
                device_obs.note_energy_config_received = original_pi
            self.assertTrue(ok, err)
            self.assertEqual(engine.config_version, 3)

        committed = {"value": False}

        def ack_like():
            try:
                committed["value"] = True
                return {"success": True, "status": "acked"}
            finally:
                try:
                    cloud_obs.safe_observe("ack", boom)
                except Exception:
                    cloud_obs.note_observation_error("ack")

        self.assertEqual(ack_like()["status"], "acked")
        self.assertTrue(committed["value"])

    def test_no_cursor_execute_monkey_patch(self):
        for relative in ("backend/cloud_obs.py", "backend/main.py", "backend/energy_api/ingest.py", "backend/energy_api/target_delivery.py", "pi_firmware/energy/meter_manager.py"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("cur.execute =", source)
            self.assertNotIn("attach_statement_counter", source)


if __name__ == "__main__":
    unittest.main()
