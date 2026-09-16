"""Offline regression for UTC calendar wiring in energy references/routes.

Covers only modified logic using in-memory fake DB; no network/socket/real DB.
"""

from __future__ import annotations

import socket
import sys
import unittest
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import psycopg
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from energy_api import routes  # noqa: E402


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        dt = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)
        return dt if tz else dt.replace(tzinfo=None)


class FakeCursor:
    def __init__(self, state):
        self.state = state
        self._rows = []
        self._one = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        params = params or ()
        q = " ".join(sql.lower().split())
        self.state["queries"].append((q, params))
        self._rows, self._one = [], None

        if q.startswith("select clock_report from energy_sync_state"):
            self._one = None
            return

        if q.startswith("select reported_config_version,reported_at from energy_sync_state"):
            self._one = None
            return

        if "from pi_devices d join societies s" in q:
            self._one = deepcopy(self.state["device"])
            return

        if q.startswith("select * from energy_meters"):
            self._rows = deepcopy(self.state["meters"])
            return

        if q.startswith("select slot, disabled from slot_configs where device_id=%s"):
            did = str(params[0])
            self._rows = deepcopy(self.state.get("slot_configs", {}).get(did, []))
            return

        if q.startswith("select max(bill_month) as month from energy_bill_history"):
            _did, wing, cutoff = params
            rows = [r for r in self.state["bill_rows"].get(wing, []) if r["bill_month"] <= cutoff]
            self._one = {"month": max((r["bill_month"] for r in rows), default=None)}
            return

        if "from energy_bill_history" in q and "between" in q and "order by bill_month" in q:
            _did, wing, start, end = params
            rows = [r for r in self.state["bill_rows"].get(wing, []) if start <= r["bill_month"] <= end]
            rows.sort(key=lambda r: r["bill_month"])
            self._rows = deepcopy(rows)
            return

        if "from energy_daily" in q and "status='closed'" in q and "wing_consumption" in q:
            # Stale Pi day should be passed here (today param), but latest_history_end may be future.
            _wing, _did, _today, _latest_end_1, _latest_end_2, _wing2, _wing3 = params
            self._one = None
            return

        if q.startswith("select generation_kwh, generation_source from energy_daily"):
            self._one = {"generation_kwh": 42.0, "generation_source": "PHYSICAL"}
            return

        if q.startswith("select id from pi_devices where id="):
            self._one = {"id": params[0]}
            return

        if q.startswith("select consumption_kwh from energy_bill_history"):
            did, wing, month = params
            for r in self.state["bill_rows"].get(wing, []):
                if r["device_id"] == did and r["bill_month"] == month:
                    self._one = {"consumption_kwh": r["consumption_kwh"]}
                    break
            return

        if q.startswith("insert into energy_bill_history"):
            did, wing, month, kwh, days, note, created_by, updated_by = params
            rows = self.state["bill_rows"].setdefault(wing, [])
            found = None
            for r in rows:
                if r["device_id"] == did and r["bill_month"] == month:
                    found = r
                    break
            if found:
                found["consumption_kwh"] = float(kwh)
                found["days"] = days
                found["note"] = note or found.get("note")
                found["updated_by"] = updated_by
                found["updated_at"] = datetime(2026, 9, 14, tzinfo=timezone.utc)
            else:
                rows.append(
                    {
                        "device_id": did,
                        "wing": wing,
                        "bill_month": month,
                        "consumption_kwh": float(kwh),
                        "days": days,
                        "note": note,
                        "source": "HISTORICAL",
                        "created_by": created_by,
                        "updated_by": updated_by,
                        "created_at": datetime(2026, 9, 14, tzinfo=timezone.utc),
                        "updated_at": datetime(2026, 9, 14, tzinfo=timezone.utc),
                    }
                )
            return

        raise AssertionError(f"Unexpected SQL in offline test: {sql}")

    def fetchone(self):
        return deepcopy(self._one)

    def fetchall(self):
        return deepcopy(self._rows)


