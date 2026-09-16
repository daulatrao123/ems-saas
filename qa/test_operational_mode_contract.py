"""Cross-API operational M1 contract. In-memory fixtures only, no main startup."""
from copy import deepcopy
from datetime import date
import unittest

from qa import test_mode_consumption_comparison as fixtures

Q, ComparisonCursor = fixtures.C.Q, fixtures.ComparisonCursor


class OperationalModeContractTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.ModeConsumptionComparisonRegression()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)

    def views(self):
        f = self.fx
        comparison = f.graph_comparison(society_id=f.sid, device_id=f.did, days=7, user=f._admin())
        graph = f._endpoint("/api/energy/graph/wing", "GET")(
            society_id=f.sid, device_id=f.did, wing="A", range="7d", frm=None, to=None,
            basis="calculation", user=f._admin())
        summary = f._endpoint("/api/energy/summary", "GET")(
            society_id=f.sid, device_id=f.did, user=f._admin())
        return comparison["wings"]["A"]["today"], graph["rows"][-1], summary["calculation"]["wings"]["A"]["today"]

    def test_all_operational_apis_share_the_same_contract_in_both_modes(self):
        f = self.fx
        before = deepcopy({k: f.state[k] for k in ("daily", "adjustments", "bills", "targets")})
        for mode, expected_cons in (("AUTO", 1.0), ("MANUAL", 10.0)):
            f.state["device"]["energy_calculation_mode"] = mode
            rows = self.views()
            self.assertEqual(rows[0], rows[1])
            self.assertEqual(rows[0], rows[2])
            self.assertEqual((rows[0]["generated_kwh"], rows[0]["generation_source"]), (11, "PHYSICAL"))
            self.assertEqual(rows[0]["consumed_kwh"], expected_cons)
        self.assertEqual({k: f.state[k] for k in before}, before)

    def test_bill_edits_change_only_manual_consumption_and_missing_month_stays_unavailable(self):
        f = self.fx
        original = deepcopy(f.state["bills"]["A"][-1])
        for monthly, expected in ((580, 20), (0, 0), (None, None)):
            f.state["bills"]["A"] = [] if monthly is None else [{**original, "consumption_kwh": monthly}]
            for row in self.views():
                self.assertEqual((row["generated_kwh"], row["generation_source"]), (11, "PHYSICAL"))
                self.assertEqual(row["consumed_kwh"], expected)
                self.assertEqual(row["generation_minus_consumption_kwh"], 11 - expected if expected is not None else None)

    def test_disabled_missing_invalid_and_zero_m1_are_identical_across_endpoints(self):
        f = self.fx
        for mode in ("AUTO", "MANUAL"):
            f.state["device"]["energy_calculation_mode"] = mode
            for enabled, source, value, expected in (
                (True, "PHYSICAL", 0, 0), (False, "PHYSICAL", 11, None),
                (True, "UNAVAILABLE", 11, None), (True, "MANUAL", 11, None),
                (True, "PHYSICAL", None, None), (True, "PHYSICAL", -1, None),
                (True, "PHYSICAL", float("nan"), None), (True, "PHYSICAL", float("inf"), None)):
                with self.subTest(mode=mode, enabled=enabled, source=source, value=value):
                    f.state["meters"][0]["enabled"] = enabled
                    f.state["daily"][-1].update(generation_source=source, generation_kwh=value,
                                               wing_generation={w: value for w in "ABCD"})
                    for row in self.views():
                        self.assertEqual(row["generated_kwh"], expected)
                        self.assertEqual(row["generation_source"], "PHYSICAL" if expected is not None else "UNAVAILABLE")

    def test_legacy_accounting_remains_explicit_and_unchanged(self):
        f = self.fx
        out = f._endpoint("/api/energy/graph/wing", "GET")(
            society_id=f.sid, device_id=f.did, wing="A", range="7d", frm=None, to=None,
            basis="legacy", user=f._admin())
        row = out["rows"][-1]
        self.assertEqual(out["contract"], "LEGACY_ADDITIVE_ACCOUNTING")
        self.assertEqual(row["generated_kwh"], 10010)
        self.assertEqual(row["generation_source"], "MIXED")
        self.assertEqual(row["consumed_kwh"], 8889)

    def test_operational_queries_never_read_adjustments_and_unknown_mode_fails(self):
        f = self.fx
        for mode in ("AUTO", "MANUAL"):
            f.state["queries"] = []
            Q.wing_graph_rows(ComparisonCursor(f.state), f.did, "A", date(2024, 2, 29), date(2024, 2, 29), True, True, mode)
            self.assertFalse(any("from energy_adjustments" in sql for sql, _ in f.state["queries"]))
        with self.assertRaises(ValueError):
            Q.wing_graph_rows(ComparisonCursor(f.state), f.did, "A", date(2024, 2, 29), date(2024, 2, 29), True, True, "GUESS")


if __name__ == "__main__":
    unittest.main(verbosity=2)