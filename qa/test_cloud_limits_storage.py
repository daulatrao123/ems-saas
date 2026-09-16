"""Offline cloud measurements only. No main startup, firmware imports or DB calls.

Run explicitly; never discover hardware/integration suites. SQL is counted at an
in-memory boundary, NOT measured PostgreSQL work, storage or physical USB wear.
"""
import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import hashlib
import io
from pathlib import Path
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID
import zipfile

import psycopg
from psycopg.types.json import Json
from starlette.responses import JSONResponse
from backend.energy_api import ingest
from backend import provisioning
from qa.test_energy_sync_flow import MemoryConnection, MemoryCursor, DID, OTHER, NOW

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    db = {"devices": {}, "meters": {}, "readings": {}, "bills": {}, "targets": [], "daily": {}, "sync": {}, "quarantine": {}, "audit": []}
    for did in (DID, OTHER):
        db["devices"][did] = {"id": did, "energy_bus": {}, "energy_config_version": 4, "energy_allocation": {"enabled": False}}
        db["meters"][did] = [{"meter_id": f"M{i}", "enabled": False, "serial": None, "modbus_address": None,
                              "model": None, "register_map": None, "phases": 1, "ct_ratio": 1, "max_kw": None} for i in range(1, 6)]
    return db


def energy_payload(active=False):
    return {"schema": 1, "config_version": 4, "today": {"operating_date": NOW.date().isoformat(),
            "updated_at": NOW.timestamp(), "generation_kwh": 23, "generation_source": "PHYSICAL"},
            "closed_days": [], "meters": {f"M{i}": {"comm_status": "ONLINE" if active else "DISABLED",
            "last_seen": NOW.timestamp() if active else None, "last_kwh": i * 23 if active else None} for i in range(1, 6)}}


class SyncCursor(MemoryCursor):
    def execute(self, sql, params=()):
        q = " ".join(sql.lower().split())
        self.one, self.rows, self.rowcount = None, [], 0
        if self.conn.fail_sql and self.conn.fail_sql in q:
            raise RuntimeError("injected storage failure")
        if q.startswith("delete"):
            raise AssertionError("sync must not delete stored data")
        if q.startswith("update energy_meters"):
            self.conn.trace.append((q, params))
            meter = next(m for m in self.conn.state["meters"][params[-2]] if m["meter_id"] == params[-1])
            meter.update(zip(("comm_status", "last_seen", "last_kwh", "power_kw", "last_error", "attribution", "updated_at"),
                             (deepcopy(v.obj if hasattr(v, "obj") else v) for v in params[:-2])))
            return
        if q.startswith("insert into energy_meter_readings"):
            self.conn.trace.append((q, params))
            assert "on conflict do nothing" in q
            self.conn.state["readings"].setdefault(params[:3], params[3:])
            return
        if q.startswith("select config_version, reset_day from societies"):
            self.one = {"config_version": 4, "reset_day": 15}
        elif q.startswith("select hardware_profile, feedback_hardware_installed"):
            self.one = {"hardware_profile": "MOCKED", "feedback_hardware_installed": False}
        elif q.startswith("select slot, target_days") or "from firmware_versions" in q or "from pi_state" in q or "from pi_commands" in q:
            pass
        elif q.startswith("update pi_devices set last_seen") or q.startswith("insert into pi_state") or q.startswith("insert into slot_state") or q.startswith("update pi_commands") or q.startswith("insert into pi_events"):
            pass
        else:
            return super().execute(sql, params)
        self.conn.trace.append((q, params))


class SyncConnection(MemoryConnection):
    def __init__(self, db, fail_sql=None, fail_commit=False):
        super().__init__(db)
        self.fail_sql, self.fail_commit = fail_sql, fail_commit
        self.commits, self.rollbacks, self.closed = 0, 0, False
    def cursor(self, **_): return SyncCursor(self)
    def commit(self):
        if self.fail_commit: raise RuntimeError("injected commit failure")
        super().commit(); self.commits += 1
    def rollback(self): super().rollback(); self.rollbacks += 1
    def close(self): self.closed = True


