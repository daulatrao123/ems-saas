"""Cloud/React blockers: offline fake cursors only; never import main or firmware."""
import socket
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

import psycopg
from fastapi import HTTPException
from backend.energy_api import ingest, queries
from backend.energy_api.operating_dates import day_problem, quarantine, record_clock_report
from backend.energy_api.target_delivery import reconcile_targets, record_report, delivery_view


NOW = datetime(2026, 9, 16, 1, tzinfo=timezone.utc)


class ClockQualificationTests(unittest.TestCase):
    def setUp(self):
        for p in [patch.object(socket, "socket", side_effect=AssertionError("network forbidden")),
                  patch.object(psycopg, "connect", side_effect=AssertionError("live DB forbidden"))]:
            p.start()
            self.addCleanup(p.stop)

    def test_bad_days_never_normalize_and_local_dates_remain_physical(self):
        for day in ["2099-01-01", "1970-01-01", "2026-02-30", "garbage"]:
            self.assertIsNone(ingest.normalize_day({"operating_date": day}, NOW))
        for day in ["2026-09-15", "2026-09-16", "2024-02-29"]:
            self.assertEqual(ingest.normalize_day({"operating_date": day}, NOW)["operating_date"].isoformat(), day)
        # UTC15Sep20:00 is16Sep01:30 in Mumbai; must not clamp the Pi toUTC15Sep.
        india_midnight = datetime(2026, 9, 15, 20, tzinfo=timezone.utc)
        self.assertIsNotNone(ingest.normalize_day({"operating_date": "2026-09-16"}, india_midnight))
        self.assertIsNone(ingest.normalize_day({"operating_date": "2026-09-17"}, india_midnight))

    def test_timestamp_poisoning_and_unknown_status_are_rejected(self):
        for stamp in [NOW.timestamp() + 301, "NaN", "Infinity", True, 10**100]:
            self.assertIsNone(ingest.normalize_day({"operating_date": "2026-09-16", "updated_at": stamp}, NOW))
        self.assertIsNotNone(ingest.normalize_day({"operating_date": "2026-09-16", "updated_at": NOW.timestamp() + 300}, NOW))
        self.assertEqual(day_problem({"operating_date": "2026-09-16", "status": "TYPO"}, NOW), "INVALID_LEDGER_STATUS")

    def test_existing_invalid_or_multiple_open_days_fail_closed_without_fallback(self):
        class Cursor:
            def __init__(self, dates): self.dates = dates
            def execute(self, sql, params):
                assert params == ("device",) and "COUNT(*) AS open_count" in sql and "status='OPEN'" in sql
            def fetchone(self):
                return {"d": max(self.dates) if self.dates else None, "earliest": min(self.dates) if self.dates else None, "open_count": len(self.dates)}
        for days in [[date(2099, 1, 1)], [date(2026, 9, 15), date(2099, 1, 1)]]:
            with self.assertRaises(HTTPException) as error:
                queries.device_today(Cursor(days), "device", NOW.date(), now=NOW)
            self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(queries.device_today(Cursor([date(2024, 2, 29)]), "device", NOW.date(), now=NOW), date(2024, 2, 29))
        self.assertIsNone(queries.device_today(Cursor([]), "device", None, now=NOW))
        with self.assertLogs("ems.energy", level="WARNING"):
            self.assertEqual(queries.device_today(Cursor([date(2026, 9, 15), date(2026, 9, 16)]), "device", None, now=NOW), date(2026, 9, 16))

    def test_quarantine_keeps_original_preview_and_is_bounded(self):
        calls = []
        class Cursor:
            def execute(self, sql, params): calls.append((sql, params))
            def fetchone(self): return {"open_count": 0}
        raw = {"operating_date": "2099-01-01", "generation_kwh": 23, "status": "OPEN"}
        result = quarantine(Cursor(), "device", raw, "FUTURE_OPERATING_DATE", NOW)
        report = record_clock_report(Cursor(), "device", raw, [result], NOW)
        self.assertIn('"generation_kwh": 23', calls[0][1][3])
        self.assertEqual(report["status"], "REJECTED")
        self.assertEqual(report["reported_operating_date"], "2099-01-01")
        self.assertEqual(calls[-1][1], ("device", "device", 64))
        self.assertTrue(all("energy_daily" not in sql or sql.startswith("SELECT") for sql, _ in calls))


