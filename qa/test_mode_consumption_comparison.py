"""Offline regression for /api/energy/graph/comparison mode-aware consumption behavior.

Uses in-memory FakeCursor/FakeConn boundaries only (no live DB/network/server).
"""

from __future__ import annotations

import socket
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import psycopg
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from energy_api import comparison as C  # noqa: E402
from energy_api import routes  # noqa: E402

from qa.test_operating_date_optional_meters import Clock, FakeConn, FakeCursor  # noqa: E402


class ComparisonCursor(FakeCursor):
    """Fake cursor extension for the new comparison-range generation SELECT."""

    def execute(self, sql, params=None):
        params = params or ()
        q = " ".join(sql.lower().split())

        if q.startswith(
            "select operating_date, generation_kwh, generation_source from energy_daily where device_id=%s and operating_date between %s and %s order by operating_date"
        ):
            self.state["queries"].append((q, params))
            self._rows, self._one = [], None
            did, frm, to = params
            rows = [
                {
                    "operating_date": r["operating_date"],
                    "generation_kwh": r.get("generation_kwh"),
                    "generation_source": r.get("generation_source"),
                }
                for r in self.state["daily"]
                if r["device_id"] == did and frm <= r["operating_date"] <= to
            ]
            rows.sort(key=lambda r: r["operating_date"])
            self._rows = rows
            return

        return super().execute(sql, params)


class ComparisonConn(FakeConn):
    def cursor(self, row_factory=None):
        return ComparisonCursor(self.state)


