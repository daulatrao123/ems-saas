"""Offline read-model regression for logical consumption scope and references.

No live DB/network/server usage. Uses in-memory FakeCursor boundaries.
"""

from __future__ import annotations

import sys
import unittest
import socket
import psycopg
from unittest.mock import patch
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from energy_api import comparison as C  # noqa: E402
from energy_api import references as R  # noqa: E402

from qa.test_operating_date_optional_meters import FakeCursor  # noqa: E402


def base_state():
    did = "77777777-7777-7777-7777-777777777777"
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    daily = []
    for day in range(1, 31):
        d = date(2026, 9, day)
        daily.append(
            {
                "device_id": did,
                "operating_date": d,
                "reset_period": "2026-09",
                "status": "OPEN" if day == 30 else "CLOSED",
                "generation_kwh": 100.0,
                "generation_source": "PHYSICAL",
                "wing_generation": {"A": 10.0, "B": 20.0, "C": 0.0, "D": 40.0},
                "wing_consumption": {"A": 1.0, "B": 2.0, "C": 0.0, "D": 4.0},
                "consumption_source": {"A": "PHYSICAL", "B": "PHYSICAL", "C": "PHYSICAL", "D": "PHYSICAL"},
                "unattributed_generation_kwh": 0.0,
                "fault_generation_kwh": 0.0,
                "gap_kwh": 0.0,
            }
        )
    return {
        "queries": [],
        "slot_configs": [],
        "adjustments": [],
        "users": {},
        "daily": daily,
        "targets": [
            {"id": 1, "device_id": did, "wing": "A", "target_kwh_per_day": 200.0, "adjustment_percent": 0.0, "base_daily_average_kwh": 200.0, "effective_from": date(2026, 1, 1)},
            {"id": 2, "device_id": did, "wing": "B", "target_kwh_per_day": 220.0, "adjustment_percent": 0.0, "base_daily_average_kwh": 220.0, "effective_from": date(2026, 1, 1)},
            {"id": 3, "device_id": did, "wing": "C", "target_kwh_per_day": 180.0, "adjustment_percent": 0.0, "base_daily_average_kwh": 180.0, "effective_from": date(2026, 1, 1)},
            {"id": 4, "device_id": did, "wing": "D", "target_kwh_per_day": 210.0, "adjustment_percent": 0.0, "base_daily_average_kwh": 210.0, "effective_from": date(2026, 1, 1)},
        ],
        "bills": {
            "A": [{"bill_month": date(2026, 9, 1), "consumption_kwh": 300.0, "note": None, "source": "HISTORICAL", "created_by": None, "updated_by": None, "updated_at": now}],
            "B": [{"bill_month": date(2026, 9, 1), "consumption_kwh": 600.0, "note": None, "source": "HISTORICAL", "created_by": None, "updated_by": None, "updated_at": now}],
            "C": [{"bill_month": date(2026, 9, 1), "consumption_kwh": 0.0, "note": None, "source": "HISTORICAL", "created_by": None, "updated_by": None, "updated_at": now}],
            "D": [{"bill_month": date(2026, 9, 1), "consumption_kwh": 120.0, "note": None, "source": "HISTORICAL", "created_by": None, "updated_by": None, "updated_at": now}],
        },
        "device": {
            "id": did,
            "energy_calculation_mode": "MANUAL",
            "energy_calculation_version": 3,
            "grid_export_enabled": True,
            "grid_export_limit_kwh": 100.0,
            "grid_reference_version": 1,
        },
        "meters": {
            "M1": {"enabled": True, "comm_status": "ONLINE"},
            "M2": {"enabled": True, "comm_status": "ONLINE"},
            "M3": {"enabled": True, "comm_status": "ONLINE"},
            "M4": {"enabled": True, "comm_status": "ONLINE"},
            "M5": {"enabled": True, "comm_status": "ONLINE"},
        },
    }