def sync_function(conn, device_id=DID):
    """Actual endpoint body; auth/HTTP/health are inert fixtures, no main import."""
    tree = ast.parse((ROOT / "backend/main.py").read_text())
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "pi_sync")
    func.decorator_list = []
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return NOW
    ns = {"Request": object, "Header": lambda *a, **k: None,
          "authenticate_pi": lambda *a: (device_id, 1), "get_db": lambda: conn,
          "datetime": Clock, "timezone": timezone, "timedelta": timedelta,
          "metric_inc": lambda *a: None, "SLOTS": list("ABCD"), "Json": Json,
          "normalize_toggle_input": lambda _: None, "normalize_storage_health": lambda _: {},
          "health_read_model": SimpleNamespace(normalize_report=lambda *a: {"cpu": {"celsius": None}, "boot": {"count": None}}, watchdog_enabled=lambda _: None),
          "dict_row": None, "DEFAULT_RESET_DAY": 15, "canonical_device_config": lambda *a: {},
          "config_hash": lambda _: "0" * 64, "derive_config_state": lambda *a: "DESIRED",
          "OTA_STATES": (), "CONFIG_ERROR_CODES": (), "energy_ingest": ingest,
          "EXECUTED_IDS_MAX": 50, "COMMAND_DELIVERY_LEASE_SECONDS": 120,
          "active_lcd_message": lambda *a: None, "JSONResponse": JSONResponse}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[func], type_ignores=[])), "offline_pi_sync", "exec"), ns)
    return ns["pi_sync"]


