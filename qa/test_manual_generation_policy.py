"""Offline policy/provenance regressions for MANUAL_GENERATION authorization and physical qualification.

No live DB/network/device usage.
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
from energy_api import queries as Q  # noqa: E402
from energy_api import routes  # noqa: E402

from qa.test_operating_date_optional_meters import Clock, FakeConn, FakeCursor  # noqa: E402


class ManualGenerationPolicyRegression(unittest.TestCase):
    def setUp(self):
        self.did = "22222222-2222-2222-2222-222222222222"
        now = datetime(2026, 9, 13, 0, 1, tzinfo=timezone.utc)
        self.state = {
            "queries": [],
            "commits": 0,
            "rollbacks": 0,
            "closed": 0,
            "users": {"u-admin": "admin@example.com"},
            "device": {
                "id": self.did,
                "energy_bus": {},
                "energy_config_version": 1,
                "energy_calculation_mode": "AUTO",
                "energy_calculation_version": 1,
                "grid_export_enabled": True,
                "grid_export_limit_kwh": 500.0,
                "grid_reference_version": 1,
                "reset_day": 15,
            },
            "meters": [
                {"device_id": self.did, "meter_id": "M1", "enabled": True, "comm_status": "ONLINE", "serial": "M1", "model": "M", "last_kwh": None, "power_kw": 1.0, "ct_ratio": None, "max_kw": None, "last_seen": now, "updated_at": now, "attribution": {"status": "ATTRIBUTED", "wing": "A"}},
                {"device_id": self.did, "meter_id": "M2", "enabled": True, "comm_status": "ONLINE", "serial": "M2", "model": "M", "last_kwh": None, "power_kw": 1.0, "ct_ratio": None, "max_kw": None, "last_seen": now, "updated_at": now, "attribution": None},
                {"device_id": self.did, "meter_id": "M3", "enabled": True, "comm_status": "ONLINE", "serial": "M3", "model": "M", "last_kwh": None, "power_kw": 1.0, "ct_ratio": None, "max_kw": None, "last_seen": now, "updated_at": now, "attribution": None},
                {"device_id": self.did, "meter_id": "M4", "enabled": True, "comm_status": "ONLINE", "serial": "M4", "model": "M", "last_kwh": None, "power_kw": 1.0, "ct_ratio": None, "max_kw": None, "last_seen": now, "updated_at": now, "attribution": None},
                {"device_id": self.did, "meter_id": "M5", "enabled": True, "comm_status": "ONLINE", "serial": "M5", "model": "M", "last_kwh": None, "power_kw": 1.0, "ct_ratio": None, "max_kw": None, "last_seen": now, "updated_at": now, "attribution": None},
            ],
            "daily": [
                {
                    "device_id": self.did,
                    "operating_date": date(2026, 9, 11),
                    "reset_period": "2026-08",
                    "status": "OPEN",
                    "generation_kwh": 99.0,
                    "generation_source": "PHYSICAL",
                    "wing_generation": {"A": 10.0, "B": 0.0, "C": 0.0, "D": 0.0},
                    "wing_consumption": {"A": 5.0, "B": 2.0, "C": 1.0, "D": 0.0},
                    "consumption_source": {"A": "PHYSICAL", "B": "PHYSICAL", "C": "PHYSICAL", "D": "PHYSICAL"},
                    "unattributed_generation_kwh": 0.0,
                    "fault_generation_kwh": 0.0,
                    "gap_kwh": 0.0,
                },
                {
                    "device_id": self.did,
                    "operating_date": date(2026, 9, 12),
                    "reset_period": "2026-08",
                    "status": "OPEN",
                    "generation_kwh": 10.0,
                    "generation_source": "PHYSICAL",
                    "wing_generation": {"A": 10.0, "B": 0.0, "C": 0.0, "D": 0.0},
                    "wing_consumption": {"A": 5.0, "B": 0.0, "C": 0.0, "D": 0.0},
                    "consumption_source": {"A": "PHYSICAL", "B": "PHYSICAL", "C": "PHYSICAL", "D": "PHYSICAL"},
                    "unattributed_generation_kwh": 0.0,
                    "fault_generation_kwh": 0.0,
                    "gap_kwh": 0.0,
                },
            ],
            "adjustments": [],
            "targets": [{"id": 1, "device_id": self.did, "wing": "A", "target_kwh_per_day": 100.0, "adjustment_percent": 0.0, "base_daily_average_kwh": 100.0, "effective_from": date(2026, 1, 1)}],
            "bills": {"A": [], "B": [], "C": [], "D": []},
            "commands": [{"noop": 1}],
        }
        self.audit = []

        for guard in (
            patch.object(socket, "socket", side_effect=AssertionError("offline: socket forbidden")),
            patch.object(psycopg, "connect", side_effect=AssertionError("offline: DB forbidden")),
            patch.object(routes, "datetime", Clock),
        ):
            guard.start()
            self.addCleanup(guard.stop)

        def get_db():
            return FakeConn(self.state)

        def get_current_user():
            return {"id": "u-admin", "role": "society_admin", "society_id": "1"}

        def log_audit(_cur, user, society_id, action, payload):
            self.audit.append({"user": user["id"], "society_id": society_id, "action": action, "payload": payload})

        def require_uuid(value, _field):
            return str(value)

        self.router = routes.create_router(get_db, get_current_user, log_audit, require_uuid)
        self.post_adjustment = self._endpoint("/api/energy/adjustments", "POST")
        self.summary = self._endpoint("/api/energy/summary", "GET")
        self.history = self._endpoint("/api/energy/history", "GET")
        self.get_adjustments = self._endpoint("/api/energy/adjustments", "GET")

    def _endpoint(self, path, method):
        for r in self.router.routes:
            if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
                return r.endpoint
        raise AssertionError(f"Missing route {method} {path}")

    @staticmethod
    def _admin():
        return {"id": "u-admin", "role": "society_admin", "society_id": "1"}

    def test_manual_generation_accepts_latest_open_only_and_preserves_other_state(self):
        self.state["bills"]["A"] = [{"bill_month": date(2026, 8, 1), "consumption_kwh": 99999.0, "note": None, "source": "HISTORICAL", "created_by": None, "updated_by": None, "updated_at": Clock.now(timezone.utc)}]
        before_daily = deepcopy(self.state["daily"])
        before_bills = deepcopy(self.state["bills"])
        before_targets = deepcopy(self.state["targets"])
        before_device = deepcopy(self.state["device"])
        before_commands = deepcopy(self.state["commands"])

        out = self.post_adjustment(
            data={
                "society_id": "1",
                "device_id": self.did,
                "wing": "A",
                "kind": "MANUAL_GENERATION",
                "operating_date": "2026-09-12",
                "value_kwh": 23,
                "reason": "policy check",
            },
            user=self._admin(),
        )
        self.assertEqual(out["adjustment"]["value_kwh"], 23)
        self.assertEqual(out["adjustment"]["kind"], "MANUAL_GENERATION")
        self.assertEqual(out["adjustment"]["operating_date"], "2026-09-12")
        self.assertEqual(out["adjustment"]["source"], "MANUAL")
        self.assertEqual(self.state["commits"], 1)
        self.assertEqual(self.state["rollbacks"], 0)
        self.assertEqual(self.state["daily"], before_daily)
        self.assertEqual(self.state["bills"], before_bills)
        self.assertEqual(self.state["targets"], before_targets)
        self.assertEqual(self.state["device"], before_device)
        self.assertEqual(self.state["commands"], before_commands)
        self.assertFalse(any(sql.startswith("insert into energy_meters") for sql, _ in self.state["queries"]))
        self.assertTrue(any("for share" in sql for sql, _ in self.state["queries"]))
        self.assertEqual(self.audit[-1]["action"], "ENERGY_ADJUSTMENT")
        self.assertEqual(self.audit[-1]["payload"]["kind"], "MANUAL_GENERATION")
        listed = self.get_adjustments(society_id="1", device_id=self.did, limit=50, user=self._admin())["rows"]
        self.assertEqual((listed[0]["operating_date"], listed[0]["kind"], listed[0]["value_kwh"]), ("2026-09-12", "MANUAL_GENERATION", 23))
        self.state["device"]["energy_calculation_mode"] = "MANUAL"
        summary = self.summary(society_id="1", device_id=self.did, user=self._admin())
        today = summary["calculation"]["wings"]["A"]["today"]
        self.assertEqual((today["generated_kwh"], today["generation_source"]), (23, "MANUAL"))
        self.assertIsNone(today["consumed_kwh"], "MANUAL_GENERATION 23 must not create consumption")
        self.assertEqual(today["consumption_source"], "UNAVAILABLE")
        self.assertEqual(summary["generation_meter"]["generation"]["today"]["kwh"], 10, "common M1 must not include manual wing23")
        self.assertEqual((summary["references"]["allocation"]["generation_kwh"], summary["references"]["allocation"]["generation_source"]), (10, "PHYSICAL"), "allocation uses physical M1, never manual wing23")
        self.post_adjustment(data={"society_id": "1", "device_id": self.did, "wing": "A", "kind": "MANUAL_CONSUMPTION", "operating_date": "2026-09-12", "value_kwh": 7, "reason": "consumption only"}, user=self._admin())
        after = self.summary(society_id="1", device_id=self.did, user=self._admin())["calculation"]["wings"]["A"]["today"]
        self.assertEqual((after["generated_kwh"], after["consumed_kwh"]), (23, 7))
        self.assertEqual(self.state["daily"], before_daily)
        self.assertEqual(self.state["bills"], before_bills)

    def test_manual_generation_rejects_non_open_day_and_uses_pi_day_not_server_day(self):
        with patch.object(Clock, "instant", datetime(2026, 9, 13, 0, 1, tzinfo=timezone.utc)):
            accepted = self.post_adjustment(
                data={"society_id": "1", "device_id": self.did, "wing": "A", "kind": "MANUAL_GENERATION", "operating_date": "2026-09-12", "value_kwh": 1, "reason": "pi day"},
                user=self._admin(),
            )
            self.assertEqual(accepted["adjustment"]["operating_date"], "2026-09-12")

            for bad_date in ("2026-09-11", "2026-09-13"):
                with self.subTest(bad_date=bad_date), self.assertRaises(HTTPException) as err:
                    self.post_adjustment(
                        data={"society_id": "1", "device_id": self.did, "wing": "A", "kind": "MANUAL_GENERATION", "operating_date": bad_date, "value_kwh": 2, "reason": "bad"},
                        user=self._admin(),
                    )
                self.assertEqual(err.exception.status_code, 409)
                self.assertIn("current Pi operating day", err.exception.detail)

        self.state["daily"] = [{**r, "status": "CLOSED"} for r in self.state["daily"]]
        before_adj = len(self.state["adjustments"])
        before_audit = len(self.audit)
        with self.assertRaises(HTTPException) as err2:
            self.post_adjustment(
                data={"society_id": "1", "device_id": self.did, "wing": "A", "kind": "MANUAL_GENERATION", "operating_date": "2026-09-12", "value_kwh": 3, "reason": "closed"},
                user=self._admin(),
            )
        self.assertEqual(err2.exception.status_code, 409)
        self.assertIn("OPEN Pi operating day is unavailable", err2.exception.detail)
        self.assertEqual(len(self.state["adjustments"]), before_adj)
        self.assertEqual(len(self.audit), before_audit)
        self.assertEqual(self.state["commits"], 1)
        self.assertGreaterEqual(self.state["rollbacks"], 3)

    def test_manual_consumption_allows_past_future_without_open_contract(self):
        self.state["daily"] = []
        out_old = self.post_adjustment(
            data={"society_id": "1", "device_id": self.did, "wing": "A", "kind": "MANUAL_CONSUMPTION", "operating_date": "2025-01-01", "value_kwh": 7, "reason": "old"},
            user=self._admin(),
        )
        out_future = self.post_adjustment(
            data={"society_id": "1", "device_id": self.did, "wing": "A", "kind": "MANUAL_CONSUMPTION", "operating_date": "2027-01-01", "value_kwh": 23, "reason": "future"},
            user=self._admin(),
        )
        self.assertEqual(out_old["adjustment"]["kind"], "MANUAL_CONSUMPTION")
        self.assertEqual(out_future["adjustment"]["kind"], "MANUAL_CONSUMPTION")
        self.assertEqual(self.state["commits"], 2)

    def test_calculation_and_legacy_graphs_keep_generation_consumption_independent(self):
        self.state["adjustments"] = [
            {"id": 1, "device_id": self.did, "wing": "A", "operating_date": date(2026, 9, 12), "kind": "MANUAL_GENERATION", "value_kwh": 23.0, "unit": "kWh", "reason": "gen", "created_by": "u-admin", "created_at": datetime(2026, 9, 12, tzinfo=timezone.utc)},
            {"id": 2, "device_id": self.did, "wing": "A", "operating_date": date(2026, 9, 12), "kind": "MANUAL_CONSUMPTION", "value_kwh": 7.0, "unit": "kWh", "reason": "cons", "created_by": "u-admin", "created_at": datetime(2026, 9, 12, tzinfo=timezone.utc)},
            {"id": 3, "device_id": self.did, "wing": "A", "operating_date": date(2026, 9, 13), "kind": "MANUAL_GENERATION", "value_kwh": 0.0, "unit": "kWh", "reason": "zero", "created_by": "u-admin", "created_at": datetime(2026, 9, 13, tzinfo=timezone.utc)},
        ]
        cursor = FakeCursor(self.state)

        legacy = Q.wing_graph_rows(cursor, self.did, "A", date(2026, 9, 12), date(2026, 9, 12), generation_enabled=True, consumption_enabled=True, calculation_mode=None)[0]
        self.assertEqual((legacy["generated_kwh"], legacy["consumed_kwh"]), (33.0, 12.0))
        self.assertEqual((legacy["generation_source"], legacy["consumption_source"], legacy["source"]), ("MIXED", "MIXED", "MIXED"))

        auto = Q.wing_graph_rows(cursor, self.did, "A", date(2026, 9, 12), date(2026, 9, 12), generation_enabled=True, consumption_enabled=True, calculation_mode="AUTO")[0]
        self.assertEqual((auto["generated_kwh"], auto["consumed_kwh"]), (10.0, 5.0))
        self.assertEqual((auto["generation_source"], auto["consumption_source"]), ("PHYSICAL", "PHYSICAL"))

        manual = Q.wing_graph_rows(cursor, self.did, "A", date(2026, 9, 12), date(2026, 9, 12), generation_enabled=True, consumption_enabled=True, calculation_mode="MANUAL")[0]
        self.assertEqual((manual["generated_kwh"], manual["consumed_kwh"]), (23.0, 7.0))
        self.assertEqual((manual["generation_source"], manual["consumption_source"]), ("MANUAL", "MANUAL"))

        zero_day = Q.wing_graph_rows(cursor, self.did, "A", date(2026, 9, 13), date(2026, 9, 13), generation_enabled=True, consumption_enabled=True, calculation_mode="MANUAL")[0]
        self.assertEqual(zero_day["generated_kwh"], 0.0)
        self.assertEqual(zero_day["generation_source"], "MANUAL")

        calc = Q.calculation_view(cursor, self.did, "MANUAL", 1, {"M1": {"enabled": True}, "M2": {"enabled": True, "comm_status": "ONLINE"}, "M3": {"enabled": True, "comm_status": "ONLINE"}, "M4": {"enabled": True, "comm_status": "ONLINE"}, "M5": {"enabled": True, "comm_status": "ONLINE"}}, date(2026, 9, 12))
        self.assertEqual(calc["common_generation_basis"], "PHYSICAL_M1_ONLY")

    def test_physical_qualification_filters_invalid_source_and_numeric_values(self):
        self.state["daily"] = [
            {"device_id": self.did, "operating_date": date(2026, 11, 1), "reset_period": "2026-10", "status": "CLOSED", "generation_kwh": 10.0, "generation_source": "PHYSICAL", "wing_generation": {"A": 10.0, "B": 2.0, "C": 0.0, "D": 0.0}, "wing_consumption": {"A": 5.0, "B": 1.0, "C": 0.0, "D": 0.0}, "consumption_source": {"A": "PHYSICAL", "B": "MANUAL", "C": "PHYSICAL", "D": "PHYSICAL"}, "unattributed_generation_kwh": 0.0, "fault_generation_kwh": 0.0, "gap_kwh": 0.0},
            {"device_id": self.did, "operating_date": date(2026, 11, 2), "reset_period": "2026-10", "status": "CLOSED", "generation_kwh": -1.0, "generation_source": "PHYSICAL", "wing_generation": {"A": -1.0, "B": 0.0, "C": 0.0, "D": 0.0}, "wing_consumption": {"A": -2.0, "B": 0.0, "C": 0.0, "D": 0.0}, "consumption_source": {"A": "PHYSICAL", "B": "MANUAL", "C": "PHYSICAL", "D": "PHYSICAL"}, "unattributed_generation_kwh": 0.0, "fault_generation_kwh": 0.0, "gap_kwh": 0.0},
            {"device_id": self.did, "operating_date": date(2026, 11, 3), "reset_period": "2026-10", "status": "CLOSED", "generation_kwh": float("nan"), "generation_source": "PHYSICAL", "wing_generation": {"A": float("inf"), "B": 0.0, "C": 0.0, "D": 0.0}, "wing_consumption": {"A": float("inf"), "B": 0.0, "C": 0.0, "D": 0.0}, "consumption_source": {"A": "PHYSICAL", "B": "MANUAL", "C": "PHYSICAL", "D": "PHYSICAL"}, "unattributed_generation_kwh": 0.0, "fault_generation_kwh": 0.0, "gap_kwh": 0.0},
            {"device_id": self.did, "operating_date": date(2026, 11, 4), "reset_period": "2026-10", "status": "OPEN", "generation_kwh": 0.0, "generation_source": "PHYSICAL", "wing_generation": {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0}, "wing_consumption": {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0}, "consumption_source": {"A": "PHYSICAL", "B": "MANUAL", "C": "PHYSICAL", "D": "PHYSICAL"}, "unattributed_generation_kwh": 0.0, "fault_generation_kwh": 0.0, "gap_kwh": 0.0},
        ]
        cursor = FakeCursor(self.state)

        periods = Q.metric_periods(cursor, self.did, "generation_kwh", date(2026, 11, 4), 15)
        self.assertEqual(periods["this_month"]["kwh"], 10.0)
        self.assertEqual(periods["this_month"]["days"], 2)

        monthly = Q.monthly_generation(cursor, self.did, date(2026, 11, 1), date(2026, 11, 4), date(2026, 11, 4), True)
        self.assertEqual(monthly[0]["generation_kwh"], 10.0)
        self.assertEqual(monthly[0]["physical_days"], 2)
        self.assertEqual(monthly[0]["source"], "PHYSICAL")

        out = self.history(society_id="1", device_id=self.did, frm="2026-11-01", to="2026-11-30", range=None, granularity="monthly", user=self._admin())
        self.assertEqual(out["rows"][0]["generation_kwh"], 10.0)
        self.assertEqual(out["rows"][0]["wing_generation"]["A"], 10.0)
        self.assertEqual(out["rows"][0]["wing_consumption"]["A"], 5.0)
        self.assertIsNone(out["rows"][0]["wing_consumption"]["B"], "non-PHYSICAL B consumption must be excluded")

    def test_summary_manual_generation_operating_date_null_without_open(self):
        self.state["daily"] = [{**self.state["daily"][0], "status": "CLOSED"}]
        with patch.object(routes, "datetime", Clock):
            out = self.summary(society_id="1", device_id=self.did, user=self._admin())
        self.assertIsNone(out["manual_generation_operating_date"])
        self.assertIsNotNone(out["as_of_operating_date"])

    def test_empty_open_day_cannot_fall_back_to_client_or_utc_and_zero_is_valid(self):
        self.post_adjustment(data={"society_id": "1", "device_id": self.did, "wing": "A", "kind": "MANUAL_GENERATION", "operating_date": "2026-09-12", "value_kwh": 0, "reason": "real zero"}, user=self._admin())
        self.state["device"]["energy_calculation_mode"] = "MANUAL"
        rows = self.summary(society_id="1", device_id=self.did, user=self._admin())["calculation"]["wings"]
        self.assertEqual((rows["A"]["today"]["generated_kwh"], rows["A"]["today"]["generation_source"]), (0, "MANUAL"))
        self.assertIsNone(rows["B"]["today"]["generated_kwh"])
        self.state["daily"] = []
        before = deepcopy({k: self.state[k] for k in ("adjustments", "daily", "bills", "targets", "device", "commands", "commits")})
        audits = deepcopy(self.audit)
        with patch.object(Clock, "instant", datetime(2026, 9, 13, tzinfo=timezone.utc)):
            for posted in ("2026-09-12", "2026-09-13", "2026-09-14"):
                with self.subTest(posted=posted), self.assertRaises(HTTPException) as error:
                    self.post_adjustment(data={"society_id": "1", "device_id": self.did, "wing": "A", "kind": "MANUAL_GENERATION", "operating_date": posted, "value_kwh": 23, "reason": "no open"}, user=self._admin())
                self.assertEqual(error.exception.status_code, 409)
                self.assertIn("OPEN Pi operating day is unavailable", error.exception.detail)
        self.assertEqual({k: self.state[k] for k in before}, before)
        self.assertEqual(self.audit, audits)

    def test_unqualified_generation_never_upgrades_in_any_aggregate_or_legacy_graph(self):
        original = deepcopy(self.state["daily"][-1])
        for source, value in (("UNAVAILABLE", 23), ("MANUAL", 23), ("HISTORICAL", 23), (None, 23), ("PHYSICAL", None), ("PHYSICAL", -23), ("PHYSICAL", float("inf")), ("PHYSICAL", float("-inf")), ("PHYSICAL", float("nan"))):
            with self.subTest(source=source, value=value):
                row = deepcopy(original)
                row.update(generation_kwh=value, generation_source=source, wing_generation={w: value for w in "ABCD"}, unattributed_generation_kwh=value, fault_generation_kwh=value)
                self.state["daily"] = [row]
                cursor = FakeCursor(self.state)
                for metric in ("generation_kwh", "unattributed_generation_kwh", "fault_generation_kwh", Q.wing_generation_expr("A")):
                    periods = Q.metric_periods(cursor, self.did, metric, date(2026, 9, 12), 15)
                    for p in periods.values():
                        self.assertIsNone(p["kwh"])
                        self.assertEqual((p["days"], p["status"]), (0, "UNAVAILABLE"))
                monthly = Q.monthly_generation(cursor, self.did, date(2026, 9, 1), date(2026, 9, 1), date(2026, 9, 12), True)[0]
                self.assertEqual((monthly["generation_kwh"], monthly["source"], monthly["physical_days"]), (None, "UNAVAILABLE", 0))
                history = self.history(society_id="1", device_id=self.did, frm="2026-09-01", to="2026-09-12", range=None, granularity="monthly", user=self._admin())["rows"][0]
                self.assertIsNone(history["generation_kwh"])
                self.assertTrue(all(v is None for v in history["wing_generation"].values()))
                for mode in (None, "AUTO"):
                    graph = Q.wing_graph_rows(cursor, self.did, "A", date(2026, 9, 12), date(2026, 9, 12), True, True, mode)[0]
                    self.assertIsNone(graph["generated_kwh"])
                    self.assertEqual(graph["generation_source"], "UNAVAILABLE")

    def test_legacy_mixed_requires_qualified_physical_and_preserves_physical_zero(self):
        self.state["daily"] = [self.state["daily"][-1]]
        row = self.state["daily"][0]
        row.update(generation_kwh=23, generation_source="UNAVAILABLE", wing_generation={w: 23 for w in "ABCD"})
        self.post_adjustment(data={"society_id": "1", "device_id": self.did, "wing": "A", "kind": "MANUAL_GENERATION", "operating_date": "2026-09-12", "value_kwh": 23, "reason": "manual not physical"}, user=self._admin())
        cursor = FakeCursor(self.state)
        legacy = Q.wing_graph_rows(cursor, self.did, "A", date(2026, 9, 12), date(2026, 9, 12), True, True)[0]
        self.assertEqual((legacy["generated_kwh"], legacy["generation_source"]), (23, "MANUAL"))
        row.update(generation_kwh=0, generation_source="PHYSICAL", wing_generation={w: 0 for w in "ABCD"})
        for metric in ("generation_kwh", Q.wing_generation_expr("A")):
            measured = Q.metric_periods(cursor, self.did, metric, date(2026, 9, 12), 15)["today"]
            self.assertEqual((measured["kwh"], measured["status"], measured["days"]), (0, "PHYSICAL", 1))
        month = Q.monthly_generation(cursor, self.did, date(2026, 9, 1), date(2026, 9, 1), date(2026, 9, 12), True)[0]
        self.assertEqual((month["generation_kwh"], month["source"]), (0, "PHYSICAL"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