class ConsumptionScopeReadModelRegression(unittest.TestCase):
    def setUp(self):
        for guard in (patch.object(socket, "socket", side_effect=AssertionError("offline: socket forbidden")),
                      patch.object(psycopg, "connect", side_effect=AssertionError("offline: DB forbidden"))):
            guard.start()
            self.addCleanup(guard.stop)

    def test_explicit_disabled_excludes_only_that_wing_and_keeps_zero_bill(self):
        state = base_state()
        state["slot_configs"] = [{"slot": "D", "disabled": True}]
        out = C.overview(FakeCursor(state), state["device"], state["meters"], date(2026, 9, 30), 30)
        self.assertEqual(out["included_wings"], ["A", "B", "C"])
        self.assertEqual(out["excluded_wings"], ["D"])
        self.assertEqual(out["scope_basis"], "CURRENT_LOGICAL_CONFIGURATION")
        self.assertEqual(out["society"]["today"]["consumed_kwh"], 30.0)  # 10 + 20 + 0
        self.assertEqual(out["wings"]["C"]["today"]["consumed_kwh"], 0.0)
        # User's exact failure: D has NO bill, not just an excluded valid bill.
        state["bills"]["D"] = []
        out = C.overview(FakeCursor(state), state["device"], state["meters"], date(2026, 9, 30), 30)
        self.assertEqual(out["society"]["today"]["consumed_kwh"], 30.0)
        self.assertIsNone(out["wings"]["D"]["today"]["consumed_kwh"])
        self.assertEqual(out["society"]["today"]["missing_consumption_wings"], [])
        month = C.month_overview(FakeCursor(state), state["device"], state["meters"], date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 15))
        self.assertEqual(month["operating_date"], "2026-08-31")
        self.assertEqual(month["included_wings"], ["A", "B", "C"])
        self.assertEqual(month["society"]["rows"][8]["consumed_kwh"], 30.0)

    def test_enabled_or_missing_config_d_still_required_and_missing_bill_stays_unavailable(self):
        state = base_state()
        state["bills"]["D"] = []
        for slot_configs in ([], [{"slot": "D", "disabled": False}]):
            with self.subTest(slot_configs=slot_configs):
                state["slot_configs"] = slot_configs
                out = C.overview(FakeCursor(state), state["device"], state["meters"], date(2026, 9, 30), 30)
                self.assertEqual(out["included_wings"], ["A", "B", "C", "D"])
                self.assertIsNone(out["wings"]["D"]["today"]["consumed_kwh"])
                self.assertIsNone(out["society"]["today"]["consumed_kwh"])
                self.assertIn("D", out["society"]["today"]["missing_consumption_wings"])

    def test_all_disabled_does_not_fabricate_zero_and_disabled_wing_history_is_retained(self):
        state = base_state()
        state["slot_configs"] = [{"slot": w, "disabled": True} for w in "ABCD"]
        out = C.overview(FakeCursor(state), state["device"], state["meters"], date(2026, 9, 30), 30)
        self.assertEqual(out["included_wings"], [])
        self.assertIsNone(out["society"]["today"]["consumed_kwh"])
        self.assertEqual(out["society"]["today"]["consumption_reason"], "NO_ENABLED_WINGS")
        self.assertEqual(out["wings"]["D"]["today"]["consumed_kwh"], 4.0)
        refs = R.overview(FakeCursor(state), state["device"], state["meters"], date(2026, 9, 30), calendar_today=date(2026, 9, 30))
        self.assertIsNone(refs["society_historical_daily_kwh"])
        self.assertIsNone(refs["society_reference_daily_kwh"])
        self.assertEqual(refs["society_reference_source"], "UNAVAILABLE")

    def test_auto_offline_meter_is_not_logical_disable_and_sums_remain_scoped(self):
        state = base_state()
        state["device"]["energy_calculation_mode"] = "AUTO"
        state["meters"]["M5"] = {"enabled": True, "comm_status": "OFFLINE"}
        state["slot_configs"] = [{"slot": "D", "disabled": True}]
        for days in (7, 30):
            with self.subTest(days=days):
                out = C.overview(FakeCursor(state), state["device"], state["meters"], date(2026, 9, 30), days)
                self.assertEqual(out["included_wings"], ["A", "B", "C"])
                self.assertIsNone(out["wings"]["D"]["today"]["consumed_kwh"])  # meter ineligible, not logical scope
                self.assertEqual(out["society"]["today"]["consumed_kwh"], 3.0)  # A+B+C physical
        state["slot_configs"] = []
        self.assertIsNone(C.overview(FakeCursor(state), state["device"], state["meters"], date(2026, 9, 30), 7)["society"]["today"]["consumed_kwh"])

    def test_references_keep_m1_and_allocation_authority_independent_of_scope(self):
        state = base_state()
        state["slot_configs"] = [{"slot": "D", "disabled": True}]
        refs = R.overview(FakeCursor(state), deepcopy(state["device"]), state["meters"], date(2026, 9, 30), calendar_today=date(2026, 9, 30))
        allocation = refs["allocation"]
        self.assertEqual((allocation["generation_kwh"], allocation["generation_source"] if "generation_source" in allocation else "PHYSICAL"), (100.0, "PHYSICAL"))
        self.assertEqual(allocation["required_kwh"], 810.0)
        self.assertEqual(allocation["reason"], "WING_QUOTAS_UNMET")
        self.assertEqual(refs["included_wings"], ["A", "B", "C"])
        self.assertEqual(refs["society_historical_daily_kwh"], 30.0)
        self.assertEqual(refs["society_reference_daily_kwh"], 30.0)
        state["bills"]["C"] = []
        missing = R.overview(FakeCursor(state), state["device"], state["meters"], date(2026, 9, 30), calendar_today=date(2026, 9, 30))
        self.assertIsNone(missing["society_historical_daily_kwh"])
        state["meters"]["M4"]["enabled"] = False
        missing = R.overview(FakeCursor(state), state["device"], state["meters"], date(2026, 9, 30), calendar_today=date(2026, 9, 30))
        self.assertIsNone(missing["society_reference_daily_kwh"])
        writes = [sql for sql, _ in state["queries"] if sql.startswith("insert ") or sql.startswith("update ") or sql.startswith("delete ")]
        self.assertEqual(writes, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
