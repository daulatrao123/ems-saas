"""P0 physical-M1 authority for allocation/Grid only; offline, in-memory boundaries.

Real summary/routes/queries/references execute with the existing fake DB and
socket/psycopg guards. No application server, hardware or firmware tests run.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from qa import test_manual_generation_policy as policy


class ManualM1AllocationRegression(unittest.TestCase):
    def setUp(self):
        self.fixture = policy.ManualGenerationPolicyRegression()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.state = self.fixture.state
        self.day = date(2026, 9, 12)
        self.state["device"].update(energy_calculation_mode="MANUAL", grid_export_limit_kwh=100)
        self.state["daily"][-1]["generation_kwh"] = 1000
        target = self.state["targets"][0]
        self.state["targets"] = [
            {**target, "id": i, "wing": wing, "target_kwh_per_day": quota}
            for i, (wing, quota) in enumerate(zip("ABCD", (200, 220, 180, 210)), 1)
        ]

    def summary(self, mode="MANUAL"):
        self.state["device"]["energy_calculation_mode"] = mode
        return self.fixture.summary(society_id="1", device_id=self.fixture.did, user=self.fixture._admin())

    def add_entry(self, wing, value, kind="MANUAL_GENERATION"):
        return self.fixture.post_adjustment(data={
            "society_id": "1", "device_id": self.fixture.did, "wing": wing,
            "operating_date": self.day.isoformat(), "kind": kind,
            "value_kwh": value, "reason": "offline P0 authority regression",
        }, user=self.fixture._admin())

    def test_manual_and_auto_waterfall_and_grid_use_current_physical_m1(self):
        cases = (
            (0, [0, 0, 0, 0], 810, 0, 0),
            (100, [100, 0, 0, 0], 710, 0, 0),
            (300, [200, 100, 0, 0], 510, 0, 0),
            (500, [200, 220, 80, 0], 310, 0, 0),
            (700, [200, 220, 180, 100], 110, 0, 0),
            (810, [200, 220, 180, 210], 0, 0, 0),
            (900, [200, 220, 180, 210], 0, 90, 90),
            (1000, [200, 220, 180, 210], 0, 190, 100),
        )
        for mode in ("AUTO", "MANUAL"):
            for generation, assigned, unmet, excess, exported in cases:
                with self.subTest(mode=mode, generation=generation):
                    self.state["daily"][-1]["generation_kwh"] = generation
                    out = self.summary(mode)
                    allocation = out["references"]["allocation"]
                    self.assertEqual(out["as_of_operating_date"], self.day.isoformat())
                    self.assertEqual(out["generation_meter"]["generation"]["today"]["kwh"], generation)
                    self.assertEqual((allocation["generation_kwh"], allocation["generation_source"]), (generation, "PHYSICAL"))
                    self.assertEqual(allocation["quota_source"], "energy_generation_targets.target_kwh_per_day")
                    self.assertEqual([w["wing"] for w in allocation["wings"]], list("ABCD"))
                    self.assertEqual([w["allocated_kwh"] for w in allocation["wings"]], assigned)
                    self.assertEqual((allocation["required_kwh"], allocation["unmet_kwh"], allocation["excess_kwh"], allocation["grid_allocation_kwh"]), (810, unmet, excess, exported))
                    self.assertEqual(allocation["unassigned_excess_kwh"], excess - exported)
                    self.assertTrue(allocation["all_quotas_known"])
                    self.assertEqual(allocation["all_quotas_satisfied"], generation >= 810)
                    expected_reason = "WING_QUOTAS_UNMET" if generation < 810 else "NO_POSITIVE_EXCESS" if generation == 810 else "REFERENCE_ONLY"
                    self.assertEqual(allocation["reason"], expected_reason)

    def test_issue1_manual_wing_entries_never_change_m1_allocation_or_grid(self):
        before = {mode: self.summary(mode) for mode in ("AUTO", "MANUAL")}
        protected = deepcopy({k: self.state[k] for k in ("daily", "bills", "targets", "meters", "commands")})
        for amounts in ((23, 1, 0, 2), (9000, -5, 0, 400)):
            for wing, value in zip("ABCD", amounts):
                self.add_entry(wing, value)
            for mode in ("AUTO", "MANUAL"):
                out = self.summary(mode)
                self.assertEqual(out["generation_meter"], before[mode]["generation_meter"])
                self.assertEqual(out["references"], before[mode]["references"])
                self.assertEqual(out["calculation"]["common_generation_basis"], "PHYSICAL_M1_ONLY")
        self.assertEqual({k: self.state[k] for k in protected}, protected)
        self.add_entry("A", 7, kind="MANUAL_CONSUMPTION")
        manual = self.summary()
        self.assertEqual(manual["references"], before["MANUAL"]["references"])
        self.assertEqual([manual["calculation"]["wings"][w]["today"]["generated_kwh"] for w in "ABCD"], [9023, -4, 0, 402])
        for w in "ABCD":
            self.assertEqual(manual["calculation"]["wings"][w]["today"]["generation_source"], "MANUAL")
            self.assertIsNone(manual["calculation"]["wings"][w]["today"]["required_kwh"])
        today = manual["calculation"]["wings"]["A"]["today"]
        self.assertEqual((today["consumed_kwh"], today["consumption_source"]), (7, "MANUAL"))
        auto = self.summary("AUTO")
        self.assertEqual(auto["calculation"], before["AUTO"]["calculation"])

    def test_unavailable_or_unqualified_m1_never_falls_back_to_manual_entries(self):
        for wing in "ABCD":
            self.add_entry(wing, 9000)
        current = deepcopy(self.state["daily"][-1])
        cases = [("UNAVAILABLE", 1000), ("MANUAL", 1000), ("MIXED", 1000), ("HISTORICAL", 1000), (None, 1000),
                 ("PHYSICAL", None), ("PHYSICAL", -1), ("PHYSICAL", float("nan")),
                 ("PHYSICAL", float("inf")), ("PHYSICAL", float("-inf"))]
        for mode in ("AUTO", "MANUAL"):
            for source, value in cases:
                with self.subTest(mode=mode, source=source, value=value):
                    self.state["daily"][-1] = {**current, "generation_source": source, "generation_kwh": value}
                    self.assert_unavailable(self.summary(mode))
            self.state["daily"][-1] = deepcopy(current)
            self.state["meters"][0]["enabled"] = False
            self.assert_unavailable(self.summary(mode))
            self.state["meters"][0]["enabled"] = True
        self.state["daily"] = []
        # Existing read-model date fallback retained; same-day manual entries still
        # exist, but cannot supply M1 generation when the ledger row is missing.
        with patch.object(policy.Clock, "instant", datetime(2026, 9, 12, tzinfo=timezone.utc)):
            for mode in ("AUTO", "MANUAL"):
                out = self.summary(mode)
                self.assert_unavailable(out)
                if mode == "MANUAL":
                    self.assertEqual(out["calculation"]["wings"]["A"]["today"]["generated_kwh"], 9000)

    def assert_unavailable(self, out):
        allocation = out["references"]["allocation"]
        self.assertEqual((allocation["generation_kwh"], allocation["generation_source"]), (None, "UNAVAILABLE"))
        self.assertEqual((allocation["grid_allocation_kwh"], allocation["status"], allocation["reason"]), (0, "UNAVAILABLE", "SOCIETY_GENERATION_UNAVAILABLE"))
        self.assertIsNone(allocation["excess_kwh"])
        self.assertIsNone(allocation["unmet_kwh"])
        self.assertEqual([w["allocated_kwh"] for w in allocation["wings"]], [None] * 4)
        self.assertFalse(allocation["all_quotas_satisfied"])

    def test_missing_or_invalid_quota_blocks_downstream_and_grid(self):
        targets = deepcopy(self.state["targets"])
        for mode in ("AUTO", "MANUAL"):
            for index, wing in enumerate("ABCD"):
                for invalid in ("missing", None, -1, float("nan"), float("inf")):
                    with self.subTest(mode=mode, wing=wing, invalid=invalid):
                        self.state["targets"] = deepcopy(targets)
                        if invalid == "missing":
                            self.state["targets"].pop(index)
                        else:
                            self.state["targets"][index]["target_kwh_per_day"] = invalid
                        # Invalid target numbers are exercised at the reference
                        # boundary, not unrelated operational-card serialization.
                        self.state["device"]["energy_calculation_mode"] = mode
                        allocation = policy.routes.R.overview(
                            policy.FakeCursor(self.state), self.state["device"],
                            {m["meter_id"]: m for m in self.state["meters"]}, self.day,
                            calendar_today=policy.Clock.now(timezone.utc).date(),
                        )["allocation"]
                        self.assertEqual((allocation["generation_kwh"], allocation["generation_source"]), (1000, "PHYSICAL"))
                        self.assertEqual((allocation["status"], allocation["reason"], allocation["grid_allocation_kwh"]), ("UNAVAILABLE", "REQUIRED_WING_QUOTA_UNAVAILABLE", 0))
                        self.assertFalse(allocation["all_quotas_known"])
                        self.assertFalse(allocation["all_quotas_satisfied"])
                        for field in ("required_kwh", "unmet_kwh", "excess_kwh"):
                            self.assertIsNone(allocation[field])
                        self.assertEqual([w["allocated_kwh"] for w in allocation["wings"]], [200, 220, 180, 210][:index] + [None] * (4 - index))

    def test_grid_enablement_and_limit_gates_preserved_in_both_modes(self):
        for mode in ("AUTO", "MANUAL"):
            for enabled, limit, exported, status, reason in (
                (True, 50, 50, "ELIGIBLE", "REFERENCE_ONLY"),
                (True, 500, 190, "ELIGIBLE", "REFERENCE_ONLY"),
                (False, 500, 0, "NOT_ELIGIBLE", "GRID_EXPORT_DISABLED"),
                (True, 0, 0, "NOT_ELIGIBLE", "GRID_LIMIT_ZERO"),
                (True, None, 0, "UNAVAILABLE", "GRID_LIMIT_UNAVAILABLE"),
            ):
                with self.subTest(mode=mode, enabled=enabled, limit=limit):
                    self.state["device"].update(grid_export_enabled=enabled, grid_export_limit_kwh=limit)
                    allocation = self.summary(mode)["references"]["allocation"]
                    self.assertEqual((allocation["required_kwh"], allocation["unmet_kwh"], allocation["excess_kwh"]), (810, 0, 190))
                    self.assertEqual((allocation["grid_allocation_kwh"], allocation["status"], allocation["reason"]), (exported, status, reason))

    def test_latest_operating_day_targets_remain_authoritative_not_other_sources(self):
        target = deepcopy(self.state["targets"][0])
        self.state["targets"] += [
            {**target, "id": 10, "target_kwh_per_day": 1, "effective_from": date(2025, 12, 31)},
            {**target, "id": 11, "target_kwh_per_day": 9999, "effective_from": date(2026, 9, 13)},
        ]
        # Neither configuration manual targets nor bill references replace DB quotas.
        self.state["device"]["energy_allocation"] = {"wings": {w: {"manual_target_kwh": 1} for w in "ABCD"}}
        self.state["bills"] = {w: [{"bill_month": date(2026, 8, 1), "consumption_kwh": 31000,
                                  "note": None, "source": "HISTORICAL", "created_by": None,
                                  "updated_by": None, "updated_at": datetime(2026, 8, 31, tzinfo=timezone.utc)}] for w in "ABCD"}
        for mode in ("AUTO", "MANUAL"):
            refs = self.summary(mode)["references"]
            self.assertEqual(refs["society_historical_daily_kwh"], 4000)
            self.assertEqual(refs["allocation"]["required_kwh"], 810)
            self.assertEqual(refs["allocation"]["grid_allocation_kwh"], 100)

    def test_zero_quotas_and_decimal_boundary_remain_valid(self):
        for mode in ("AUTO", "MANUAL"):
            for generation, quotas, required, excess, exported in (
                (Decimal("1.0"), (Decimal("0.1"), Decimal("0.2"), Decimal("0.3"), Decimal("0.4")), 1, 0, 0),
                (0, (0, 0, 0, 0), 0, 0, 0),
                (1000, (0, 0, 0, 0), 0, 1000, 100),
            ):
                with self.subTest(mode=mode, generation=generation, quotas=quotas):
                    self.state["daily"][-1]["generation_kwh"] = generation
                    for target, quota in zip(self.state["targets"], quotas):
                        target["target_kwh_per_day"] = quota
                    allocation = self.summary(mode)["references"]["allocation"]
                    self.assertTrue(allocation["all_quotas_known"])
                    self.assertTrue(allocation["all_quotas_satisfied"])
                    self.assertEqual(allocation["generation_source"], "PHYSICAL")
                    self.assertEqual((allocation["required_kwh"], allocation["unmet_kwh"], allocation["excess_kwh"], allocation["grid_allocation_kwh"]), (required, 0, excess, exported))

    def test_existing_m1_communication_and_read_date_policy_is_not_tightened(self):
        for status in ("ONLINE", "OFFLINE", "STALE", "UNKNOWN", "ERROR", "FAULT"):
            self.state["meters"][0]["comm_status"] = status
            with self.subTest(status=status):
                self.assertEqual(self.summary()["references"]["allocation"], self.summary("AUTO")["references"]["allocation"])
                self.assertEqual(self.summary()["references"]["allocation"]["generation_kwh"], 1000)
        self.state["daily"] = [{**self.state["daily"][-1], "status": "CLOSED"}]
        with patch.object(policy.Clock, "instant", datetime(2026, 9, 12, tzinfo=timezone.utc)):
            out = self.summary()
            self.assertIsNone(out["manual_generation_operating_date"])
            self.assertEqual(out["references"]["allocation"]["generation_kwh"], 1000)


if __name__ == "__main__":
    unittest.main(verbosity=2)