class CloudLimitsTests(unittest.TestCase):
    def setUp(self):
        for guard in (patch.object(socket, "socket", side_effect=AssertionError("network forbidden")),
                      patch.object(psycopg, "connect", side_effect=AssertionError("live DB forbidden"))):
            guard.start(); self.addCleanup(guard.stop)

    def test_measure_sync_statement_counts(self):
        measurements = {}
        for active in (False, True):
            conn = SyncConnection(fixture())
            result = sync_function(conn)(None, {"energy": energy_payload(active), "slots": {w: {} for w in "ABCD"}})
            self.assertTrue(result["success"])
            self.assertEqual(conn.commits, 1)
            self.assertTrue(conn.closed)
            counts = {op: sum(q.startswith(op) for q, _ in conn.trace) for op in ("select", "insert", "update", "delete", "savepoint", "release")}
            counts["total"] = len(conn.trace)
            self.assertEqual(counts["total"], 36 if active else 31)
            self.assertEqual(counts["delete"], 0)
            self.assertEqual(sum(q.startswith("insert into energy_meter_readings") for q, _ in conn.trace), 5 if active else 0)
            self.assertNotIn("energy_config", result, "an unchanged reported version needs no Pi config write")
            measurements["active_five_meters" if active else "disabled_five_meters"] = counts
        print("MOCKED_SYNC_STATEMENTS=" + json.dumps(measurements, sort_keys=True))

    def test_partial_ingest_must_not_receive_legacy_success(self):
        db = fixture(); before = deepcopy(db)
        conn = SyncConnection(db, fail_sql="insert into energy_daily")
        result = sync_function(conn)(None, {"energy": energy_payload()})
        self.assertIsInstance(result, JSONResponse, "failed energy must not be a successful sync dictionary")
        self.assertEqual(result.status_code, 503)
        self.assertFalse(json.loads(result.body)["success"])
        self.assertEqual(db, before, "energy savepoint rolls back completely")
        self.assertEqual(conn.commits, 1, "heartbeat may commit, energy is not acknowledged")
        self.assertFalse(any("pi_commands" in q for q, _ in conn.trace), "do not lease a command in an unsuccessful reply")

    def test_valid_history_is_durable_on_rejection_and_retry_does_not_duplicate(self):
        db = fixture(); other = deepcopy(db["devices"][OTHER])
        payload = energy_payload()
        good = {"operating_date": "2026-09-15", "status": "CLOSED", "generation_kwh": 23, "generation_source": "PHYSICAL"}
        bad = {"operating_date": "2099-01-01", "status": "CLOSED"}
        payload["closed_days"] = [good, bad]
        untouched = deepcopy(payload)
        for _ in range(2):
            conn = SyncConnection(db)
            result = sync_function(conn)(None, {"energy": payload, "events": [{"eventId": "retained-by-legacy-client"}]})
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.headers["retry-after"], "60")
            self.assertEqual(conn.commits, 1)
            self.assertTrue(conn.closed)
            self.assertEqual(len(db["daily"]), 2)
            self.assertEqual(db["daily"][(DID, NOW.date() - timedelta(days=1))]["generation_kwh"], 23)
            self.assertIn((DID, "2099-01-01"), db["quarantine"])
            self.assertFalse(any("pi_commands" in q or "pi_events" in q for q, _ in conn.trace))
        self.assertEqual(payload, untouched, "caller retains original in-flight batch")
        self.assertEqual(db["devices"][OTHER], other)
        # Once the source payload is corrected (not silently corrected by cloud), ACK resumes.
        payload["closed_days"] = [good]
        conn = SyncConnection(db)
        self.assertTrue(sync_function(conn)(None, {"energy": payload})["success"])
        self.assertEqual(len(db["daily"]), 2)
        self.assertIn((DID, "2099-01-01"), db["quarantine"], "no automatic diagnostic deletion")

    def test_invalid_or_oversized_batches_never_silently_drop_rows(self):
        for block in ([], "bad", {"closed_days": None}, {"closed_days": {}},
                      {"closed_days": [None]}, {"closed_days": [1]},
                      {"closed_days": [{}] * 32}, {"today": "bad"}):
            with self.subTest(block=block):
                db = fixture(); conn = SyncConnection(db)
                result = sync_function(conn)(None, {"energy": block})
                self.assertEqual(result.status_code, 503)
                self.assertFalse(json.loads(result.body)["energy_accepted"])
        # Pre-energy legacy devices remain compatible.
        conn = SyncConnection(fixture())
        self.assertTrue(sync_function(conn)(None, {})["success"])

    def test_storage_failure_recovery_and_commit_failure_never_ack(self):
        for failure in ("update energy_meters", "insert into energy_meter_readings", "insert into energy_daily", "insert into energy_sync_state"):
            with self.subTest(failure=failure):
                db = fixture(); before = deepcopy(db)
                conn = SyncConnection(db, fail_sql=failure)
                self.assertEqual(sync_function(conn)(None, {"energy": energy_payload(True)}).status_code, 503)
                self.assertEqual(db, before)
                conn = SyncConnection(db)
                self.assertTrue(sync_function(conn)(None, {"energy": energy_payload(True)})["success"])
        for rejected in (False, True):
            db = fixture(); before = deepcopy(db)
            conn = SyncConnection(db, fail_commit=True)
            payload = energy_payload()
            if rejected: payload["today"]["operating_date"] = "2099-01-01"
            with self.assertRaisesRegex(RuntimeError, "commit failure"):
                sync_function(conn)(None, {"energy": payload})
            self.assertEqual(db, before)
            self.assertEqual(conn.commits, 0)
            self.assertEqual(conn.rollbacks, 1)
            self.assertTrue(conn.closed)

    def test_retries_keep_cumulative_history_and_closed_day_dominance(self):
        db = fixture(); payload = energy_payload(True)
        payload["closed_days"] = [{"operating_date": "2026-09-15", "status": "CLOSED", "generation_kwh": 42,
                                   "updated_at": NOW.timestamp() - 60, "generation_source": "PHYSICAL"}]
        for _ in range(2):
            conn = SyncConnection(db)
            self.assertTrue(sync_function(conn)(None, {"energy": payload})["success"])
        self.assertEqual(len(db["readings"]), 5, "same timestamp is idempotent")
        # Old history is retained, even beyond the removed 30-day cleanup age.
        oldest = (DID, "M1", NOW - timedelta(days=365))
        db["readings"][oldest] = (1, None)
        payload["closed_days"][0].update(status="OPEN", generation_kwh=999)
        for meter in payload["meters"].values(): meter["last_seen"] += 60
        conn = SyncConnection(db)
        self.assertTrue(sync_function(conn)(None, {"energy": payload})["success"])
        self.assertEqual(len(db["readings"]), 11)
        self.assertIn(oldest, db["readings"])
        self.assertEqual(db["daily"][(DID, NOW.date() - timedelta(days=1))]["generation_kwh"], 42)

    def test_database_uuid_failure_response_is_json_serializable(self):
        conn = SyncConnection(fixture())
        result = sync_function(conn, UUID(DID))(None, {"energy": "invalid"})
        self.assertEqual(result.status_code, 503)
        self.assertEqual(json.loads(result.body)["device_id"], DID)

    def test_bulk_defaults_and_config_fast_path_preserve_delivery_rules(self):
        conn = SyncConnection(fixture())
        ingest.ensure_meter_rows(conn.cursor(), DID)
        self.assertEqual(len(conn.trace), 1)
        q, params = conn.trace[0]
        self.assertIn("on conflict (device_id, meter_id) do nothing", q)
        self.assertEqual(q.count("%s"), 20)
        self.assertEqual(params, tuple(v for i, mid in enumerate(ingest.METER_IDS)
                                      for v in (DID, mid, ingest.METER_ROLE[mid], ingest.METER_WING[mid])))
        for version in (4, 3, None, True, "4", -1, 2**40):
            with self.subTest(version=version):
                conn = SyncConnection(fixture())
                cfg = ingest.config_reply(conn.cursor(), DID, {"config_version": version}, NOW)
                if type(version) is int and version == 4:
                    self.assertIsNone(cfg)
                    self.assertFalse(any("energy_meters" in q for q, _ in conn.trace))
                else:
                    self.assertEqual(len(cfg["meters"]), 5)
                    self.assertEqual(cfg["version"], 4)
                self.assertEqual(conn.state["devices"][DID]["energy_config_version"], 4)
                self.assertIn("for update", conn.trace[0][0], "lock device before sync-state report")
        conn = SyncConnection(fixture())
        self.assertEqual(len(ingest.build_energy_config(conn.cursor(), DID, NOW)["meters"]), 5)

    def test_provisioning_package_in_memory_contains_unchanged_runtime(self):
        # Non-credential placeholders only, no installer/runtime/firmware execution.
        with patch.object(Path, "write_bytes", side_effect=AssertionError("no file writes")), \
             patch.object(Path, "write_text", side_effect=AssertionError("no file writes")):
            data = provisioning.build_provisioning_zip(DID, "OFFLINE fixture", "NOT-A-KEY", "NOT-A-CREDENTIAL", "https://example.invalid/api")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            prefix = provisioning.ROOT + "/"
            for name in provisioning.FIRMWARE_FILES:
                self.assertEqual(archive.read(prefix + "firmware/" + name), (provisioning.FIRMWARE_DIR / name).read_bytes())
            manifest = archive.read(prefix + "MANIFEST.sha256").decode().splitlines()
            for entry in manifest:
                digest, name = entry.split("  ", 1)
                self.assertEqual(hashlib.sha256(archive.read(prefix + name)).hexdigest(), digest)
            self.assertFalse(any(n.endswith((".sqlite", "ledger.json", "meter_state.json")) for n in archive.namelist()))
            print("MOCKED_PACKAGE=" + json.dumps({"zip_bytes": len(data), "entries": len(archive.namelist()),
                  "uncompressed_bytes": sum(item.file_size for item in archive.infolist()),
                  "note": "in-memory only; no installation or physical write measurement"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)