class FakeConn:
    def __init__(self, state):
        self.state = state
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self, row_factory=None):
        return FakeCursor(self.state)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class HistoricalCalendarRegression(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.device_id = "11111111-1111-1111-1111-111111111111"
        self.stale_today = date(2024, 1, 15)
        self.calendar_today = date(2026, 9, 14)
        self.audit = []
        self.metric_dates = []
        self.latest_target_dates = []

        common_meter = {
            "device_id": self.device_id,
            "enabled": True,
            "comm_status": "ONLINE",
            "serial": "meter",
            "model": "m",
            "last_kwh": None,
            "power_kw": 1.0,
            "ct_ratio": None,
            "max_kw": None,
            "last_seen": datetime(2026, 9, 14, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 9, 14, tzinfo=timezone.utc),
        }
        self.state = {
            "queries": [],
            "device": {
                "id": self.device_id,
                "energy_bus": {},
                "energy_config_version": 3,
                "energy_calculation_mode": "AUTO",
                "energy_calculation_version": 8,
                "grid_export_enabled": True,
                "grid_export_limit_kwh": 100.0,
                "grid_reference_version": 2,
                "reset_day": 15,
            },
            "meters": [
                {**common_meter, "meter_id": "M1", "attribution": {"status": "ATTRIBUTED", "wing": "A"}},
                {**common_meter, "meter_id": "M2", "attribution": None},
                {**common_meter, "meter_id": "M3", "attribution": None},
                {**common_meter, "meter_id": "M4", "attribution": None},
                {**common_meter, "meter_id": "M5", "attribution": None},
            ],
            "bill_rows": {
                "A": [
                    {
                        "device_id": self.device_id,
                        "wing": "A",
                        "bill_month": date(2026, 7, 1),
                        "consumption_kwh": 210.0,
                        "days": 31,
                        "note": "jul",
                        "source": "HISTORICAL",
                        "created_by": "u1",
                        "updated_by": "u1",
                        "created_at": datetime(2026, 7, 5, tzinfo=timezone.utc),
                        "updated_at": datetime(2026, 7, 5, tzinfo=timezone.utc),
                    },
                    {
                        "device_id": self.device_id,
                        "wing": "A",
                        "bill_month": date(2026, 8, 1),
                        "consumption_kwh": 220.0,
                        "days": 31,
                        "note": "aug",
                        "source": "HISTORICAL",
                        "created_by": "u1",
                        "updated_by": "u1",
                        "created_at": datetime(2026, 8, 5, tzinfo=timezone.utc),
                        "updated_at": datetime(2026, 8, 5, tzinfo=timezone.utc),
                    },
                    {
                        "device_id": self.device_id,
                        "wing": "A",
                        "bill_month": date(2026, 10, 1),
                        "consumption_kwh": 999.0,
                        "days": 31,
                        "note": "future",
                        "source": "HISTORICAL",
                        "created_by": "u1",
                        "updated_by": "u1",
                        "created_at": datetime(2026, 10, 5, tzinfo=timezone.utc),
                        "updated_at": datetime(2026, 10, 5, tzinfo=timezone.utc),
                    },
                ],
                "B": [
                    {
                        "device_id": self.device_id,
                        "wing": "B",
                        "bill_month": date(2026, 8, 1),
                        "consumption_kwh": 180.0,
                        "days": 31,
                        "note": "",
                        "source": "HISTORICAL",
                        "created_by": "u1",
                        "updated_by": "u1",
                        "created_at": datetime(2026, 8, 5, tzinfo=timezone.utc),
                        "updated_at": datetime(2026, 8, 5, tzinfo=timezone.utc),
                    }
                ],
                "C": [
                    {
                        "device_id": self.device_id,
                        "wing": "C",
                        "bill_month": date(2026, 8, 1),
                        "consumption_kwh": 160.0,
                        "days": 31,
                        "note": "",
                        "source": "HISTORICAL",
                        "created_by": "u1",
                        "updated_by": "u1",
                        "created_at": datetime(2026, 8, 5, tzinfo=timezone.utc),
                        "updated_at": datetime(2026, 8, 5, tzinfo=timezone.utc),
                    }
                ],
                "D": [],
            },
            "slot_configs": {self.device_id: []},
        }

        def get_db():
            return FakeConn(self.state)

        def get_current_user():
            return {"id": "u-admin", "role": "society_admin", "society_id": "1"}

        def require_uuid(value, _field):
            return str(value)

        def log_audit(_cur, user, society_id, action, payload):
            self.audit.append({"user": user.get("id"), "society_id": society_id, "action": action, "payload": payload})

        self.router = routes.create_router(get_db, get_current_user, log_audit, require_uuid)
        self.summary = self._endpoint("/api/energy/summary", "GET")
        self.get_bills = self._endpoint("/api/energy/bills", "GET")
        self.put_bills = self._endpoint("/api/energy/bills", "PUT")

        self.socket_patch = patch.object(socket, "socket", side_effect=AssertionError("socket usage forbidden in offline regression"))
        self.connect_patch = patch.object(psycopg, "connect", side_effect=AssertionError("psycopg.connect forbidden in offline regression"))
        self.socket_patch.start()
        self.connect_patch.start()

    def tearDown(self):
        self.socket_patch.stop()
        self.connect_patch.stop()

    def _endpoint(self, path, method):
        for r in self.router.routes:
            if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
                return r.endpoint
        raise AssertionError(f"Missing route {method} {path}")

    def _metric_periods(self, _cur, _did, _expr, today, _reset_day):
        self.metric_dates.append(today)
        return {
            "today": {"kwh": 1.0, "days": 1, "status": "PHYSICAL"},
            "yesterday": {"kwh": 1.0, "days": 1, "status": "PHYSICAL"},
            "this_month": {"kwh": 1.0, "days": 1, "status": "PHYSICAL"},
            "previous_month": {"kwh": 1.0, "days": 1, "status": "PHYSICAL"},
            "this_year": {"kwh": 1.0, "days": 1, "status": "PHYSICAL"},
            "lifetime": {"kwh": 1.0, "days": 1, "status": "PHYSICAL"},
            "reset_period": {"kwh": 1.0, "days": 1, "status": "PHYSICAL", "period": "2024-01"},
        }

    def _latest_target(self, _cur, _did, wing, as_of):
        self.latest_target_dates.append((wing, as_of))
        return {
            "target_kwh_per_day": 9.0,
            "adjustment_percent": 0.0,
            "base_daily_average_kwh": 9.0,
            "effective_from": date(2023, 1, 1),
        }

    def test_summary_and_bills_use_utc_calendar_for_history_only(self):
        user = {"id": "u-admin", "role": "society_admin", "society_id": "1"}
        with patch.object(routes, "datetime", FrozenDateTime), patch.object(routes, "ensure_meter_rows", lambda _cur, _did: None), patch.object(
            routes.Q,
            "device_today",
            side_effect=lambda _cur, _did, fallback, **_kwargs: self.stale_today if fallback in (None, self.calendar_today) else None,
        ), patch.object(routes.Q, "metric_periods", side_effect=self._metric_periods), patch.object(
            routes.Q, "latest_target", side_effect=self._latest_target
        ), patch.object(routes.Q, "calculation_view", return_value={"mode": "AUTO", "version": 8, "operating_date": self.stale_today.isoformat(), "wings": {}}) as calculation_view:
            summary = self.summary(society_id="1", device_id=self.device_id, user=user)
            bills_before = self.get_bills(society_id="1", device_id=self.device_id, wing="A", end_month=None, user=user)

            put = self.put_bills(
                data={
                    "society_id": "1",
                    "device_id": self.device_id,
                    "wing": "A",
                    "months": [
                        {"month": "2026-07", "consumption_kwh": 0},
                        {"month": "2026-08", "consumption_kwh": ""},
                    ],
                },
                user=user,
            )

            bills_after = self.get_bills(society_id="1", device_id=self.device_id, wing="A", end_month=None, user=user)
            summary_after = self.summary(society_id="1", device_id=self.device_id, user=user)

            self.assertTrue(calculation_view.call_args_list)
            self.assertTrue(all(call.args[-1] == self.stale_today for call in calculation_view.call_args_list), "P1 calculation must keep the Pi operating date")

        self.assertEqual(summary["as_of_operating_date"], "2024-01-15")
        for wing in ("A", "B", "C", "D"):
            self.assertEqual(summary["references"]["wings"][wing]["history"]["end_month"], "2026-08")
        self.assertEqual(summary["references"]["wings"]["D"]["history"]["source"], "UNAVAILABLE")
        before_history = summary["references"]["wings"]["A"]["history"]
        after_history = summary_after["references"]["wings"]["A"]["history"]
        self.assertEqual(before_history["reference_daily_kwh"], bills_before["reference_daily_kwh"])
        self.assertEqual(after_history["reference_daily_kwh"], put["reference_daily_kwh"])
        self.assertEqual(after_history["reference_daily_kwh"], bills_after["reference_daily_kwh"])
        self.assertEqual(after_history["reference_daily_kwh"], round((0 / 31 + 220 / 31) / 2, 4))
        self.assertEqual(after_history["valid_months"], 2, "future October bill must not enter the September reference")

        self.assertTrue(all(d == self.stale_today for d in self.metric_dates), "Q.metric_periods must keep stale Pi operating date")
        self.assertTrue(all(as_of == self.stale_today for _, as_of in self.latest_target_dates), "latest_target must keep stale Pi day")

        closed_queries = [q for q in self.state["queries"] if "status='closed'" in q[0] and "wing_consumption" in q[0]]
        self.assertTrue(closed_queries, "physical closed-day lookups should execute")
        self.assertTrue(all(params[2] == self.stale_today for _, params in closed_queries), "closed-day lookup must use stale operating day")
        generation_queries = [(sql, params) for sql, params in self.state["queries"] if sql.startswith("select generation_kwh, generation_source from energy_daily")]
        self.assertTrue(generation_queries)
        self.assertTrue(all(params[1] == self.stale_today for _, params in generation_queries), "common generation lookup must keep stale operating day")

        self.assertEqual(bills_before["end_month"], "2026-08")
        self.assertEqual(bills_after["end_month"], "2026-08")
        self.assertEqual(put["updated_months"], 1)

        by_month = {m["month"]: m["consumption_kwh"] for m in bills_after["months"] if m["month"] in ("2026-07", "2026-08")}
        self.assertEqual(by_month["2026-07"], 0.0, "explicit zero correction must be persisted")
        self.assertEqual(by_month["2026-08"], 220.0, "blank month payload must be omitted, not overwrite")

        self.assertEqual(len(self.audit), 1)
        self.assertEqual(self.audit[0]["action"], "ENERGY_BILL_HISTORY")
        self.assertEqual(self.audit[0]["payload"]["changes"], [{"month": "2026-07", "previous_kwh": 210.0, "kwh": 0.0}])

        with self.assertRaises(HTTPException) as err:
            self.put_bills(
                data={
                    "society_id": "1",
                    "device_id": self.device_id,
                    "wing": "A",
                    "months": [{"month": "2026-07", "consumption_kwh": 10}],
                },
                user={"id": "u-member", "role": "member", "society_id": "1"},
            )
        self.assertEqual(err.exception.status_code, 403)
        self.assertIn("Read-only role", err.exception.detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
