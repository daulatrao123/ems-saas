"""Execute actual cloud target routes/ingest/config reply with transactional memory cursors.
No service, firmware, authentication provider, socket or PostgreSQL is executed.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
import importlib.util
from pathlib import Path
import socket
import sys
import types
import unittest
from unittest.mock import patch

import psycopg
from fastapi import HTTPException
from backend.energy_api import ingest, routes

NOW = datetime(2026, 9, 16, 1, tzinfo=timezone.utc)
DID = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
USER = {"id": 1, "role": "society_admin", "society_id": "1"}


class Clock(datetime):
    @classmethod
    def now(cls, tz=None): return NOW if tz else NOW.replace(tzinfo=None)


class MemoryConnection:
    def __init__(self, db, fail_audit=False):
        self.db, self.state, self.trace, self.fail_audit = db, deepcopy(db), [], fail_audit
    def cursor(self, **_): return MemoryCursor(self)
    def commit(self): self.db.clear(); self.db.update(deepcopy(self.state))
    def rollback(self): self.state.clear(); self.state.update(deepcopy(self.db))
    def close(self): pass


class MemoryCursor:
    def __init__(self, conn): self.conn, self.one, self.rows = conn, None, []
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def fetchone(self): return self.one
    def fetchall(self): return self.rows
    def execute(self, sql, params=()):
        q = " ".join(sql.lower().split()); s = self.conn.state
        self.conn.trace.append((q, params)); self.one, self.rows = None, []
        if q.startswith("savepoint"):
            self.saved = deepcopy(s); return
        if q.startswith("rollback to savepoint"):
            s.clear(); s.update(self.saved); return
        if q.startswith("release savepoint"): return
        if "from pi_devices d join societies" in q:
            row = s["devices"].get(str(params[0])); self.one = deepcopy(row) if row and row["society_id"] == params[1] else None; return
        if q.startswith("select energy_bus") or q.startswith("select id from pi_devices"):
            self.one = deepcopy(s["devices"].get(params[0])); return
        if q.startswith("insert into energy_meters"): return
        if "from energy_meters" in q:
            self.rows = deepcopy(s["meters"][params[0]]); return
        if q.startswith("update pi_devices set energy_config_version"):
            s["devices"][params[0]]["energy_config_version"] += 1
            self.one = deepcopy(s["devices"][params[0]]); return
        if q.startswith("insert into energy_sync_state"):
            fields = q.split("(", 1)[1].split(")", 1)[0].replace(" ", "").split(",")
            target = s["sync"].setdefault(params[0], {"target_signature":None,"reported_config_version":None,"reported_at":None,"clock_report":None})
            target.update({k: deepcopy(v.obj if hasattr(v, "obj") else v) for k, v in zip(fields[1:], params[1:])}); return
        if "from energy_sync_state" in q:
            self.one = deepcopy(s["sync"].get(params[0])); return
        if "from energy_bill_history" in q:
            self.rows = deepcopy(s["bills"].get((params[0], params[1]), []))[-6:][::-1]; return
        if q.startswith("insert into energy_generation_targets"):
            assert any("for update of d" in t[0] for t in self.conn.trace), "target insert must follow device lock"
            fields = ["device_id", "wing", "base_daily_average_kwh", "adjustment_percent", "target_kwh_per_day", "basis", "effective_from", "reason", "created_by"]
            row = {k: deepcopy(v.obj if hasattr(v, "obj") else v) for k, v in zip(fields, params)}
            row.update(id=max([t["id"] for t in s["targets"]], default=0)+1, created_at=NOW)
            s["targets"].append(row); self.one = deepcopy(row); return
        if "from energy_generation_targets" in q:
            rows = [t for t in s["targets"] if t["device_id"] == params[0]]
            if "distinct on" in q:
                rows = [t for t in rows if t["effective_from"] <= params[1]]
                self.rows = [deepcopy(max([t for t in rows if t["wing"] == w], key=lambda t:(t["effective_from"], t["id"]))) for w in "ABCD" if any(t["wing"] == w for t in rows)]; return
            rows = [t for t in rows if t["wing"] == params[1]]
            if "effective_from<=%s" in q: rows = [t for t in rows if t["effective_from"] <= params[2]]
            rows.sort(key=lambda t:(t["effective_from"], t["id"]), reverse=True)
            self.rows = deepcopy(rows[:20]); self.one = deepcopy(rows[0]) if rows else None; return
        if q.startswith("insert into energy_daily"):
            assert "not (energy_daily.status = 'closed' and excluded.status = 'open')" in q
            fields = ["device_id", "operating_date", "reset_period", "status", "generation_kwh", "generation_source", "wing_generation", "unattributed_generation_kwh", "fault_generation_kwh", "wing_consumption", "consumption_source", "samples", "attempts", "gap_kwh", "events", "opened_at", "closed_at", "updated_at"]
            row = {k: deepcopy(v.obj if hasattr(v, "obj") else v) for k, v in zip(fields, params)}
            key = (params[0], params[1]); old = s["daily"].get(key)
            if old and old["status"] == "CLOSED" and row["status"] == "OPEN": return
            if not old or old["status"] != row["status"] or old.get("updated_at") is None or row["updated_at"] is None or row["updated_at"] >= old["updated_at"]:
                s["daily"][key] = row
            return
        if "from energy_daily" in q and "status='open'" in q:
            days = [d for (did,d),v in s["daily"].items() if did == params[0] and v["status"] == "OPEN"]
            self.one = {"d": max(days) if days else None, "earliest": min(days) if days else None, "open_count": len(days)}; return
        if q.startswith("insert into energy_day_quarantine"):
            key = (params[0], params[1]); old = s["quarantine"].get(key)
            s["quarantine"][key] = {"reason":params[2], "preview":params[3], "first_seen":old["first_seen"] if old else params[4], "last_seen":params[5]}; return
        if q.startswith("delete from energy_day_quarantine"):
            assert params[0] == params[1] and params[2] == 64
            keys = sorted([k for k in s["quarantine"] if k[0] == params[0]], key=lambda k:(-s["quarantine"][k]["last_seen"].timestamp(),k[1]))
            for key in keys[64:]: del s["quarantine"][key]
            return
        if q.startswith("delete from energy_meter_readings"): return
        raise AssertionError(f"Unhandled offline SQL: {q}")


class EnergySyncFlow(unittest.TestCase):
    def setUp(self):
        for guard in [patch.object(socket, "socket", side_effect=AssertionError("No network")), patch.object(psycopg, "connect", side_effect=AssertionError("No liveDB")), patch.object(routes, "datetime", Clock)]:
            guard.start(); self.addCleanup(guard.stop)
        self.db = {"devices":{}, "meters":{}, "bills":{}, "targets":[], "daily":{}, "sync":{}, "quarantine":{}, "audit":[]}
        for did, sid in [(DID,1),(OTHER,2)]:
            self.db["devices"][did] = {"id":did,"society_id":sid,"energy_config_version":4,"energy_bus":{"port":None},"energy_allocation":{"enabled":False},"reset_day":15}
            self.db["meters"][did] = [{"meter_id":f"M{i}","enabled":False,"serial":None,"modbus_address":None,"model":None,"register_map":None,"phases":1,"ct_ratio":1,"max_kw":None} for i in range(1,6)]
            self.db["bills"][(did,"A")] = [{"bill_month":date(2026,9,1),"consumption_kwh":690,"days":30,"note":None}]
            self.db["daily"][(did,NOW.date())] = {"status":"OPEN"}
        self.fail_audit = False
        def audit(cur, user, sid, action, payload):
            if self.fail_audit: raise RuntimeError("mock audit failure")
            cur.conn.state["audit"].append({"sid":sid,"action":action,"payload":payload})
        router = routes.create_router(lambda:MemoryConnection(self.db), lambda:USER, audit, lambda value,_:value)
        self.post = next(r.endpoint for r in router.routes if r.path.endswith("/targets") and "POST" in r.methods)
        self.get = next(r.endpoint for r in router.routes if r.path.endswith("/targets") and "GET" in r.methods)
    def save(self, **changes):
        return self.post(data={"device_id":DID,"society_id":"1","wing":"A","adjustment_percent":0,"effective_from":"2026-09-16",**changes}, user=USER)
    def reply(self, reported, now=NOW):
        conn = MemoryConnection(self.db)
        out = ingest.config_reply(conn.cursor(), DID, {"config_version":reported}, now)
        conn.commit(); return out

    def test_route_save_to_config_resend_report_and_same_day_correction(self):
        other = deepcopy(self.db["devices"][OTHER]); saved = self.save()
        self.assertEqual((saved["config_version"],saved["target"]["target_kwh_per_day"]),(5,23))
        cfg = self.reply(4)
        self.assertEqual(cfg["version"],5); self.assertEqual(cfg["targets"]["A"]["target_kwh_per_day"],23)
        self.assertEqual(cfg["meters"]["M1"]["enabled"],False)
        self.assertEqual(self.reply(4),cfg,"lost reply resent without another bump")
        self.assertIsNone(self.reply(5))
        out = self.get(society_id="1",device_id=DID,wing="A",user=USER)
        self.assertEqual(out["delivery"]["status"],"REPORTED_CURRENT")
        self.assertEqual(self.save(adjustment_percent=100)["config_version"],6)
        self.assertEqual(self.reply(5)["targets"]["A"]["target_kwh_per_day"],46)
        self.assertEqual(self.db["devices"][OTHER],other)

    def test_future_target_not_current_then_rollover_delivered_once(self):
        self.save(); scheduled = self.save(effective_from="2026-09-17",adjustment_percent=100)
        current = self.get(society_id="1",device_id=DID,wing="A",user=USER)
        self.assertNotEqual(current["current"]["id"],scheduled["target"]["id"])
        self.assertEqual(self.reply(5)["targets"]["A"]["target_kwh_per_day"],23)
        self.db["daily"][(DID,NOW.date())]["status"] = "CLOSED"
        self.db["daily"][(DID,date(2026,9,17))] = {"status":"OPEN"}
        tomorrow = datetime(2026,9,17,1,tzinfo=timezone.utc)
        cfg = self.reply(6,tomorrow)
        self.assertEqual((cfg["version"],cfg["targets"]["A"]["target_kwh_per_day"]),(7,46))
        self.assertIsNone(self.reply(7,tomorrow))

    def test_save_rollback_rbac_and_future_open_do_not_deliver(self):
        before = deepcopy(self.db); self.fail_audit = True
        with self.assertRaises(RuntimeError): self.save()
        self.assertEqual(self.db,before,"insert/signature/version all rolled back")
        self.fail_audit = False
        for user, did, sid in [({**USER,"role":"member"},DID,"1"),(USER,OTHER,"2"),(USER,OTHER,"1")]:
            with self.assertRaises(HTTPException):
                self.post(data={"device_id":did,"society_id":sid,"wing":"A","adjustment_percent":0},user=user)
        self.assertEqual(self.db,before)
        self.db["daily"][(DID,date(2099,1,1))] = {"status":"OPEN"}
        self.assertIsNone(self.reply(4),"invalid date withholds energy config without throwing")
        with self.assertRaises(HTTPException): self.save()
        self.assertEqual(self.db["devices"][DID]["energy_config_version"],4)

    def test_current_target_remains_visible_outside_twenty_scheduled_history_rows(self):
        current = self.save()
        for day in range(1,23): self.save(effective_from=f"2027-01-{day:02d}")
        out = self.get(society_id="1",device_id=DID,wing="A",user=USER)
        self.assertEqual(len(out["history"]),20)
        self.assertNotIn(current["target"]["id"],[r["id"] for r in out["history"]])
        self.assertEqual(out["current"]["id"],current["target"]["id"])

    def test_ingest_keeps_valid_rows_and_quarantines_bad_rows_with_bounded_evidence(self):
        conn = MemoryConnection(self.db)
        raw = {"today":{"operating_date":"2099-01-01","generation_kwh":9999},
               "closed_days":[{"operating_date":"2026-09-15","status":"CLOSED","generation_kwh":23,"generation_source":"PHYSICAL"}]}
        out = ingest.ingest(conn.cursor(), DID, raw, NOW)
        self.assertNotIn("error",out); conn.commit()
        self.assertEqual(out["days"],1)
        self.assertEqual(self.db["daily"][(DID,date(2026,9,15))]["generation_kwh"],23)
        self.assertNotIn((DID,date(2099,1,1)),self.db["daily"])
        self.assertIn((DID,"2099-01-01"),self.db["quarantine"])
        # Repeated bad uploads are bounded; another device's evidence is untouched.
        self.db["quarantine"][(OTHER,"2099-01-01")] = {"first_seen":NOW,"last_seen":NOW}
        for month in range(1,4):
            conn = MemoryConnection(self.db)
            out = ingest.ingest(conn.cursor(),DID,{"closed_days":[{"operating_date":f"2099-{month:02d}-{day:02d}"} for day in range(1,29)],"today":{"operating_date":"2026-09-16"}},NOW)
            self.assertNotIn("error",out); conn.commit()
        self.assertEqual(len([k for k in self.db["quarantine"] if k[0]==DID]),64)
        self.assertIn((OTHER,"2099-01-01"),self.db["quarantine"])
        conn = MemoryConnection(self.db)
        result = ingest.ingest(conn.cursor(),DID,{"today":{"operating_date":"bad\x00date"}},NOW)
        self.assertNotIn("error",result)
        self.assertNotIn("\x00",result["clock_report"]["reported_operating_date"])

    def test_migration_emits_only_additive_ddl_offline(self):
        statements=[]
        fake = types.ModuleType("alembic"); fake.op=types.SimpleNamespace(execute=statements.append)
        path=Path(__file__).parents[1]/"backend/alembic/versions/0015_energy_sync_integrity.py"
        spec=importlib.util.spec_from_file_location("offline_migration",path); module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{"alembic":fake}): spec.loader.exec_module(module); module.upgrade()
        self.assertEqual(module.down_revision,"0014_energy_references")
        self.assertEqual(len(statements),3)
        self.assertTrue(all(sql.startswith("CREATE ") for sql in statements))
        self.assertIn("REFERENCES pi_devices(id)",statements[0])
        self.assertIn("65536",statements[1])