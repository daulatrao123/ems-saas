"""Offline regression for selected calendar-month bill visibility and comparisons.

Uses in-memory router endpoints only (no backend.main, no server, no real DB).
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


class FrozenClock(datetime):
    @classmethod
    def now(cls, tz=None):
        dt = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)
        return dt if tz else dt.replace(tzinfo=None)


class BillMonthCursor:
    """In-memory SQL adapter for graph/comparison + bills PUT/GET only."""

    def __init__(self, state):
        self.state = state
        self._one = None
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        params = params or ()
        q = " ".join(sql.lower().split())
        self.state["queries"].append((q, params))
        self._one, self._rows = None, []

        if "from pi_devices d join societies s" in q:
            did, sid = str(params[0]), int(params[1])
            row = self.state["devices"].get(did)
            self._one = deepcopy(row) if row and int(row["society_id"]) == sid else None
            return

        if q.startswith("insert into energy_meters"):
            self.state["ensure_meter_rows_calls"] += 1
            return

        if q.startswith("select * from energy_meters where device_id=%s order by meter_id"):
            did = str(params[0])
            self._rows = [deepcopy(r) for r in self.state["meters"].get(did, [])]
            return

        if q.startswith("select slot, disabled from slot_configs where device_id=%s"):
            did = str(params[0])
            self._rows = [deepcopy(r) for r in self.state.get("slot_configs", {}).get(did, [])]
            return

        if q.startswith("select max(operating_date) as d from energy_daily where device_id=%s and status='open'"):
            did = str(params[0])
            days = [r["operating_date"] for r in self.state["daily"].get(did, []) if r["status"] == "OPEN"]
            self._one = {"d": max(days)} if days else {"d": None}
            return

        if q.startswith("select operating_date, status, (wing_generation->>"):
            wing, wing2, wing3, did, frm, to = params
            assert wing == wing2 == wing3
            rows = []
            for r in self.state["daily"].get(str(did), []):
                if not (frm <= r["operating_date"] <= to):
                    continue
                rows.append(
                    {
                        "operating_date": r["operating_date"],
                        "status": r["status"],
                        "gen": r["wing_generation"].get(wing),
                        "cons": r["wing_consumption"].get(wing),
                        "cons_src": r["consumption_source"].get(wing),
                        "generation_source": r["generation_source"],
                    }
                )
            rows.sort(key=lambda r: r["operating_date"])
            self._rows = rows
            return

        if q.startswith("select operating_date, kind, sum(value_kwh) as v from energy_adjustments"):
            did, wing, frm, to = params
            grouped = {}
            for a in self.state["adjustments"]:
                if a["device_id"] != did or a["wing"] != wing or not (frm <= a["operating_date"] <= to):
                    continue
                k = (a["operating_date"], a["kind"])
                grouped[k] = grouped.get(k, 0.0) + float(a["value_kwh"])
            self._rows = [{"operating_date": k[0], "kind": k[1], "v": v} for k, v in sorted(grouped.items())]
            return

        if q.startswith("select operating_date, generation_kwh, generation_source from energy_daily where device_id=%s and operating_date between %s and %s order by operating_date"):
            did, frm, to = str(params[0]), params[1], params[2]
            rows = [
                {
                    "operating_date": r["operating_date"],
                    "generation_kwh": r["generation_kwh"],
                    "generation_source": r["generation_source"],
                }
                for r in self.state["daily"].get(did, [])
                if frm <= r["operating_date"] <= to
            ]
            rows.sort(key=lambda r: r["operating_date"])
            self._rows = rows
            return

        if q.startswith("select id, base_daily_average_kwh, adjustment_percent, target_kwh_per_day, effective_from from energy_generation_targets"):
            # Comparison read-model must not depend on target rows being present.
            self._rows = []
            return

        if q.startswith("select bill_month, consumption_kwh from energy_bill_history where device_id=%s and wing=%s and bill_month between %s and %s order by bill_month"):
            did, wing, start, end = str(params[0]), params[1], params[2], params[3]
            rows = [
                {"bill_month": r["bill_month"], "consumption_kwh": r["consumption_kwh"]}
                for r in self.state["bills"].get((did, wing), [])
                if start <= r["bill_month"] <= end
            ]
            rows.sort(key=lambda r: r["bill_month"])
            self._rows = rows
            return

        if q.startswith("select id from pi_devices where id=%s for update"):
            self._one = {"id": str(params[0])}
            return

        if q.startswith("select consumption_kwh from energy_bill_history where device_id=%s and wing=%s and bill_month=%s"):
            did, wing, month = str(params[0]), params[1], params[2]
            row = next((r for r in self.state["bills"].get((did, wing), []) if r["bill_month"] == month), None)
            self._one = {"consumption_kwh": row["consumption_kwh"]} if row else None
            return

        if q.startswith("insert into energy_bill_history"):
            did, wing, bm, kwh, days, note, created_by, updated_by = params
            key = (str(did), wing)
            rows = self.state["bills"].setdefault(key, [])
            found = next((r for r in rows if r["bill_month"] == bm), None)
            if found:
                found["consumption_kwh"] = float(kwh)
                found["days"] = days
                found["note"] = note or found.get("note")
                found["updated_by"] = updated_by
                found["updated_at"] = FrozenClock.now(timezone.utc)
            else:
                rows.append(
                    {
                        "bill_month": bm,
                        "consumption_kwh": float(kwh),
                        "days": days,
                        "note": note,
                        "source": "HISTORICAL",
                        "created_by": created_by,
                        "updated_by": updated_by,
                        "created_at": FrozenClock.now(timezone.utc),
                        "updated_at": FrozenClock.now(timezone.utc),
                    }
                )
            return

        if q.startswith("select max(bill_month) as month from energy_bill_history where device_id=%s and wing=%s and bill_month<=%s"):
            did, wing, cutoff = str(params[0]), params[1], params[2]
            rows = [r["bill_month"] for r in self.state["bills"].get((did, wing), []) if r["bill_month"] <= cutoff]
            self._one = {"month": max(rows)} if rows else {"month": None}
            return

        if q.startswith("select bill_month, consumption_kwh, note, source, created_by, updated_by, created_at, updated_at from energy_bill_history where device_id=%s and wing=%s and bill_month between %s and %s order by bill_month"):
            did, wing, start, end = str(params[0]), params[1], params[2], params[3]
            rows = [deepcopy(r) for r in self.state["bills"].get((did, wing), []) if start <= r["bill_month"] <= end]
            rows.sort(key=lambda r: r["bill_month"])
            self._rows = rows
            return

        raise AssertionError(f"Unexpected SQL in offline test: {sql}")

    def fetchone(self):
        return deepcopy(self._one)

    def fetchall(self):
        return deepcopy(self._rows)


class BillMonthConn:
    def __init__(self, state):
        self.state = state

    def cursor(self, row_factory=None):
        return BillMonthCursor(self.state)

    def commit(self):
        self.state["commits"] += 1

    def rollback(self):
        self.state["rollbacks"] += 1

    def close(self):
        self.state["closed"] += 1


class BillMonthVisibilityRegression(unittest.TestCase):
    def setUp(self):
        self.did = "44444444-4444-4444-4444-444444444444"
        self.other = "55555555-5555-5555-5555-555555555555"
        now = FrozenClock.now(timezone.utc)

        def meter(mid):
            return {
                "device_id": self.did,
                "meter_id": mid,
                "enabled": True,
                "comm_status": "ONLINE",
                "serial": f"{mid}-SN",
                "model": "m",
                "last_kwh": None,
                "power_kw": 1.0,
                "ct_ratio": None,
                "max_kw": None,
                "last_seen": now,
                "updated_at": now,
                "attribution": {"status": "ATTRIBUTED", "wing": "A"} if mid == "M1" else None,
            }

        daily = []
        for day in range(1, 31):
            op = date(2026, 9, day)
            # Sep 10 intentionally unqualified to assert N/A generation while consumption remains bill-derived.
            source = "PHYSICAL" if day != 10 else "MANUAL"
            g = 120.0 if day != 10 else 777.0
            daily.append(
                {
                    "device_id": self.did,
                    "operating_date": op,
                    "status": "CLOSED",
                    "generation_kwh": g,
                    "generation_source": source,
                    "wing_generation": {"A": 20.0 if source == "PHYSICAL" else 777.0, "B": 10.0, "C": 5.0, "D": 2.0},
                    "wing_consumption": {"A": 1.0, "B": 1.0, "C": 1.0, "D": 1.0},
                    "consumption_source": {"A": "PHYSICAL", "B": "PHYSICAL", "C": "PHYSICAL", "D": "PHYSICAL"},
                }
            )
        # Latest OPEN day is stale (Aug), while selected month can be September.
        daily.append(
            {
                "device_id": self.did,
                "operating_date": date(2026, 8, 31),
                "status": "OPEN",
                "generation_kwh": 90.0,
                "generation_source": "PHYSICAL",
                "wing_generation": {"A": 15.0, "B": 15.0, "C": 15.0, "D": 15.0},
                "wing_consumption": {"A": 5.0, "B": 5.0, "C": 5.0, "D": 5.0},
                "consumption_source": {"A": "PHYSICAL", "B": "PHYSICAL", "C": "PHYSICAL", "D": "PHYSICAL"},
            }
        )

        self.state = {
            "queries": [],
            "ensure_meter_rows_calls": 0,
            "commits": 0,
            "rollbacks": 0,
            "closed": 0,
            "devices": {
                self.did: {
                    "id": self.did,
                    "society_id": 1,
                    "energy_bus": {},
                    "energy_config_version": 1,
                    "energy_calculation_mode": "MANUAL",
                    "energy_calculation_version": 4,
                    "grid_export_enabled": False,
                    "grid_export_limit_kwh": 0.0,
                    "grid_reference_version": 1,
                    "reset_day": 15,
                },
                self.other: {
                    "id": self.other,
                    "society_id": 1,
                    "energy_bus": {},
                    "energy_config_version": 1,
                    "energy_calculation_mode": "MANUAL",
                    "energy_calculation_version": 7,
                    "grid_export_enabled": False,
                    "grid_export_limit_kwh": 0.0,
                    "grid_reference_version": 1,
                    "reset_day": 15,
                },
            },
            "meters": {
                self.did: [meter("M1"), meter("M2"), meter("M3"), meter("M4"), meter("M5")],
                self.other: [{**meter(mid), "device_id": self.other} for mid in ("M1", "M2", "M3", "M4", "M5")],
            },
            "daily": {
                self.did: daily,
                self.other: [
                    {
                        "device_id": self.other,
                        "operating_date": date(2026, 9, 9),
                        "status": "OPEN",
                        "generation_kwh": 50.0,
                        "generation_source": "PHYSICAL",
                        "wing_generation": {"A": 12.0, "B": 12.0, "C": 12.0, "D": 12.0},
                        "wing_consumption": {"A": 1.0, "B": 1.0, "C": 1.0, "D": 1.0},
                        "consumption_source": {"A": "PHYSICAL", "B": "PHYSICAL", "C": "PHYSICAL", "D": "PHYSICAL"},
                    }
                ],
            },
            "adjustments": [
                {"device_id": self.did, "wing": "A", "operating_date": date(2026, 9, 9), "kind": "MANUAL_CONSUMPTION", "value_kwh": 9999}
            ],
            "bills": {
                (self.did, "A"): [
                    {"bill_month": date(2026, 8, 1), "consumption_kwh": 620.0, "days": 31, "note": "aug", "source": "HISTORICAL", "created_by": "u-admin", "updated_by": "u-admin", "created_at": now, "updated_at": now},
                    {"bill_month": date(2026, 9, 1), "consumption_kwh": 600.0, "days": 30, "note": "sep", "source": "HISTORICAL", "created_by": "u-admin", "updated_by": "u-admin", "created_at": now, "updated_at": now},
                ],
                (self.did, "B"): [{"bill_month": date(2026, 9, 1), "consumption_kwh": 300.0, "days": 30, "note": None, "source": "HISTORICAL", "created_by": "u-admin", "updated_by": "u-admin", "created_at": now, "updated_at": now}],
                (self.did, "C"): [{"bill_month": date(2026, 9, 1), "consumption_kwh": 0.0, "days": 30, "note": None, "source": "HISTORICAL", "created_by": "u-admin", "updated_by": "u-admin", "created_at": now, "updated_at": now}],
                (self.did, "D"): [],
                (self.other, "A"): [{"bill_month": date(2026, 9, 1), "consumption_kwh": 1234.0, "days": 30, "note": "other-device", "source": "HISTORICAL", "created_by": "u-admin", "updated_by": "u-admin", "created_at": now, "updated_at": now}],
            },
            "slot_configs": {self.did: [], self.other: []},
        }

        for guard in (
            patch.object(socket, "socket", side_effect=AssertionError("offline: socket forbidden")),
            patch.object(psycopg, "connect", side_effect=AssertionError("offline: DB forbidden")),
            patch.object(routes, "datetime", FrozenClock),
        ):
            guard.start()
            self.addCleanup(guard.stop)

        def get_db():
            return BillMonthConn(self.state)

        def require_uuid(value, _field):
            return str(value)

        def log_audit(_cur, _user, _society_id, _action, _payload):
            return None

        self.router = routes.create_router(get_db, lambda: self._admin(), log_audit, require_uuid)
        self.graph = self._endpoint("/api/energy/graph/comparison", "GET")
        self.put_bills = self._endpoint("/api/energy/bills", "PUT")
        self.get_bills = self._endpoint("/api/energy/bills", "GET")

    def _endpoint(self, path, method):
        for r in self.router.routes:
            if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
                return r.endpoint
        raise AssertionError(f"Missing route {method} {path}")

    @staticmethod
    def _admin(society_id="1"):
        return {"id": "u-admin", "role": "society_admin", "society_id": society_id}

    @staticmethod
    def _member(society_id="1"):
        return {"id": "u-member", "role": "member", "society_id": society_id}

    # Router contract: selected month uses calendar period metadata + stale Pi operating day.
    def test_selected_month_uses_exact_bill_rate_and_calendar_metadata(self):
        out = self.graph(society_id="1", device_id=self.did, month="2026-09", user=self._admin())
        self.assertEqual(out["operating_date"], "2026-08-31", "must preserve actual Pi day even for selected month")
        self.assertEqual(out["period"], {"kind": "CALENDAR_MONTH", "month": "2026-09", "start": "2026-09-01", "end": "2026-09-14", "calendar_days": 30})
        self.assertNotIn("today", out["wings"]["A"], "historical series must not expose misleading today field")

        wing_a = {r["date"]: r for r in out["wings"]["A"]["rows"]}
        self.assertEqual(wing_a["2026-09-09"]["consumed_kwh"], 20.0)
        self.assertEqual(wing_a["2026-09-09"]["consumption_source"], "HISTORICAL")
        self.assertEqual(wing_a["2026-09-09"]["generated_kwh"], 20.0)
        self.assertEqual(wing_a["2026-09-10"]["consumed_kwh"], 20.0)
        self.assertIsNone(wing_a["2026-09-10"]["generated_kwh"], "unqualified physical generation must remain unavailable")
        self.assertIsNone(wing_a["2026-09-10"]["generation_minus_consumption_kwh"])

        # Zero bill must remain explicit 0, not missing.
        wing_c = {r["date"]: r for r in out["wings"]["C"]["rows"]}
        self.assertEqual((wing_c["2026-09-09"]["consumed_kwh"], wing_c["2026-09-09"]["consumption_source"]), (0.0, "HISTORICAL"))

        # Unknown wing bill blocks society sum while known wing consumption remains visible.
        society_0909 = next(r for r in out["society"]["rows"] if r["date"] == "2026-09-09")
        self.assertIsNone(society_0909["consumed_kwh"])
        self.assertIn("D", society_0909["missing_consumption_wings"])

    # Month window behavior: current month ends at UTC today; full past month includes all days.
    def test_current_vs_past_month_boundaries(self):
        current = self.graph(society_id="1", device_id=self.did, month="2026-09", user=self._admin())
        self.assertEqual(len(current["wings"]["A"]["rows"]), 14)
        self.assertEqual(current["period"]["end"], "2026-09-14")
        self.assertFalse(any(r["date"] > "2026-09-14" for r in current["wings"]["A"]["rows"]))

        august = self.graph(society_id="1", device_id=self.did, month="2026-08", user=self._admin())
        self.assertEqual(august["period"]["end"], "2026-08-31")
        self.assertEqual(len(august["wings"]["A"]["rows"]), 31)
        aug31 = next(r for r in august["wings"]["A"]["rows"] if r["date"] == "2026-08-31")
        self.assertEqual(aug31["consumed_kwh"], 20.0, "620/31 must stay exact for August")

    # Backward compatibility + authorization/validation for graph/comparison.
    def test_days_default_compatibility_and_month_validation(self):
        out7 = self.graph(society_id="1", device_id=self.did, days=7, user=self._admin())
        out30 = self.graph(society_id="1", device_id=self.did, days=30, user=self._member())
        self.assertEqual((len(out7["society"]["rows"]), len(out30["society"]["rows"])), (7, 30))
        self.assertEqual(out30["operating_date"], "2026-08-31")

        with self.assertRaises(HTTPException) as bad_days:
            self.graph(society_id="1", device_id=self.did, days=8, user=self._admin())
        self.assertEqual(bad_days.exception.status_code, 400)

        for bad in ("2026/09", "2069-01", "1970-12"):
            with self.subTest(bad=bad), self.assertRaises(HTTPException) as bad_month:
                self.graph(society_id="1", device_id=self.did, month=bad, user=self._admin())
            self.assertEqual(bad_month.exception.status_code, 400)

        with self.assertRaises(HTTPException) as tenant:
            self.graph(society_id="2", device_id=self.did, month="2026-09", user=self._admin(society_id="1"))
        self.assertEqual(tenant.exception.status_code, 403)

    # selected month route must not invoke meter initialization writes.
    def test_selected_month_skips_ensure_meter_rows(self):
        before = self.state["ensure_meter_rows_calls"]
        self.graph(society_id="1", device_id=self.did, month="2026-09", user=self._admin())
        self.assertEqual(self.state["ensure_meter_rows_calls"], before)

    # PUT upsert must return committed month values and update selected-month daily rates only.
    def test_put_bills_then_selected_month_reflects_exact_updated_values(self):
        put = self.put_bills(
            data={
                "society_id": "1",
                "device_id": self.did,
                "wing": "A",
                "end_month": "2026-09",
                "months": [
                    {"month": "2026-09", "consumption_kwh": 900},
                    {"month": "2026-08", "consumption_kwh": ""},
                ],
            },
            user=self._admin(),
        )
        self.assertEqual(put["updated_months"], 1)

        sep = self.graph(society_id="1", device_id=self.did, month="2026-09", user=self._admin())
        sep_0909 = next(r for r in sep["wings"]["A"]["rows"] if r["date"] == "2026-09-09")
        self.assertEqual(sep_0909["consumed_kwh"], 30.0, "900/30 must replace prior 600/30")

        aug = self.graph(society_id="1", device_id=self.did, month="2026-08", user=self._admin())
        aug31 = next(r for r in aug["wings"]["A"]["rows"] if r["date"] == "2026-08-31")
        self.assertEqual(aug31["consumed_kwh"], 20.0, "blank month entry must retain prior value")

        # Other wing data must remain unchanged.
        wing_b = next(r for r in sep["wings"]["B"]["rows"] if r["date"] == "2026-09-09")
        self.assertEqual(wing_b["consumed_kwh"], 10.0)

        # Device isolation: another device's wing A bill cannot leak.
        self.assertNotEqual(sep_0909["consumed_kwh"], 1234.0 / 30)

    # Denominator exactness across month lengths and multiple changed months.
    def test_bill_denominators_28_29_30_31_and_multiple_month_updates(self):
        now = FrozenClock.now(timezone.utc)
        self.state["bills"][(self.did, "A")].extend(
            [
                {"bill_month": date(2025, 2, 1), "consumption_kwh": 560.0, "days": 28, "note": None, "source": "HISTORICAL", "created_by": "u-admin", "updated_by": "u-admin", "created_at": now, "updated_at": now},
                {"bill_month": date(2024, 2, 1), "consumption_kwh": 580.0, "days": 29, "note": None, "source": "HISTORICAL", "created_by": "u-admin", "updated_by": "u-admin", "created_at": now, "updated_at": now},
                {"bill_month": date(2026, 4, 1), "consumption_kwh": 600.0, "days": 30, "note": None, "source": "HISTORICAL", "created_by": "u-admin", "updated_by": "u-admin", "created_at": now, "updated_at": now},
                {"bill_month": date(2026, 5, 1), "consumption_kwh": 620.0, "days": 31, "note": None, "source": "HISTORICAL", "created_by": "u-admin", "updated_by": "u-admin", "created_at": now, "updated_at": now},
            ]
        )

        put = self.put_bills(
            data={
                "society_id": "1",
                "device_id": self.did,
                "wing": "A",
                "end_month": "2026-09",
                "months": [
                    {"month": "2026-09", "consumption_kwh": 900},
                    {"month": "2026-08", "consumption_kwh": 620},
                ],
            },
            user=self._admin(),
        )
        self.assertEqual(put["updated_months"], 2)

        aug = self.graph(society_id="1", device_id=self.did, month="2026-08", user=self._admin())
        sep = self.graph(society_id="1", device_id=self.did, month="2026-09", user=self._admin())
        self.assertEqual(next(r for r in aug["wings"]["A"]["rows"] if r["date"] == "2026-08-31")["consumed_kwh"], 20.0)
        self.assertEqual(next(r for r in sep["wings"]["A"]["rows"] if r["date"] == "2026-09-09")["consumed_kwh"], 30.0)

        feb28 = self.graph(society_id="1", device_id=self.did, month="2025-02", user=self._admin())
        self.assertEqual(next(r for r in feb28["wings"]["A"]["rows"] if r["date"] == "2025-02-28")["consumed_kwh"], 20.0)
        feb29 = self.graph(society_id="1", device_id=self.did, month="2024-02", user=self._admin())
        self.assertEqual(next(r for r in feb29["wings"]["A"]["rows"] if r["date"] == "2024-02-29")["consumed_kwh"], 20.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
