"""Actual commissioning endpoint functions with transactional memory cursors only."""
from copy import deepcopy
import calendar
from datetime import date
from types import SimpleNamespace
import unittest
from fastapi import HTTPException

from qa import test_mode_consumption_comparison as fixtures
from energy_api.routes import create_router
from energy_api.commissioning import register_problems


class Cursor(fixtures.ComparisonCursor):
    def execute(self, sql, params=()):
        q = " ".join(sql.lower().split())
        self._one, self._rows = None, []
        if q.startswith("select energy_allocation"):
            self._one = {"energy_allocation": self.state["allocation"]}
        elif "from energy_bill_history" in q and "limit 6" in q:
            self._rows = sorted(deepcopy(self.state["bills"].get(params[1], [])), key=lambda r: r["bill_month"], reverse=True)[:6]
            for row in self._rows:
                row.setdefault("days", calendar.monthrange(row["bill_month"].year, row["bill_month"].month)[1])
        elif q.startswith("select * from energy_meters") and "meter_id=%s" in q:
            self._one = next(dict(m) for m in self.state["meters"] if m["meter_id"] == params[1])
        elif q.startswith("select meter_id from energy_meters"):
            key = "serial" if "serial=%s" in q else "modbus_address"
            match = next((m for m in self.state["meters"] if m.get(key) == params[1] and m["meter_id"] != params[2] and (key == "serial" or m["enabled"])), None)
            self._one = {"meter_id": match["meter_id"]} if match else None
        elif q.startswith("update energy_meters set"):
            meter = next(m for m in self.state["meters"] if m["meter_id"] == params[-1])
            if "comm_status='disabled'" in q: meter["comm_status"] = "DISABLED"
            else:
                keys = [chunk.strip().split("=")[0] for chunk in q.split(" set ")[1].split(" where ")[0].split(",") if "%s" in chunk]
                meter.update({k: deepcopy(v.obj if hasattr(v, "obj") else v) for k, v in zip(keys, params)})
        elif q.startswith("update pi_devices set energy_bus="):
            self.state["device"]["energy_bus"] = deepcopy(params[0].obj)
            self.state["device"]["energy_config_version"] += 1
            self._one = {"energy_config_version": self.state["device"]["energy_config_version"]}
        elif q.startswith("update pi_devices set energy_config_version=energy_config_version+1"):
            self.state["device"]["energy_config_version"] += 1
            self._one = {"energy_config_version": self.state["device"]["energy_config_version"]}
        elif q.startswith("select * from energy_generation_targets"):
            rows = sorted([r for r in self.state["targets"] if r["wing"] == params[1] and (len(params) == 2 or r["effective_from"] <= params[2])], key=lambda r: (r["effective_from"], r["id"]), reverse=True)
            self._rows = deepcopy(rows); self._one = deepcopy(rows[0]) if rows else None
        elif q.startswith("insert into energy_generation_targets"):
            keys = ("device_id", "wing", "base_daily_average_kwh", "adjustment_percent", "target_kwh_per_day", "basis", "effective_from", "reason", "created_by")
            row = dict(zip(keys, (v.obj if hasattr(v, "obj") else v for v in params)))
            row.update(id=len(self.state["targets"]) + 1, created_at=fixtures.datetime(2024, 2, 29, tzinfo=fixtures.timezone.utc))
            self.state["targets"].append(row); self._one = deepcopy(row)
        elif q.startswith("select distinct on (wing)"):
            candidates = [r for r in self.state["targets"] if r["device_id"] == params[0] and r["effective_from"] <= params[1]]
            latest = {}
            for r in sorted(candidates, key=lambda r: (r["effective_from"], r["id"])):
                latest[r["wing"]] = r
            self._rows = [deepcopy(latest[w]) for w in sorted(latest)]
        elif q.startswith("select target_signature from energy_sync_state"):
            self._one = {"target_signature": self.state.get("signature", {})}
        elif q.startswith("insert into energy_sync_state (device_id,target_signature)"):
            self.state["signature"] = deepcopy(params[1].obj)
        else:
            return super().execute(sql, params)
        self.state["queries"].append((sql, params))


class Connection:
    def __init__(self, original): self.original, self.state = original, deepcopy(original)
    def cursor(self, **_): return Cursor(self.state)
    def commit(self): self.original.clear(); self.original.update(deepcopy(self.state)); self.original["commits"] += 1
    def rollback(self): self.original["rollbacks"] += 1
    def close(self): self.original["closed"] += 1


class CommissioningTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ModeConsumptionComparisonRegression(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.s = self.f.state; self.s["allocation"] = {"enabled": False}; self.s["audit"] = []
        self.router = create_router(lambda: Connection(self.s), lambda: self.f._admin(), lambda cur, *a: cur.state["audit"].append(a), lambda v, _: str(v))
        self.body = {"society_id": self.f.sid, "device_id": self.f.did}
        self.map = {"verified": True, "registers": {"energy_total_kwh": {"address": 0, "type": "float32", "unit": "kWh", "scale": 1, "function": 3, "word_order": "big"}}}
    def call(self, path, method="GET", body=None, user=None, **params):
        endpoint = next(r.endpoint for r in self.router.routes if r.path == "/api/energy/" + path and method in r.methods)
        return endpoint(**({"data": {**self.body, **(body or {})}} if method != "GET" else self.body | params), user=user or self.f._admin())

    def test_admin_read_is_bounded_non_mutating_and_keeps_unknown_report_truthful(self):
        before = deepcopy(self.s["meters"])
        result = self.call("commissioning")
        self.assertEqual(result["device_id"], self.f.did)
        self.assertFalse(result["allocation_enabled"])
        self.assertEqual(result["delivery"]["status"], "UNKNOWN")
        self.assertIsNone(result["delivery"]["reported_version"])
        self.assertEqual(self.s["meters"], before)
        self.assertEqual(self.s["commits"], 0)

    def test_members_and_other_societies_are_rejected_before_writes(self):
        for user in ({"role": "member", "society_id": self.f.sid}, {"role": "society_admin", "society_id": int(self.f.sid) + 1}):
            for path, method, body in (("commissioning", "GET", None), ("bus", "PUT", {"port": "/dev/serial/by-id/fixture"}), ("targets", "POST", {"wing": "A", "adjustment_percent": 0})):
                with self.subTest(role=user["role"], path=path), self.assertRaises(HTTPException) as cm:
                    self.call(path, method, body, user=user)
                self.assertEqual(cm.exception.status_code, 403)

    def test_bus_save_and_stale_version_are_transactional(self):
        old = self.s["device"]["energy_config_version"]
        out = self.call("bus", "PUT", {"port": "/dev/serial/by-id/fixture", "serial": {"baudrate": 9600, "timeout_s": .5}, "expected_version": old})
        self.assertEqual(out["config_version"], old + 1)
        self.assertFalse(self.s["allocation"]["enabled"])
        with self.assertRaises(HTTPException) as cm:
            self.call("bus", "PUT", {"port": "/dev/ttyOTHER", "expected_version": old})
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(self.s["device"]["energy_bus"]["port"], "/dev/serial/by-id/fixture")
        with self.assertRaises(HTTPException): self.call("bus", "PUT", {"port": "/dev/ttyF", "serial": {"bytesize": 7.5}})

    def test_m1_requires_valid_mapping_bus_and_preserves_other_meters(self):
        self.s["device"]["energy_bus"] = {"port": "/dev/serial/by-id/fixture"}
        other = deepcopy(self.s["meters"][1:]); previous = self.s["device"]["energy_config_version"]
        data = {"enabled": True, "serial": "FIXTURE-M1", "model": "FIXTURE ONLY", "modbus_address": 21, "register_map": self.map, "ct_ratio": 1, "phases": 1, "expected_version": previous}
        ep = next(r.endpoint for r in self.router.routes if r.path == "/api/energy/meters/{meter_id}" and "PUT" in r.methods)
        out = ep("M1", {**self.body, **data}, self.f._admin())
        self.assertTrue(out["meter"]["enabled"])
        self.assertEqual(out["config_version"], previous + 1)
        self.assertEqual(self.s["meters"][1:], other)
        for extra in ({"enabled": "false"}, {"register_map": {"verified": False, "registers": self.map["registers"]}}, {"expected_version": previous}):
            with self.assertRaises(HTTPException): ep("M1", {**self.body, **data, "expected_version": previous + 1, **extra}, self.f._admin())

    def test_register_validation_matches_supported_pi_fields_without_running_pi(self):
        self.assertEqual(register_problems(self.map), [])
        for fields in ({"address": True}, {"scale": float("inf")}, {"scale": 0}, {"word_order": "guess"}, {"function": 3.5}, {"unit": "V"}):
            r = deepcopy(self.map); r["registers"]["energy_total_kwh"].update(fields)
            self.assertTrue(register_problems(r))
        r = deepcopy(self.map); r["registers"]["power_kw"] = {"address": 9, "type": "float32", "unit": "V"}
        self.assertTrue(register_problems(r))

    def test_targets_keep_formula_and_reject_changed_bill_preview(self):
        before = self.call("targets", wing="A")
        self.s["bills"]["A"][-1]["consumption_kwh"] += 100
        with self.assertRaises(HTTPException) as cm:
            self.call("targets", "POST", {"wing": "A", "adjustment_percent": 10, "effective_from": "2024-02-29", "expected_basis_hash": before["basis_hash"]})
        self.assertEqual(cm.exception.status_code, 409)
        fresh = self.call("targets", wing="A")
        saved = self.call("targets", "POST", {"wing": "A", "adjustment_percent": 10, "effective_from": "2024-02-29", "expected_version": fresh["delivery"]["desired_version"], "expected_basis_hash": fresh["basis_hash"]})
        self.assertEqual(saved["target"]["target_kwh_per_day"], round(fresh["bills"]["daily_average_kwh"] * 1.1, 4))
        self.assertEqual(saved["delivery_status"], "PENDING_CONTROLLER_REPORT")
        self.assertFalse(self.s["allocation"]["enabled"])


if __name__ == "__main__": unittest.main(verbosity=2)