class ModeConsumptionComparisonRegression(unittest.TestCase):
    def setUp(self):
        self.did = "33333333-3333-3333-3333-333333333333"
        self.sid = "1"
        now = datetime(2026, 4, 30, 0, 1, tzinfo=timezone.utc)

        def mk_meter(mid, enabled=True, status="ONLINE"):
            return {
                "device_id": self.did,
                "meter_id": mid,
                "enabled": enabled,
                "comm_status": status,
                "serial": f"{mid}-SN",
                "model": mid,
                "last_kwh": None,
                "power_kw": 1.0,
                "ct_ratio": None,
                "max_kw": None,
                "last_seen": now,
                "updated_at": now,
                "attribution": {"status": "ATTRIBUTED", "wing": "A"} if mid == "M1" else None,
            }

        # 2024-01-31 .. 2024-02-29 (30 days, leap year boundary).
        start = date(2024, 1, 31)
        daily = []
        for i in range(30):
            d = start + timedelta(days=i)
            daily.append(
                {
                    "device_id": self.did,
                    "operating_date": d,
                    "reset_period": "2024-01",
                    "status": "OPEN" if d == date(2024, 2, 29) else "CLOSED",
                    "generation_kwh": 100.0,
                    "generation_source": "PHYSICAL",
                    "wing_generation": {"A": 11.0, "B": 22.0, "C": 0.0, "D": 44.0},
                    "wing_consumption": {"A": 1.0, "B": 2.0, "C": 0.0, "D": 4.0},
                    "consumption_source": {"A": "PHYSICAL", "B": "PHYSICAL", "C": "PHYSICAL", "D": "PHYSICAL"},
                    "unattributed_generation_kwh": 0.0,
                    "fault_generation_kwh": 0.0,
                    "gap_kwh": 0.0,
                }
            )

        self.state = {
            "queries": [],
            "commits": 0,
            "rollbacks": 0,
            "closed": 0,
            "users": {"u-admin": "admin@example.com", "u-member": "member@example.com"},
            "device": {
                "id": self.did,
                "energy_bus": {},
                "energy_config_version": 1,
                "energy_calculation_mode": "MANUAL",
                "energy_calculation_version": 9,
                "grid_export_enabled": False,
                "grid_export_limit_kwh": 0,
                "grid_reference_version": 1,
                "reset_day": 15,
            },
            "meters": [
                mk_meter("M1", True, "ONLINE"),
                mk_meter("M2", True, "ONLINE"),
                mk_meter("M3", True, "ONLINE"),
                mk_meter("M4", True, "ONLINE"),
                mk_meter("M5", True, "ONLINE"),
            ],
            "daily": daily,
            "adjustments": [
                {
                    "id": 1,
                    "device_id": self.did,
                    "wing": "A",
                    "operating_date": date(2024, 2, 29),
                    "kind": "MANUAL_GENERATION",
                    "value_kwh": 9999.0,
                    "unit": "kWh",
                    "reason": "must-not-affect-comparison",
                    "created_by": "u-admin",
                    "created_at": now,
                },
                {
                    "id": 2,
                    "device_id": self.did,
                    "wing": "A",
                    "operating_date": date(2024, 2, 29),
                    "kind": "MANUAL_CONSUMPTION",
                    "value_kwh": 8888.0,
                    "unit": "kWh",
                    "reason": "must-not-affect-comparison",
                    "created_by": "u-admin",
                    "created_at": now,
                },
            ],
            "targets": [],
            "commands": [],
            "bills": {
                "A": [
                    {"bill_month": date(2024, 1, 1), "consumption_kwh": 620.0, "note": None, "source": "HISTORICAL", "created_by": None, "updated_by": None, "updated_at": now},
                    {"bill_month": date(2024, 2, 1), "consumption_kwh": 290.0, "note": None, "source": "HISTORICAL", "created_by": None, "updated_by": None, "updated_at": now},
                ],
                "B": [
                    {"bill_month": date(2024, 1, 1), "consumption_kwh": 310.0, "note": None, "source": "HISTORICAL", "created_by": None, "updated_by": None, "updated_at": now},
                    {"bill_month": date(2024, 2, 1), "consumption_kwh": 580.0, "note": None, "source": "HISTORICAL", "created_by": None, "updated_by": None, "updated_at": now},
                ],
                "C": [
                    {"bill_month": date(2024, 1, 1), "consumption_kwh": 0.0, "note": None, "source": "HISTORICAL", "created_by": None, "updated_by": None, "updated_at": now},
                    {"bill_month": date(2024, 2, 1), "consumption_kwh": 0.0, "note": None, "source": "HISTORICAL", "created_by": None, "updated_by": None, "updated_at": now},
                ],
                "D": [
                    # Jan intentionally missing: must remain unavailable on 2024-01-31.
                    {"bill_month": date(2024, 2, 1), "consumption_kwh": 116.0, "note": None, "source": "HISTORICAL", "created_by": None, "updated_by": None, "updated_at": now},
                ],
            },
        }

        for guard in (
            patch.object(socket, "socket", side_effect=AssertionError("offline: socket forbidden")),
            patch.object(psycopg, "connect", side_effect=AssertionError("offline: DB forbidden")),
            patch.object(routes, "datetime", Clock),
        ):
            guard.start()
            self.addCleanup(guard.stop)

        def get_db():
            return ComparisonConn(self.state)

        def get_current_user():
            return {"id": "u-admin", "role": "society_admin", "society_id": self.sid}

        def log_audit(_cur, _user, _society_id, _action, _payload):
            raise AssertionError("comparison GET must not audit/write")

        def require_uuid(v, _field):
            return str(v)

        self.router = routes.create_router(get_db, get_current_user, log_audit, require_uuid)
        self.graph_comparison = self._endpoint("/api/energy/graph/comparison", "GET")

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

    # route contract and tenant/role/day constraints
    def test_graph_comparison_access_days_validation_and_metadata(self):
        out = self.graph_comparison(society_id=self.sid, device_id=self.did, days=30, user=self._admin())
        self.assertEqual(out["device_id"], self.did)
        self.assertEqual((out["mode"], out["version"], out["operating_date"]), ("MANUAL", 9, "2024-02-29"))
        self.assertEqual(out["consumption_basis"], "MONTHLY_BILL_DAILY_REFERENCE")
        self.assertEqual(set(out["wings"].keys()), {"A", "B", "C", "D"})
        self.assertEqual(len(out["society"]["rows"]), 30)

        # read-only member GET is allowed.
        out_member = self.graph_comparison(society_id=self.sid, device_id=self.did, days=7, user=self._member())
        self.assertEqual(out_member["device_id"], self.did)
        self.assertEqual(len(out_member["society"]["rows"]), 7)

        with self.assertRaises(HTTPException) as bad_days:
            self.graph_comparison(society_id=self.sid, device_id=self.did, days=8, user=self._admin())
        self.assertEqual(bad_days.exception.status_code, 400)
        self.assertIn("days must be 7 or 30", bad_days.exception.detail)

        with self.assertRaises(HTTPException) as tenant_err:
            self.graph_comparison(society_id="2", device_id=self.did, days=30, user=self._admin(society_id="1"))
        self.assertEqual(tenant_err.exception.status_code, 403)

        self.assertEqual(self.state["commits"], 0)
        self.assertGreaterEqual(self.state["rollbacks"], 2)

    # MANUAL mode: exact month/day bill-rate, leap-year boundary, missing month stays unavailable, explicit 0 accepted
    def test_manual_comparison_uses_bill_month_daily_rate_only(self):
        out = self.graph_comparison(society_id=self.sid, device_id=self.did, days=30, user=self._admin())
        rows_a = out["wings"]["A"]["rows"]
        rows_b = out["wings"]["B"]["rows"]
        rows_c = out["wings"]["C"]["rows"]
        rows_d = out["wings"]["D"]["rows"]

        jan31 = rows_a[0]
        feb29_a = rows_a[-1]
        feb29_b = rows_b[-1]
        feb29_c = rows_c[-1]

        # Jan31 uses January bill only; no fallback to February.
        self.assertEqual(jan31["date"], "2024-01-31")
        self.assertEqual((jan31["consumed_kwh"], jan31["consumption_source"]), (20.0, "HISTORICAL"))

        # Leap-year February uses /29 (290/29=10, 580/29=20, 0/29=0).
        self.assertEqual(feb29_a["date"], "2024-02-29")
        self.assertEqual((feb29_a["consumed_kwh"], feb29_a["consumption_source"]), (10.0, "HISTORICAL"))
        self.assertEqual((feb29_b["consumed_kwh"], feb29_b["consumption_source"]), (20.0, "HISTORICAL"))
        self.assertEqual((feb29_c["consumed_kwh"], feb29_c["consumption_source"]), (0.0, "HISTORICAL"))  # explicit bill0 is valid

        # Wing D has no January bill: Jan31 must remain unavailable (never fall back).
        self.assertEqual(rows_d[0]["date"], "2024-01-31")
        self.assertIsNone(rows_d[0]["consumed_kwh"])
        self.assertEqual(rows_d[0]["consumption_source"], "UNAVAILABLE")

        # Society consumption requires all 4 wing values present (including valid zero).
        jan31_soc = out["society"]["rows"][0]
        feb29_soc = out["society"]["rows"][-1]
        self.assertIsNone(jan31_soc["consumed_kwh"])
        self.assertEqual(jan31_soc["consumption_source"], "UNAVAILABLE")
        self.assertEqual((feb29_soc["consumed_kwh"], feb29_soc["consumption_source"]), (34.0, "HISTORICAL"))

        # Generation remains physical attribution only in both modes; manual entries must not substitute.
        self.assertEqual((rows_a[-1]["generated_kwh"], rows_a[-1]["generation_source"]), (11.0, "PHYSICAL"))
        self.assertEqual(rows_a[-1]["generation_minus_consumption_kwh"], 1.0)

    def test_calendar_month_lengths_and_bill_refresh_do_not_change_generation(self):
        for day, monthly in ((date(2025, 2, 28), 280), (date(2024, 2, 29), 290),
                             (date(2026, 4, 30), 300), (date(2026, 5, 31), 310)):
            with self.subTest(day=day):
                self.state["daily"] = [{**self.state["daily"][-1], "operating_date": day, "status": "OPEN"}]
                self.state["bills"] = {w: [{"bill_month": day.replace(day=1), "consumption_kwh": monthly}] for w in "ABCD"}
                before = self.graph_comparison(society_id=self.sid, device_id=self.did, days=7, user=self._admin())
                self.assertEqual(before["wings"]["A"]["today"]["consumed_kwh"], 10)
                self.assertEqual(before["society"]["today"]["consumed_kwh"], 40)
                self.assertEqual(before["society"]["today"]["generated_kwh"], 100)
                self.state["bills"]["A"][0]["consumption_kwh"] = monthly * 2
                after = self.graph_comparison(society_id=self.sid, device_id=self.did, days=7, user=self._admin())
                self.assertEqual(after["wings"]["A"]["today"]["consumed_kwh"], 20)
                self.assertEqual(after["wings"]["A"]["today"]["generation_minus_consumption_kwh"], -9)
                self.assertEqual(after["society"]["today"]["consumed_kwh"], 50)
                self.assertEqual(after["society"]["today"]["generated_kwh"], 100)
                self.assertEqual(after["wings"]["A"]["today"]["generated_kwh"], 11)

    def test_both_modes_zero_m1_and_missing_m1_never_use_manual_adjustments(self):
        for mode in ("AUTO", "MANUAL"):
            self.state["device"]["energy_calculation_mode"] = mode
            self.state["meters"][0]["enabled"] = True
            self.state["daily"][-1].update(generation_kwh=0, wing_generation={w: 0 for w in "ABCD"})
            valid = self.graph_comparison(society_id=self.sid, device_id=self.did, days=7, user=self._admin())
            self.assertEqual((valid["society"]["today"]["generated_kwh"], valid["society"]["today"]["generation_source"]), (0, "PHYSICAL"))
            for wing in "ABCD":
                self.assertEqual((valid["wings"][wing]["today"]["generated_kwh"], valid["wings"][wing]["today"]["generation_source"]), (0, "PHYSICAL"))
            self.state["meters"][0]["enabled"] = False
            unavailable = self.graph_comparison(society_id=self.sid, device_id=self.did, days=7, user=self._admin())
            self.assertIsNone(unavailable["society"]["today"]["generated_kwh"])
            self.assertIsNone(unavailable["wings"]["A"]["today"]["generated_kwh"])
            self.assertIsNone(unavailable["society"]["today"]["generation_minus_consumption_kwh"])

    # AUTO mode gating: M1 and per-wing physical qualification/eligibility drive availability.
    def test_auto_comparison_respects_m1_and_consumption_eligibility(self):
        self.state["device"]["energy_calculation_mode"] = "AUTO"
        self.state["meters"][2]["comm_status"] = "OFFLINE"  # M3 / Wing B consumption ineligible

        # Force Wing A to a deficit on today, keep others physical.
        today_row = self.state["daily"][-1]
        today_row["wing_generation"]["A"] = 3.0
        today_row["wing_consumption"]["A"] = 5.0

        out = self.graph_comparison(society_id=self.sid, device_id=self.did, days=7, user=self._admin())
        a_today = out["wings"]["A"]["today"]
        b_today = out["wings"]["B"]["today"]
        society_today = out["society"]["today"]

        self.assertEqual((a_today["generated_kwh"], a_today["consumed_kwh"], a_today["generation_minus_consumption_kwh"]), (3.0, 5.0, -2.0))
        self.assertEqual(a_today["consumption_source"], "PHYSICAL")
        self.assertIsNone(b_today["consumed_kwh"])
        self.assertEqual(b_today["consumption_source"], "UNAVAILABLE")
        self.assertIsNone(society_today["consumed_kwh"], "society requires all four known")

        # M1 disabled or non-physical source/value => society generation unavailable.
        original = dict(self.state["meters"][0])
        original_row = dict(self.state["daily"][-1])
        cases = [
            {"m1_enabled": False, "source": "PHYSICAL", "kwh": 100.0},
            {"m1_enabled": True, "source": "MANUAL", "kwh": 100.0},
            {"m1_enabled": True, "source": "PHYSICAL", "kwh": None},
            {"m1_enabled": True, "source": "PHYSICAL", "kwh": -1.0},
        ]
        for case in cases:
            with self.subTest(case=case):
                self.state["meters"][0]["enabled"] = case["m1_enabled"]
                self.state["daily"][-1]["generation_source"] = case["source"]
                self.state["daily"][-1]["generation_kwh"] = case["kwh"]
                cur = ComparisonCursor(self.state)
                overview = C.overview(cur, self.state["device"], {m["meter_id"]: m for m in self.state["meters"]}, date(2024, 2, 29), 7)
                self.assertIsNone(overview["society"]["today"]["generated_kwh"])
                self.assertEqual(overview["society"]["today"]["generation_source"], "UNAVAILABLE")

        self.state["meters"][0].update(original)
        self.state["daily"][-1].update(original_row)


if __name__ == "__main__":
    unittest.main(verbosity=2)
