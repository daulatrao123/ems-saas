"""Focused offline regression: operating-date semantics + optional consumption meters.

No live DB/network/device/server usage.
"""

from __future__ import annotations

import socket
import re
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import psycopg
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from energy_api import queries as Q  # noqa: E402
from energy_api import references as R  # noqa: E402
from energy_api import routes  # noqa: E402
from pi_firmware.energy.allocation import AllocationPolicy  # noqa: E402


class Clock(datetime):
    instant = datetime(2026, 12, 1, 0, 1, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.instant.astimezone(tz) if tz else cls.instant.replace(tzinfo=None)


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

        if "from pi_devices d join societies s" in q:
            self._one = dict(self.state["device"])
            return

        if q.startswith("select * from energy_meters"):
            self._rows = [dict(r) for r in self.state["meters"]]
            return

        if q.startswith("insert into energy_meters"):
            # ensure_meter_rows bootstrap path for router.device(...)
            return

        if q.startswith("insert into energy_adjustments"):
            did, wing, od, kind, val, reason, created_by = params
            row = {
                "id": len(self.state["adjustments"]) + 1,
                "device_id": did,
                "wing": wing,
                "operating_date": od,
                "kind": kind,
                "value_kwh": float(val),
                "unit": "kWh",
                "reason": reason,
                "created_by": created_by,
                "created_at": Clock.now(timezone.utc),
            }
            self.state["adjustments"].append(row)
            self._one = {"id": row["id"], "created_at": row["created_at"]}
            return

        if "from energy_adjustments a left join users u" in q:
            did, limit = params
            rows = [r for r in self.state["adjustments"] if r["device_id"] == did]
            rows.sort(key=lambda r: (r["operating_date"], r["id"]), reverse=True)
            out = []
            for r in rows[: int(limit)]:
                email = self.state["users"].get(r["created_by"], None)
                out.append({**r, "email": email})
            self._rows = out
            return

        if q.startswith("select operating_date, reset_period, status, generation_kwh"):
            did, frm, to = params
            rows = [r for r in self.state["daily"] if r["device_id"] == did and frm <= r["operating_date"] <= to]
            rows.sort(key=lambda r: r["operating_date"])
            self._rows = rows
            return

        if q.startswith("select operating_date, status, (wing_generation->>"):
            wing, wing2, wing3, did, frm, to = params
            assert wing == wing2 == wing3
            rows = []
            for r in self.state["daily"]:
                if r["device_id"] != did or not (frm <= r["operating_date"] <= to):
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
                key = (a["operating_date"], a["kind"])
                grouped[key] = grouped.get(key, 0.0) + float(a["value_kwh"])
            self._rows = [
                {"operating_date": k[0], "kind": k[1], "v": v}
                for k, v in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1]))
            ]
            return

        if q.startswith("select id, base_daily_average_kwh"):
            did, wing, to_day = params
            rows = [
                r
                for r in self.state["targets"]
                if r["device_id"] == did and r["wing"] == wing and r["effective_from"] <= to_day
            ]
            rows.sort(key=lambda r: (r["effective_from"], r["id"]))
            self._rows = rows
            return

        if q.startswith("select generation_kwh, generation_source from energy_daily"):
            did, od = params
            row = next((r for r in self.state["daily"] if r["device_id"] == did and r["operating_date"] == od), None)
            self._one = {"generation_kwh": row["generation_kwh"], "generation_source": row["generation_source"]} if row else None
            return

        if q.startswith("select * from energy_generation_targets"):
            did, wing, as_of = params
            rows = [
                r
                for r in self.state["targets"]
                if r["device_id"] == did and r["wing"] == wing and r["effective_from"] <= as_of
            ]
            rows.sort(key=lambda r: (r["effective_from"], r["id"]), reverse=True)
            self._one = rows[0] if rows else None
            return

        if q.startswith("select max(operating_date) as d from energy_daily where device_id=%s and status='open'"):
            did = params[0]
            days = [r["operating_date"] for r in self.state["daily"] if r["device_id"] == did and r["status"] == "OPEN"]
            self._one = {"d": max(days)} if days else {"d": None}
            return

        if q.startswith("select operating_date from energy_daily where device_id=%s and status='open' order by operating_date desc limit 1 for share"):
            did = params[0]
            days = sorted(
                [r["operating_date"] for r in self.state["daily"] if r["device_id"] == did and r["status"] == "OPEN"],
                reverse=True,
            )
            self._one = {"operating_date": days[0]} if days else None
            return

        if q.startswith("select sum(") and " as total, count(" in q and " from energy_daily where " in q:
            did = params[0]
            rows = [r for r in self.state["daily"] if r["device_id"] == did]
            rows = [r for r in rows if r["reset_period"] == params[1]] if "reset_period=%s" in q else [r for r in rows if params[1] <= r["operating_date"] <= params[2]]
            values = [self._qualified_value(r, q) for r in rows]
            values = [v for v in values if v is not None]
            self._one = {"total": sum(values) if values else None, "days": len(values)}
            return

        if q.startswith("select to_char(operating_date, 'yyyy-mm') as month") and "count(*) as rows" in q:
            did, frm, to = params
            rows = [r for r in self.state["daily"] if r["device_id"] == did and frm <= r["operating_date"] <= to]
            grouped = {}
            for r in rows:
                month = r["operating_date"].strftime("%Y-%m")
                g = grouped.setdefault(month, {"month": month, "vals": []})
                v = self._qualified_value(r, q)
                if v is not None:
                    g["vals"].append(v)
            out = []
            for month in sorted(grouped):
                vals = grouped[month]["vals"]
                out.append({"month": month, "kwh": sum(vals) if vals else None, "physical_days": len(vals), "rows": len([r for r in rows if r["operating_date"].strftime("%Y-%m") == month])})
            self._rows = out
            return

        if q.startswith("select to_char(operating_date,'yyyy-mm') as month") and "as generation_kwh" in q and "as cons_a" in q:
            for metric in ["generation_kwh"] + [f"{field}:{wing}" for wing in "ABCD" for field in ("wing_generation", "wing_consumption")]:
                self._assert_qualification(q, metric)
            did, frm, to = params
            rows = [r for r in self.state["daily"] if r["device_id"] == did and frm <= r["operating_date"] <= to]
            by_month = {}
            for r in rows:
                month = r["operating_date"].strftime("%Y-%m")
                agg = by_month.setdefault(
                    month,
                    {
                        "month": month,
                        "generation_kwh": 0.0,
                        "generation_days": 0,
                        **{f"cons_{w.lower()}": 0.0 for w in "ABCD"},
                        **{f"cons_days_{w.lower()}": 0 for w in "ABCD"},
                        **{f"gen_{w.lower()}": 0.0 for w in "ABCD"},
                    },
                )
                gv = self._qualified_metric(r, "generation_kwh")
                if gv is not None:
                    agg["generation_kwh"] += gv
                    agg["generation_days"] += 1
                for wing in "ABCD":
                    cv = self._qualified_metric(r, f"wing_consumption:{wing}")
                    if cv is not None:
                        agg[f"cons_{wing.lower()}"] += cv
                        agg[f"cons_days_{wing.lower()}"] += 1
                    wgv = self._qualified_metric(r, f"wing_generation:{wing}")
                    if wgv is not None:
                        agg[f"gen_{wing.lower()}"] += wgv
            out = []
            for month in sorted(by_month):
                a = by_month[month]
                out.append(
                    {
                        "month": month,
                        "generation_kwh": a["generation_kwh"] if a["generation_days"] else None,
                        "generation_days": a["generation_days"],
                        **{f"cons_{w.lower()}": a[f"cons_{w.lower()}"] for w in "ABCD"},
                        **{f"cons_days_{w.lower()}": a[f"cons_days_{w.lower()}"] for w in "ABCD"},
                        **{f"gen_{w.lower()}": a[f"gen_{w.lower()}"] if a[f"gen_{w.lower()}"] != 0.0 or self._month_has_qualified(rows, month, f"wing_generation:{w}") else None for w in "ABCD"},
                    }
                )
            self._rows = out
            return

        if q.startswith("select max(bill_month) as month"):
            _did, wing, cutoff = params
            rows = [r for r in self.state["bills"].get(wing, []) if r["bill_month"] <= cutoff]
            self._one = {"month": max((r["bill_month"] for r in rows), default=None)}
            return

        if q.startswith("select bill_month, consumption_kwh"):
            _did, wing, start, end = params
            self._rows = sorted([r for r in self.state["bills"].get(wing, []) if start <= r["bill_month"] <= end], key=lambda r: r["bill_month"])
            return

        if "status='closed'" in q and "wing_consumption" in q:
            wing, did, today, history_end, _end2, _wing2, _wing3 = params
            rows = [r for r in self.state["daily"] if r["device_id"] == did and r["status"] == "CLOSED" and r["operating_date"] < today
                    and (history_end is None or r["operating_date"] > date.fromisoformat(history_end))
                    and r["consumption_source"].get(wing) == "PHYSICAL" and r["wing_consumption"].get(wing) is not None and r["wing_consumption"][wing] >= 0]
            row = max(rows, key=lambda r: r["operating_date"]) if rows else None
            self._one = {"operating_date": row["operating_date"], "kwh": row["wing_consumption"][wing]} if row else None
            return

        raise AssertionError(f"Unexpected SQL in offline test: {sql}")

    @staticmethod
    def _finite_non_negative(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if f >= 0 and f != float("inf") and f != float("-inf") and f == f else None

    def _qualified_metric(self, row, metric):
        if metric.startswith("wing_consumption:"):
            wing = metric.split(":", 1)[1]
            if row["consumption_source"].get(wing) != "PHYSICAL":
                return None
            return self._finite_non_negative(row["wing_consumption"].get(wing))
        if metric.startswith("wing_generation:"):
            wing = metric.split(":", 1)[1]
            if row.get("generation_source") != "PHYSICAL":
                return None
            return self._finite_non_negative(row["wing_generation"].get(wing))
        if metric in {"generation_kwh", "unattributed_generation_kwh", "fault_generation_kwh"}:
            if row.get("generation_source") != "PHYSICAL":
                return None
            return self._finite_non_negative(row.get(metric))
        return None

    def _qualified_value(self, row, normalized_sql):
        wing_cons = re.search(r"wing_consumption->>'([abcd])'", normalized_sql)
        if wing_cons:
            metric = f"wing_consumption:{wing_cons.group(1).upper()}"
        else:
            wing_gen = re.search(r"wing_generation->>'([abcd])'", normalized_sql)
            metric = f"wing_generation:{wing_gen.group(1).upper()}" if wing_gen else next((field for field in ("unattributed_generation_kwh", "fault_generation_kwh") if field in normalized_sql), "generation_kwh")
        self._assert_qualification(normalized_sql, metric)
        return self._qualified_metric(row, metric)

    @staticmethod
    def _assert_qualification(sql, metric):
        # Do not silently return safe mock results if production SQL loses a guard.
        # Independent expected SQL contract, not a call to the production helper.
        if ":" in metric:
            field, wing = metric.split(":")
            expr = f"({field}->>'{wing.lower()}')::numeric"
            source = f"consumption_source->>'{wing.lower()}'" if field == "wing_consumption" else "generation_source"
        else:
            expr, source = metric, "generation_source"
        required = f"case when {source}='physical' and {expr}>=0 and {expr}<'infinity'::numeric then {expr} end"
        if required not in sql:
            raise AssertionError(f"Missing physical provenance/value guard for {metric}: {sql}")

    def _month_has_qualified(self, rows, month, metric):
        for r in rows:
            if r["operating_date"].strftime("%Y-%m") != month:
                continue
            if self._qualified_metric(r, metric) is not None:
                return True
        return False

    def fetchone(self):
        return dict(self._one) if isinstance(self._one, dict) else self._one

    def fetchall(self):
        return [dict(r) if isinstance(r, dict) else r for r in self._rows]


class FakeConn:
    def __init__(self, state):
        self.state = state

    def cursor(self, row_factory=None):
        return FakeCursor(self.state)

    def commit(self):
        self.state["commits"] += 1

    def rollback(self):
        self.state["rollbacks"] += 1

    def close(self):
        self.state["closed"] += 1


class OperatingDateAndOptionalMetersRegression(unittest.TestCase):
    def setUp(self):
        self.did = "11111111-1111-1111-1111-111111111111"
        now = datetime(2026, 12, 1, 0, 1, tzinfo=timezone.utc)
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
                "energy_calculation_version": 5,
                "grid_export_enabled": True,
                "grid_export_limit_kwh": 500.0,
                "grid_reference_version": 1,
                "reset_day": 15,
            },
            "meters": [
                {
                    "device_id": self.did,
                    "meter_id": "M1",
                    "enabled": True,
                    "comm_status": "ONLINE",
                    "serial": "M1-SN",
                    "model": "M1",
                    "last_kwh": None,
                    "power_kw": 2.0,
                    "ct_ratio": None,
                    "max_kw": None,
                    "last_seen": now,
                    "updated_at": now,
                    "attribution": {"status": "ATTRIBUTED", "wing": "A"},
                },
                {
                    "device_id": self.did,
                    "meter_id": "M2",
                    "enabled": True,
                    "comm_status": "ONLINE",
                    "serial": "M2",
                    "model": "C",
                    "last_kwh": None,
                    "power_kw": 1.2,
                    "ct_ratio": None,
                    "max_kw": None,
                    "last_seen": now,
                    "updated_at": now,
                    "attribution": None,
                },
                {
                    "device_id": self.did,
                    "meter_id": "M3",
                    "enabled": False,
                    "comm_status": "ONLINE",
                    "serial": "M3",
                    "model": "C",
                    "last_kwh": None,
                    "power_kw": 9.9,
                    "ct_ratio": None,
                    "max_kw": None,
                    "last_seen": now,
                    "updated_at": now,
                    "attribution": None,
                },
                {
                    "device_id": self.did,
                    "meter_id": "M4",
                    "enabled": True,
                    "comm_status": "ONLINE",
                    "serial": "M4",
                    "model": "C",
                    "last_kwh": None,
                    "power_kw": 0.0,
                    "ct_ratio": None,
                    "max_kw": None,
                    "last_seen": now,
                    "updated_at": now,
                    "attribution": None,
                },
                {
                    "device_id": self.did,
                    "meter_id": "M5",
                    "enabled": False,
                    "comm_status": "OFFLINE",
                    "serial": "M5",
                    "model": "C",
                    "last_kwh": None,
                    "power_kw": 7.0,
                    "ct_ratio": None,
                    "max_kw": None,
                    "last_seen": now,
                    "updated_at": now,
                    "attribution": None,
                },
            ],
            "daily": [
                {
                    "device_id": self.did,
                    "operating_date": date(2026, 11, 30),
                    "reset_period": "2026-11",
                    "status": "OPEN",
                    "generation_kwh": 1000.0,
                    "generation_source": "PHYSICAL",
                    "wing_generation": {"A": 5.0, "B": None, "C": 0.0, "D": None},
                    "wing_consumption": {"A": 11.0, "B": 22.0, "C": 0.0, "D": 44.0},
                    "consumption_source": {"A": "PHYSICAL", "B": "PHYSICAL", "C": "PHYSICAL", "D": "PHYSICAL"},
                    "unattributed_generation_kwh": 0.0,
                    "fault_generation_kwh": 0.0,
                    "gap_kwh": 0.0,
                }
            ],
            "adjustments": [],
            "targets": [
                {"id": 11, "device_id": self.did, "wing": "A", "target_kwh_per_day": 200.0, "adjustment_percent": 0.0, "base_daily_average_kwh": 200.0, "effective_from": date(2026, 1, 1)},
                {"id": 12, "device_id": self.did, "wing": "B", "target_kwh_per_day": 220.0, "adjustment_percent": 0.0, "base_daily_average_kwh": 220.0, "effective_from": date(2026, 1, 1)},
                {"id": 13, "device_id": self.did, "wing": "C", "target_kwh_per_day": 180.0, "adjustment_percent": 0.0, "base_daily_average_kwh": 180.0, "effective_from": date(2026, 1, 1)},
            ],
        }
        self.audit = []
        self.state["bills"] = {w: [] if value is None else [self.bill(date(2026, 11, 1), value)] for w, value in zip("ABCD", (150, 150, 0, None))}
        for guard in (patch.object(socket, "socket", side_effect=AssertionError("offline: socket forbidden")),
                      patch.object(psycopg, "connect", side_effect=AssertionError("offline: DB connection forbidden")),
                      patch.object(routes, "datetime", Clock)):
            guard.start()
            self.addCleanup(guard.stop)

        def get_db():
            return FakeConn(self.state)

        def get_current_user():
            return {"id": "u-admin", "role": "society_admin", "society_id": "1"}

        def log_audit(_cur, user, society_id, action, payload):
            self.audit.append((user["id"], society_id, action, payload))

        def require_uuid(v, _field):
            return str(v)

        self.router = routes.create_router(get_db, get_current_user, log_audit, require_uuid)
        self.post_adjustment = self._endpoint("/api/energy/adjustments", "POST")
        self.get_adjustments = self._endpoint("/api/energy/adjustments", "GET")
        self.history = self._endpoint("/api/energy/history", "GET")
        self.graph_wing = self._endpoint("/api/energy/graph/wing", "GET")
        self.summary = self._endpoint("/api/energy/summary", "GET")

    @staticmethod
    def bill(month, kwh):
        return {"bill_month": month, "consumption_kwh": kwh, "source": "HISTORICAL", "note": None,
                "created_by": None, "updated_by": None, "updated_at": datetime(2026, 12, 1, tzinfo=timezone.utc)}

    def _endpoint(self, path, method):
        for r in self.router.routes:
            if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
                return r.endpoint
        raise AssertionError(f"Missing route {method} {path}")

    def test_network_db_guards(self):
        with patch.object(socket, "socket", side_effect=AssertionError("socket usage forbidden")), patch.object(
            psycopg, "connect", side_effect=AssertionError("psycopg.connect forbidden")
        ):
            out = self.get_adjustments(society_id="1", device_id=self.did, limit=5, user={"id": "u-admin", "role": "society_admin", "society_id": "1"})
            self.assertEqual(out["rows"], [])

    def test_adjustment_operating_date_persistence_and_history_not_merged(self):
        user = {"id": "u-admin", "role": "society_admin", "society_id": "1"}
        created = self.post_adjustment(
            data={
                "society_id": "1",
                "device_id": self.did,
                "wing": "A",
                "kind": "MANUAL_GENERATION",
                "operating_date": "2026-11-30",
                "value_kwh": 3,
                "reason": "month boundary correction",
            },
            user=user,
        )
        self.assertEqual(created["adjustment"]["operating_date"], "2026-11-30")
        self.assertTrue(created["adjustment"]["created_at"].startswith("2026-12-01"), "created_at UTC timestamp must remain distinct from operating_date")

        listed = self.get_adjustments(society_id="1", device_id=self.did, limit=50, user=user)
        self.assertEqual(len(listed["rows"]), 1)
        self.assertEqual(listed["rows"][0]["operating_date"], "2026-11-30")
        self.assertEqual(listed["rows"][0]["source"], "MANUAL")

        hist = self.history(society_id="1", device_id=self.did, frm="2026-11-30", to="2026-11-30", range=None, granularity="daily", user=user)
        self.assertEqual(hist["count"], 1)
        self.assertEqual(hist["rows"][0]["operating_date"], "2026-11-30")
        self.assertEqual(hist["rows"][0]["generation_kwh"], 1000.0)

    def test_invalid_adjustment_date_and_reason_validation(self):
        user = {"id": "u-admin", "role": "society_admin", "society_id": "1"}
        with self.assertRaises(HTTPException) as bad_date:
            self.post_adjustment(
                data={
                    "society_id": "1",
                    "device_id": self.did,
                    "wing": "A",
                    "kind": "MANUAL_GENERATION",
                    "operating_date": "2026-13-01",
                    "value_kwh": 1,
                    "reason": "x",
                },
                user=user,
            )
        self.assertEqual(bad_date.exception.status_code, 400)
        self.assertIn("operating_date must be YYYY-MM-DD", bad_date.exception.detail)

        with self.assertRaises(HTTPException) as bad_reason:
            self.post_adjustment(
                data={
                    "society_id": "1",
                    "device_id": self.did,
                    "wing": "A",
                    "kind": "MANUAL_GENERATION",
                    "operating_date": "2026-11-30",
                    "value_kwh": 1,
                    "reason": "   ",
                },
                user=user,
            )
        self.assertEqual(bad_reason.exception.status_code, 400)
        self.assertIn("reason is required", bad_reason.exception.detail)

    def test_wing_graph_rows_groups_by_operating_date_with_manual_additive(self):
        self.state["adjustments"].append(
            {
                "id": 1,
                "device_id": self.did,
                "wing": "A",
                "operating_date": date(2026, 11, 30),
                "kind": "MANUAL_GENERATION",
                "value_kwh": 3.0,
                "unit": "kWh",
                "reason": "+3",
                "created_by": "u-admin",
                "created_at": datetime(2026, 12, 1, 0, 1, tzinfo=timezone.utc),
            }
        )
        cur = FakeCursor(self.state)
        rows = Q.wing_graph_rows(cur, self.did, "A", date(2026, 11, 30), date(2026, 11, 30), generation_enabled=True, consumption_enabled=True, calculation_mode=None)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-11-30")
        self.assertEqual(rows[0]["generated_kwh"], 8.0, "must keep Pi business operating day and additive manual generation")

    def test_consumption_meter_eligible_matrix(self):
        cases = [
            ({"enabled": True, "comm_status": "ONLINE"}, True),
            ({"enabled": False, "comm_status": "ONLINE"}, False),
            ({"enabled": True, "comm_status": "OFFLINE"}, False),
            ({"enabled": True, "comm_status": "STALE"}, False),
            ({"enabled": True, "comm_status": "UNKNOWN"}, False),
            ({"enabled": True, "comm_status": "FAULT"}, False),
            ({"enabled": True, "comm_status": "ERROR"}, False),
            ({"enabled": True}, False),
            (None, False),
        ]
        for meter, expected in cases:
            with self.subTest(meter=meter):
                self.assertEqual(Q.consumption_meter_eligible(meter), expected)

    def test_summary_and_calculation_basis_optional_meters_behavior(self):
        user = {"id": "u-admin", "role": "society_admin", "society_id": "1"}
        out = self.summary(society_id="1", device_id=self.did, user=user)
        self.assertEqual(out["wings"]["A"]["consumption"]["today"]["kwh"], 11.0)
        self.assertEqual(out["wings"]["C"]["consumption"]["today"]["kwh"], 0.0)
        self.assertEqual(out["wings"]["B"]["consumption"]["status"], "UNAVAILABLE")
        self.assertEqual(out["wings"]["D"]["consumption"]["status"], "UNAVAILABLE")
        self.assertEqual(out["wings"]["B"]["consumption_meter"]["power_kw"], None)
        self.assertEqual(out["wings"]["D"]["consumption_meter"]["power_kw"], None)
        self.assertEqual(out["wings"]["B"]["required_generation"]["target_kwh_per_day"], 220.0)
        self.assertEqual(out["references"]["allocation"]["reason"], "REQUIRED_WING_QUOTA_UNAVAILABLE")
        self.assertEqual([out["calculation"]["wings"][w]["today"]["consumed_kwh"] for w in "ABCD"], [11, None, 0, None])
        self.assertEqual([w["wing"] for w in out["references"]["allocation"]["wings"]], list("ABCD"))
        self.assertIsNone(out["references"]["society_historical_daily_kwh"])
        self.assertEqual(out["references"]["wings"]["B"]["history"]["reference_daily_kwh"], 5)

        out_graph = self.graph_wing(
            society_id="1",
            device_id=self.did,
            wing="B",
            range="7d",
            frm=None,
            to=None,
            basis="calculation",
            user=user,
        )
        self.assertFalse(out_graph["consumption_meter_enabled"], "B consumption meter remains ineligible in graph basis=calculation")
        self.assertIsNone(out_graph["rows"][-1]["consumed_kwh"])

    def test_enabled_offline_gate_exercises_summary_calculation_and_graph(self):
        user = {"id": "u-admin", "role": "society_admin", "society_id": "1"}
        meter = self.state["meters"][2]  # B/M3
        meter["enabled"] = True
        for status in ("OFFLINE", "STALE", "UNKNOWN", "ERROR", "FAULT"):
            with self.subTest(status=status):
                meter["comm_status"] = status
                out = self.summary(society_id="1", device_id=self.did, user=user)
                self.assertEqual(out["wings"]["B"]["consumption"]["status"], "UNAVAILABLE")
                self.assertIsNone(out["wings"]["B"]["consumption_meter"]["power_kw"])
                self.assertIsNone(out["calculation"]["wings"]["B"]["today"]["consumed_kwh"])
                graph = self.graph_wing(society_id="1", device_id=self.did, wing="B", range="7d", frm=None, to=None, basis="calculation", user=user)
                self.assertTrue(graph["consumption_meter_enabled"], "configuration flag must not be silently renamed to eligibility")
                self.assertIsNone(graph["rows"][-1]["consumed_kwh"])
                self.assertEqual(graph["rows"][-1]["consumption_source"], "UNAVAILABLE")
        self.state["daily"][0]["wing_consumption"]["A"] = None
        out = self.summary(society_id="1", device_id=self.did, user=user)
        self.assertIsNone(out["wings"]["A"]["consumption"]["today"]["kwh"])
        self.assertEqual(out["wings"]["A"]["consumption"]["today"]["status"], "UNAVAILABLE")

    def test_utc_midnight_keeps_adjustments_on_pi_day_while_history_advances_month(self):
        user = {"id": "u-admin", "role": "society_admin", "society_id": "1"}
        self.state["device"]["energy_calculation_mode"] = "MANUAL"
        self.state["bills"]["A"].append(self.bill(date(2026, 12, 1), 310))
        for clock, amount, history_end, reference in ((datetime(2026, 11, 30, 23, 59, tzinfo=timezone.utc), 5, "2026-11", 5),
                                                       (datetime(2026, 12, 1, 0, 1, tzinfo=timezone.utc), 3, "2026-12", 7.5)):
            with patch.object(Clock, "instant", clock):
                self.post_adjustment(data={"society_id": "1", "device_id": self.did, "wing": "A", "kind": "MANUAL_GENERATION", "operating_date": "2026-11-30", "value_kwh": amount, "reason": "boundary"}, user=user)
                out = self.summary(society_id="1", device_id=self.did, user=user)
                self.assertEqual(out["as_of_operating_date"], "2026-11-30")
                self.assertEqual(out["calculation"]["operating_date"], "2026-11-30")
                self.assertEqual(out["references"]["wings"]["A"]["history"]["end_month"], history_end)
                self.assertEqual(out["references"]["wings"]["A"]["history"]["reference_daily_kwh"], reference)
        self.assertEqual([r["operating_date"] for r in self.state["adjustments"]], [date(2026, 11, 30)] * 2)
        self.assertEqual([r["created_at"].date() for r in self.state["adjustments"]], [date(2026, 11, 30), date(2026, 12, 1)])
        self.assertEqual(out["calculation"]["wings"]["A"]["today"]["generated_kwh"], 8)
        graph = self.graph_wing(society_id="1", device_id=self.did, wing="A", range=None, frm="2026-11-30", to="2026-12-01", basis="calculation", user=user)
        self.assertEqual([r["generated_kwh"] for r in graph["rows"]], [8, None])
        self.assertEqual(graph["rows"][0]["generation_source"], "MANUAL")
        self.assertEqual(self.state["daily"][0]["generation_kwh"], 1000)
        self.assertEqual(out["references"]["allocation"]["reason"], "SOCIETY_GENERATION_UNAVAILABLE")

    def test_missing_reference_and_target_independent_of_optional_meters(self):
        user = {"id": "u-admin", "role": "society_admin", "society_id": "1"}
        self.state["bills"]["D"] = [self.bill(date(2026, 11, 1), 0)]
        out = self.summary(society_id="1", device_id=self.did, user=user)
        self.assertEqual(out["references"]["society_historical_daily_kwh"], 10)
        self.assertEqual(out["references"]["wings"]["D"]["history"]["source"], "HISTORICAL")
        self.assertEqual(out["references"]["allocation"]["grid_allocation_kwh"], 0)
        self.assertIsNone(out["references"]["allocation"]["required_kwh"])
        self.state["targets"].append({**self.state["targets"][0], "id": 14, "wing": "D", "target_kwh_per_day": 210})
        out = self.summary(society_id="1", device_id=self.did, user=user)
        self.assertEqual(out["references"]["allocation"]["required_kwh"], 810)
        self.assertEqual(out["references"]["allocation"]["grid_allocation_kwh"], 190)
        self.assertEqual([r["allocated_kwh"] for r in out["references"]["allocation"]["wings"]], [200, 220, 180, 210])
        self.assertIsNone(out["calculation"]["wings"]["B"]["today"]["consumed_kwh"])
        self.state["device"]["grid_export_enabled"] = False
        out = self.summary(society_id="1", device_id=self.did, user=user)
        self.assertEqual(out["references"]["allocation"]["grid_allocation_kwh"], 0)
        self.assertEqual(out["references"]["allocation"]["reason"], "GRID_EXPORT_DISABLED")

    def test_references_overview_keeps_meter_enabled_qualification_boundary(self):
        state = {
            "queries": [],
            "targets": [
                {"id": 1, "device_id": self.did, "wing": "A", "target_kwh_per_day": 200.0, "adjustment_percent": 0.0, "base_daily_average_kwh": 200.0, "effective_from": date(2026, 1, 1)},
                {"id": 2, "device_id": self.did, "wing": "B", "target_kwh_per_day": 220.0, "adjustment_percent": 0.0, "base_daily_average_kwh": 220.0, "effective_from": date(2026, 1, 1)},
                {"id": 3, "device_id": self.did, "wing": "C", "target_kwh_per_day": 180.0, "adjustment_percent": 0.0, "base_daily_average_kwh": 180.0, "effective_from": date(2026, 1, 1)},
            ],
            "daily": [
                {
                    "device_id": self.did,
                    "operating_date": date(2026, 11, 30),
                    "generation_kwh": 1000.0,
                    "generation_source": "PHYSICAL",
                }
            ],
            "bill_history": {w: 5.0 for w in ("A", "B", "C", "D")},
            "closed_physical": {"B": (date(2026, 11, 29), 9.0)},
        }

        class RefCursor:
            def __init__(self, st):
                self.st = st
                self._one = None

            def execute(self, sql, params=None):
                q = " ".join(sql.lower().split())
                p = params or ()
                if q.startswith("select max(bill_month) as month"):
                    self._one = {"month": date(2026, 10, 1)}
                elif q.startswith("select bill_month, consumption_kwh"):
                    did, wing, _start, _end = p
                    assert did == self.did
                    # one valid month -> historical daily 5.0
                    self._rows = [
                        {
                            "bill_month": date(2026, 10, 1),
                            "consumption_kwh": 155.0,
                            "note": None,
                            "source": "HISTORICAL",
                            "created_by": "u",
                            "updated_by": "u",
                            "created_at": datetime(2026, 10, 2, tzinfo=timezone.utc),
                            "updated_at": datetime(2026, 10, 2, tzinfo=timezone.utc),
                        }
                    ]
                    self._wing = wing
                elif "status='closed'" in q and "wing_consumption" in q:
                    wing = p[0]
                    row = self.st["closed_physical"].get(wing)
                    self._one = {"operating_date": row[0], "kwh": row[1]} if row else None
                elif q.startswith("select generation_kwh, generation_source from energy_daily"):
                    self._one = {"generation_kwh": 1000.0, "generation_source": "PHYSICAL"}
                elif q.startswith("select * from energy_generation_targets"):
                    did, wing, as_of = p
                    rows = [r for r in self.st["targets"] if r["device_id"] == did and r["wing"] == wing and r["effective_from"] <= as_of]
                    rows.sort(key=lambda r: (r["effective_from"], r["id"]), reverse=True)
                    self._one = rows[0] if rows else None
                else:
                    raise AssertionError(f"Unexpected SQL for references test: {sql}")

            def fetchone(self):
                return dict(self._one) if isinstance(self._one, dict) else self._one

            def fetchall(self):
                return [dict(r) for r in getattr(self, "_rows", [])]

        cur = RefCursor(state)
        cur.did = self.did
        dev = {
            "id": self.did,
            "energy_calculation_mode": "AUTO",
            "grid_export_enabled": True,
            "grid_export_limit_kwh": 300.0,
            "grid_reference_version": 1,
        }
        meters = {
            "M1": {"enabled": True},
            "M2": {"enabled": True, "comm_status": "ONLINE"},
            "M3": {"enabled": True, "comm_status": "OFFLINE"},
            "M4": {"enabled": True, "comm_status": "ONLINE"},
            "M5": {"enabled": False, "comm_status": "OFFLINE"},
        }
        out = R.overview(cur, dev, meters, date(2026, 11, 30), calendar_today=date(2026, 12, 1))
        self.assertEqual(out["wings"]["B"]["effective"]["source"], "PHYSICAL", "completed physical reference remains valid with enabled meter even if currently offline")
        self.assertEqual(out["allocation"]["reason"], "REQUIRED_WING_QUOTA_UNAVAILABLE")

    def test_allocation_policy_manual_target_requirement_and_grid_math(self):
        cfg = {
            "wings": {
                "A": {"generation_attribution_enabled": True, "manual_target_kwh": None},
                "B": {"generation_attribution_enabled": True, "manual_target_kwh": None},
                "C": {"generation_attribution_enabled": True, "manual_target_kwh": None},
                "D": {"generation_attribution_enabled": True, "manual_target_kwh": None},
            }
        }
        policy_holder = SimpleNamespace(config=cfg)
        ctx = {
            "wings": {
                "A": {"ems_enabled": True, "consumption_meter_enabled": True},
                "B": {"ems_enabled": True, "consumption_meter_enabled": False},
                "C": {"ems_enabled": True, "consumption_meter_enabled": True},
                "D": {"ems_enabled": True, "consumption_meter_enabled": False},
            },
            "targets": {
                "A": {"target_kwh_per_day": 200.0},
                "B": {"target_kwh_per_day": 220.0},
                "C": {"target_kwh_per_day": 180.0},
                "D": None,
            },
        }

        target_b, reason_b = AllocationPolicy.wing_target(policy_holder, "B", ctx)
        self.assertIsNone(target_b)
        self.assertEqual(reason_b, "CONSUMPTION_METER_OFF_NO_MANUAL_TARGET")
        cfg["wings"]["B"]["manual_target_kwh"] = 220.0
        self.assertEqual(AllocationPolicy.wing_target(policy_holder, "B", ctx), (220.0, None))
        self.assertEqual(AllocationPolicy.wing_target(policy_holder, "A", ctx), (200.0, None))
        self.assertEqual(AllocationPolicy.wing_target(policy_holder, "D", ctx), (None, "CONSUMPTION_METER_OFF_NO_MANUAL_TARGET"))

        alloc_unknown = R.allocation_reference(1000.0, {"A": 200.0, "B": 220.0, "C": 180.0, "D": None}, enabled=True, limit=500.0)
        self.assertEqual(alloc_unknown["reason"], "REQUIRED_WING_QUOTA_UNAVAILABLE")
        self.assertIsNone(alloc_unknown["excess_kwh"])
        self.assertIsNone(alloc_unknown["unmet_kwh"])
        self.assertEqual(alloc_unknown["grid_allocation_kwh"], 0.0)

        alloc_1000 = R.allocation_reference(1000.0, {"A": 200.0, "B": 220.0, "C": 180.0, "D": 210.0}, enabled=True, limit=190.0)
        self.assertEqual(alloc_1000["required_kwh"], 810.0)
        self.assertEqual(alloc_1000["excess_kwh"], 190.0)
        self.assertEqual(alloc_1000["grid_allocation_kwh"], 190.0)

        alloc_700 = R.allocation_reference(700.0, {"A": 200.0, "B": 220.0, "C": 180.0, "D": 210.0}, enabled=True, limit=190.0)
        self.assertEqual(alloc_700["unmet_kwh"], 110.0)
        self.assertEqual(alloc_700["grid_allocation_kwh"], 0.0)
        self.assertEqual(alloc_700["reason"], "WING_QUOTAS_UNMET")

        alloc_manual = R.allocation_reference(None, {"A": 200.0, "B": 220.0, "C": 180.0, "D": 210.0}, enabled=True, limit=190.0)
        self.assertEqual(alloc_manual["generation_kwh"], None)
        self.assertEqual(alloc_manual["grid_allocation_kwh"], 0.0)
        self.assertEqual(alloc_manual["reason"], "SOCIETY_GENERATION_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