class TargetDeliveryTests(unittest.TestCase):
    def test_version_bump_rollover_resend_ack_and_freshness(self):
        class Cursor:
            def __init__(self):
                self.version, self.signature, self.reported, self.stamp, self.one, self.rows = 4, {}, None, None, None, []
                self.targets = [{"id": 1, "wing": "A", "target_kwh_per_day": 23, "effective_from": date(2026, 9, 1)},
                                {"id": 2, "wing": "A", "target_kwh_per_day": 40, "effective_from": date(2026, 9, 17)}]
            def execute(self, sql, params):
                q = " ".join(sql.split())
                if q.startswith("SELECT DISTINCT ON"):
                    candidates = [t for t in self.targets if t["effective_from"] <= params[1]]
                    self.rows = [max(candidates, key=lambda t: (t["effective_from"], t["id"]))] if candidates else []
                elif q.startswith("SELECT target_signature"):
                    self.one = {"target_signature": self.signature}
                elif q.startswith("UPDATE pi_devices"):
                    self.version += 1
                    self.one = {"energy_config_version": self.version}
                elif q.startswith("INSERT INTO energy_sync_state (device_id,target_signature)"):
                    self.signature = params[1].obj
                elif q.startswith("INSERT INTO energy_sync_state (device_id,reported_config_version"):
                    _, self.reported, self.stamp = params
                elif q.startswith("SELECT reported_config_version"):
                    self.one = {"reported_config_version": self.reported, "reported_at": self.stamp}
                else:
                    raise AssertionError(q)
            def fetchall(self): return self.rows
            def fetchone(self): return self.one
        cur = Cursor()
        targets, version = reconcile_targets(cur, "device", date(2026, 9, 16), cur.version, force=True)
        self.assertEqual((targets["A"]["target_kwh_per_day"], version), (23, 5))
        self.assertEqual(reconcile_targets(cur, "device", date(2026, 9, 16), version)[1], 5)
        record_report(cur, "device", {"config_version": 4}, NOW)
        self.assertEqual(delivery_view(cur, "device", 5, NOW)["status"], "PENDING")
        record_report(cur, "device", {"config_version": 5}, NOW)
        self.assertEqual(delivery_view(cur, "device", 5, NOW)["status"], "REPORTED_CURRENT")
        targets, version = reconcile_targets(cur, "device", date(2026, 9, 17), version)
        self.assertEqual((targets["A"]["target_kwh_per_day"], version), (40, 6))
        self.assertEqual(reconcile_targets(cur, "device", date(2026, 9, 17), version)[1], 6)
        self.assertEqual(delivery_view(cur, "device", 6, NOW)["status"], "PENDING")
        self.assertEqual(delivery_view(cur, "device", 5, datetime(2026, 9, 17, tzinfo=timezone.utc))["status"], "STALE_REPORT")
        for invalid in [True, -1, "6", None, 2**40]:
            self.assertIsNone(record_report(cur, "device", {"config_version": invalid}, NOW))
            self.assertEqual(delivery_view(cur, "device", 6, NOW)["status"], "UNKNOWN")
        cur.targets.append({"id": 3, "wing": "A", "target_kwh_per_day": float("inf"), "effective_from": date(2026, 9, 17)})
        with self.assertRaises(HTTPException):
            reconcile_targets(cur, "device", date(2026, 9, 17), version)
        self.assertEqual(cur.version, version)


if __name__ == "__main__":
    unittest.main()