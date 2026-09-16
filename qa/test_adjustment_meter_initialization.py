"""Offline regression for adjustment-kind meter initialization; no live API/DB."""
import unittest
from copy import deepcopy
from unittest.mock import patch

from fastapi import HTTPException
from qa import test_manual_generation_policy as fixtures


class AdjustmentMeterInitialization(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ManualGenerationPolicyRegression()
        self.fixture.setUp()  # Existing fake connection and socket/DB prohibitions.
        self.addCleanup(self.fixture.doCleanups)
        spy = patch.object(fixtures.routes, "ensure_meter_rows", wraps=fixtures.routes.ensure_meter_rows)
        self.ensure = spy.start()
        self.addCleanup(spy.stop)

    def post(self, kind, day, value=23, wing="A"):
        return self.fixture.post_adjustment(data={
            "society_id": "1", "device_id": self.fixture.did, "wing": wing,
            "kind": kind, "operating_date": day, "value_kwh": value,
            "reason": "initialization scope regression",
        }, user=self.fixture._admin())

    def test_manual_generation_never_initializes_meters_and_keeps_date_policy(self):
        state = self.fixture.state
        opened = deepcopy(state["daily"])
        cases = [("missing", "2026-09-12", False), ("closed", "2026-09-12", False),
                 ("open", "2026-09-11", False), ("open", "2026-09-13", False),
                 ("open", "2026-09-12", True)]
        for status, day, accepted in cases:
            with self.subTest(status=status, day=day):
                state["daily"] = [] if status == "missing" else [
                    {**r, "status": "CLOSED" if status == "closed" else r["status"]} for r in opened]
                before_rows, before_audit = deepcopy(state["adjustments"]), deepcopy(self.fixture.audit)
                before_daily, before_commits = deepcopy(state["daily"]), state["commits"]
                before_rollbacks = state["rollbacks"]
                if accepted:
                    out = self.post("MANUAL_GENERATION", day)
                    self.assertEqual(out["adjustment"]["operating_date"], day)
                    self.assertEqual(out["adjustment"]["value_kwh"], 23)
                    self.assertEqual(state["commits"], before_commits + 1)
                else:
                    with self.assertRaises(HTTPException) as error:
                        self.post("MANUAL_GENERATION", day)
                    self.assertEqual(error.exception.status_code, 409)
                    message = "OPEN Pi operating day is unavailable" if status != "open" else "current Pi operating day"
                    self.assertIn(message, error.exception.detail)
                    self.assertEqual(state["adjustments"], before_rows)
                    self.assertEqual(self.fixture.audit, before_audit)
                    self.assertEqual(state["commits"], before_commits)
                    self.assertEqual(state["rollbacks"], before_rollbacks + 1)
                self.ensure.assert_not_called()
                self.assertFalse(any(q.startswith("insert into energy_meters") for q, _ in state["queries"]))
                self.assertEqual(state["daily"], before_daily)

    def check_legacy_kind(self, kind, wing):
        state = self.fixture.state
        state["daily"] = []  # Neither consumption nor accounting requires OPEN.
        for day, value in (("2025-01-01", -7), ("2027-01-01", 23), ("2027-01-02", 0)):
            with self.subTest(kind=kind, day=day, value=value):
                self.ensure.reset_mock()
                query_start, commits = len(state["queries"]), state["commits"]
                out = self.post(kind, day, value, wing)
                self.ensure.assert_called_once()
                self.assertEqual(self.ensure.call_args.args[1], self.fixture.did)
                calls = state["queries"][query_start:]
                inserts = [(q, p) for q, p in calls if q.startswith("insert into energy_meters")]
                self.assertEqual(len(inserts), 1)
                self.assertEqual(list(inserts[0][1][1::4]), ["M1", "M2", "M3", "M4", "M5"])
                adjustment_index = next(i for i, (q, _) in enumerate(calls) if q.startswith("insert into energy_adjustments"))
                self.assertEqual(sum(q.startswith("insert into energy_meters") for q, _ in calls[:adjustment_index]), 1)
                self.assertFalse(any("status='open'" in q for q, _ in calls))
                row = out["adjustment"]
                self.assertEqual((row["kind"], row["operating_date"], row["value_kwh"], row["wing"]), (kind, day, value, wing))
                self.assertEqual(row["source"], "MANUAL")
                self.assertEqual(state["commits"], commits + 1)
                self.assertEqual(state["daily"], [])
                self.assertEqual(self.fixture.audit[-1]["payload"]["kind"], kind)

    def test_manual_consumption_preserves_initialization_and_dates_values(self):
        self.check_legacy_kind("MANUAL_CONSUMPTION", "A")

    def test_accounting_preserves_initialization_optional_wing_dates_values(self):
        self.check_legacy_kind("ACCOUNTING", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)