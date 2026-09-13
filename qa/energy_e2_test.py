"""E2 energy backend tests against the live local backend (:8001). Logins: 3 (limit 5/min)."""
import os, sys, uuid, requests, subprocess, calendar
from datetime import date, timedelta, datetime, timezone
B = "http://localhost:8001"; O = "http://localhost:3000"
R = []
def check(n, c, d=""): R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + (f"  [{d}]" if d else ""))
def psql(sql): return subprocess.check_output(["su", "postgres", "-c", f"psql -p 5433 -d ems_fresh -Atc \"{sql}\""], text=True).strip()
def login(e, p):
    s = requests.Session(); r = s.post(f"{B}/api/auth/login", json={"email": e, "password": p}, headers={"Origin": O}); assert r.status_code == 200, r.text
    for ck in s.cookies: ck.secure = False
    s.headers.update({"Origin": O, "X-CSRF-Token": s.cookies.get("ems_csrf")}); return s
tag = uuid.uuid4().hex[:6]; sa = login("admin@ems.com", "Boot#Pass123")
sid = sa.post(f"{B}/api/super-admin/societies", json={"name": f"ENERGY-{tag}", "reset_day": 15}).json()["society_id"]
u = sa.post(f"{B}/api/super-admin/users", json={"society_id": sid, "role": "society_admin", "email": f"en-{tag}@t.test", "name": "A"}).json(); adm = login(u["email"], u["temporary_password"])
mu = sa.post(f"{B}/api/super-admin/users", json={"society_id": sid, "role": "member", "email": f"em-{tag}@t.test", "name": "M"}).json(); mem = login(mu["email"], mu["temporary_password"])
reg = adm.post(f"{B}/api/admin/devices/register", json={"name": f"pe-{tag}"}).json(); dev, key = reg["device_id"], reg["api_key"]
Q = {"society_id": sid, "device_id": dev}; body = lambda **k: {"society_id": sid, "device_id": dev, **k}
def sync(energy=None):
    p = {"slots": {s: {"physical_toggle": "UNKNOWN", "used_days": 0, "clicks": 0} for s in "ABCD"}, "events": []}
    if energy is not None: p["energy"] = energy
    return requests.post(f"{B}/api/pi/sync", json=p, headers={"X-Device-ID": dev, "X-API-Key": key})
VMAP = {"name": "TEST", "verified": True, "registers": {"energy_total_kwh": {"address": 342, "function": 4, "type": "float32", "word_order": "big", "scale": 1.0, "unit": "kWh"}}}

# ---------------------------------------------------------------- migration / registry
check("M1 alembic single head 0012_energy_allocation applied", psql("select version_num from alembic_version") == "0012_energy_allocation")
r = sync(); j = r.json()
check("1 sync without energy block still 200; reply carries energy_config (Pi has no version) with exactly 5 disabled meters", r.status_code == 200 and "energy_config" in j and sorted(j["energy_config"]["meters"]) == ["M1", "M2", "M3", "M4", "M5"] and not any(m["enabled"] for m in j["energy_config"]["meters"].values()) and j["energy_config"]["version"] == 0)
m = adm.get(f"{B}/api/energy/meters", params=Q).json()
check("2 GET meters: M1 GENERATION (no wing), M2..M5 CONSUMPTION A-D, all DISABLED, identity fields present", m["meters"]["M1"]["role"] == "GENERATION" and m["meters"]["M1"]["wing"] is None and [m["meters"][k]["wing"] for k in ("M2", "M3", "M4", "M5")] == ["A", "B", "C", "D"] and all(v["comm_status"] == "DISABLED" for v in m["meters"].values()) and "serial" in m["meters"]["M2"] and "modbus_address" in m["meters"]["M2"])
check("3 DB constraint: a second generation meter row is impossible (CHECK role/id/wing)", "violates check constraint" in subprocess.run(["su", "postgres", "-c", f"psql -p 5433 -d ems_fresh -Atc \"insert into energy_meters (device_id, meter_id, role, wing) values ('{dev}','M2','GENERATION',NULL)\""], capture_output=True, text=True).stderr)

# ---------------------------------------------------------------- config writes (backend-authoritative)
check("4 enabling a meter before the RS485 bus port exists -> 409", adm.put(f"{B}/api/energy/meters/M2", json=body(enabled=True, serial="A-1", modbus_address=2, register_map=VMAP)).status_code == 409)
r = adm.put(f"{B}/api/energy/bus", json=body(port="/dev/serial/by-id/usb-rs485", serial={"baudrate": 9600, "parity": "N"})).json()
check("5 bus configured -> config_version bumped to 1", r["success"] and r["config_version"] == 1 and r["bus"]["port"].startswith("/dev/serial"))
check("6 enable without serial -> 409 (physical identity required)", adm.put(f"{B}/api/energy/meters/M2", json=body(enabled=True, modbus_address=2, register_map=VMAP)).status_code == 409)
check("7 unverified register map (template, no addresses) -> 400", adm.put(f"{B}/api/energy/meters/M2", json=body(register_map={"verified": False, "registers": {"energy_total_kwh": {"address": None}}})).status_code == 400)
r = adm.put(f"{B}/api/energy/meters/M2", json=body(enabled=True, serial="A-1", modbus_address=2, register_map=VMAP, model="TEST", max_kw=30)).json()
check("8 M2 enabled with serial+address+verified map -> version 2", r["success"] and r["config_version"] == 2 and r["meter"]["enabled"] and r["meter"]["modbus_address"] == 2)
check("9 M3 with the same Modbus address -> 409; same serial -> 409", adm.put(f"{B}/api/energy/meters/M3", json=body(enabled=True, serial="B-1", modbus_address=2, register_map=VMAP)).status_code == 409 and adm.put(f"{B}/api/energy/meters/M3", json=body(serial="A-1")).status_code == 409)
check("10 M6 / bad address / bad meter -> 400", adm.put(f"{B}/api/energy/meters/M6", json=body(enabled=False)).status_code == 400 and adm.put(f"{B}/api/energy/meters/M3", json=body(modbus_address=300)).status_code == 400)
check("11 member is read-only (403 on PUT), can GET", mem.put(f"{B}/api/energy/meters/M3", json=body(serial="x")).status_code == 403 and mem.get(f"{B}/api/energy/meters", params=Q).status_code == 200)
check("12 cross-tenant: other society_id -> 403; unknown device -> 404; malformed device -> 400", adm.get(f"{B}/api/energy/meters", params={"society_id": sid + 999999, "device_id": dev}).status_code == 403 and adm.get(f"{B}/api/energy/meters", params={"society_id": sid, "device_id": str(uuid.uuid4())}).status_code == 404 and adm.get(f"{B}/api/energy/meters", params={"society_id": sid, "device_id": "nope"}).status_code == 400)
adm.put(f"{B}/api/energy/meters/M1", json=body(enabled=True, serial="G-1", modbus_address=1, register_map=VMAP, max_kw=50))
adm.put(f"{B}/api/energy/meters/M3", json=body(enabled=True, serial="B-1", modbus_address=3, register_map=VMAP))
check("13 removing the bus port while meters are enabled -> 409", adm.put(f"{B}/api/energy/bus", json=body(port="")).status_code == 409)
ver = adm.get(f"{B}/api/energy/meters", params=Q).json()["config_version"]
j = sync({"schema": 1, "config_version": ver, "meters": {}, "today": None}).json()
check("14 Pi reporting the current config_version gets NO energy_config; stale version gets it (with enabled M1/M2/M3)", "energy_config" not in j and sync({"config_version": ver - 1}).json()["energy_config"]["meters"]["M1"]["enabled"] and sync({"config_version": ver - 1}).json()["energy_config"]["bus"]["port"].startswith("/dev/"))

# ---------------------------------------------------------------- ingestion (idempotent, CLOSED wins)
T = date(2026, 6, 20); D1, D2 = T - timedelta(days=2), T - timedelta(days=1)
def day(d, status, gen, wa, wb=None, ga=None, un=0.0, upd=1.0):
    return {"operating_date": d.isoformat(), "reset_period": "2026-06", "status": status, "generation_kwh": gen, "generation_source": "PHYSICAL" if gen is not None else "UNAVAILABLE",
            "wing_generation": {"A": ga, "B": None, "C": None, "D": None}, "unattributed_generation_kwh": un, "fault_generation_kwh": 0.0,
            "wing_consumption": {"A": wa, "B": wb, "C": None, "D": None}, "consumption_source": {"A": "PHYSICAL" if wa is not None else "UNAVAILABLE", "B": "PHYSICAL" if wb is not None else "UNAVAILABLE", "C": "UNAVAILABLE", "D": "UNAVAILABLE"},
            "samples": {"M1": 100}, "attempts": {"M1": 100}, "gap_kwh": {}, "events": [], "opened_at": 1.0, "closed_at": None, "updated_at": upd}
now_ts = datetime.now(timezone.utc).timestamp()
E = {"config_version": ver, "meters": {"M1": {"comm_status": "ONLINE", "last_seen": now_ts, "last_kwh": 1000.5, "power_kw": 8.42}, "M2": {"comm_status": "ONLINE", "last_seen": now_ts, "last_kwh": 200.0}, "M3": {"comm_status": "OFFLINE", "last_error": "timeout"}},
     "attribution": {"wing": "A", "status": "ATTRIBUTED", "reason": None, "ts": now_ts},
     "today": day(T, "OPEN", 2.0, 1.5, wb=None, ga=2.0), "closed_days": [day(D1, "CLOSED", 10.0, 6.0, wb=4.0, ga=10.0), day(D2, "CLOSED", 12.0, 7.0, wb=5.0, ga=9.0, un=3.0)]}
r = sync(E); r2 = sync(E)
check("15 energy ingestion: 3 ledger rows (2 CLOSED + 1 OPEN), re-sending is idempotent (still 3), meter health + attribution stored", r.status_code == 200 and r2.status_code == 200 and psql(f"select count(*) from energy_daily where device_id='{dev}'") == "3" and psql(f"select comm_status||'/'||coalesce(last_error,'') from energy_meters where device_id='{dev}' and meter_id='M3'") == "OFFLINE/timeout" and psql(f"select attribution->>'wing' from energy_meters where device_id='{dev}' and meter_id='M1'") == "A" and psql(f"select count(*) from energy_meter_readings where device_id='{dev}'") == "2")
sync({**E, "today": day(D2, "OPEN", 0.1, 0.1, upd=99.0), "closed_days": []})
check("16 an OPEN row can never overwrite a CLOSED day (no double counting / regression)", psql(f"select status||':'||generation_kwh from energy_daily where device_id='{dev}' and operating_date='{D2}'") == "CLOSED:12.000000")
sync({**E, "today": day(T, "OPEN", 2.5, 1.9, ga=2.5, upd=2.0), "closed_days": []})
check("17 newer OPEN snapshot of today replaces the older one (2.0 -> 2.5 kWh)", psql(f"select generation_kwh from energy_daily where device_id='{dev}' and operating_date='{T}'") == "2.500000")
sync({**E, "today": day(T, "OPEN", 2.2, 1.7, ga=2.2, upd=1.5), "closed_days": []})
check("18 out-of-order older OPEN snapshot ignored (updated_at older)", psql(f"select generation_kwh from energy_daily where device_id='{dev}' and operating_date='{T}'") == "2.500000")
check("19 garbage energy block never breaks the sync (200) and stores nothing extra", sync({"today": {"operating_date": "nope"}, "closed_days": "x", "meters": [1, 2]}).status_code == 200 and psql(f"select count(*) from energy_daily where device_id='{dev}'") == "3")

# ---------------------------------------------------------------- summary
s = adm.get(f"{B}/api/energy/summary", params=Q).json()
A, Bw = s["wings"]["A"], s["wings"]["B"]
check("20 summary uses the Pi's operating date; wing A consumption today 1.9, yesterday 7.0, this_month 14.9, reset period 2026-06 = 14.9, lifetime 14.9", s["as_of_operating_date"] == T.isoformat() and A["consumption"]["today"]["kwh"] == 1.9 and A["consumption"]["yesterday"]["kwh"] == 7.0 and A["consumption"]["this_month"]["kwh"] == 14.9 and A["consumption"]["reset_period"]["period"] == "2026-06" and A["consumption"]["reset_period"]["kwh"] == 14.9 and A["consumption"]["lifetime"]["kwh"] == 14.9)
check("21 wing A generation today 2.5 (attributed M1), wing B consumption today UNAVAILABLE (no data today, not 0) but yesterday 5.0", A["generation"]["today"]["kwh"] == 2.5 and Bw["consumption"]["today"]["kwh"] is None and Bw["consumption"]["today"]["status"] == "UNAVAILABLE" and Bw["consumption"]["yesterday"]["kwh"] == 5.0)
check("22 wing C consumption meter disabled -> consumption UNAVAILABLE (reason), never 0; previous_month UNAVAILABLE", s["wings"]["C"]["consumption"]["status"] == "UNAVAILABLE" and "disabled" in s["wings"]["C"]["consumption"]["reason"] and A["consumption"]["previous_month"]["kwh"] is None)
g = s["generation_meter"]
check("23 generation meter card: M1 ONLINE, serial, 8.42 kW, today 2.5 / month 24.5 / lifetime 24.5, unattributed 3.0, active wing A (from verified attribution), PHYSICAL", g["status"] == "ONLINE" and g["serial"] == "G-1" and g["current_power_kw"] == 8.42 and g["generation"]["today"]["kwh"] == 2.5 and g["generation"]["this_month"]["kwh"] == 24.5 and g["generation"]["lifetime"]["kwh"] == 24.5 and g["unattributed"]["lifetime"]["kwh"] == 3.0 and g["active_generation_wing"] == "A" and g["source"] == "PHYSICAL")
check("24 sum(daily) == monthly == lifetime (no double counting across days)", abs((10.0 + 12.0 + 2.5) - g["generation"]["this_month"]["kwh"]) < 1e-6)

# ---------------------------------------------------------------- bills / daily average / target
months = [{"month": f"2026-{m:02d}", "consumption_kwh": k} for m, k in ((4, 4820), (5, 5120), (6, 4940), (7, 5310), (8, 5080), (9, 4900))]
b = adm.put(f"{B}/api/energy/bills", json=body(wing="A", months=months)).json()
days = sum(calendar.monthrange(2026, m)[1] for m in range(4, 10))
check("25 six-month bill history: total 30170, ACTUAL days 183 (not 180), average 164.8634, labelled HISTORICAL BILL REFERENCE", b["total_kwh"] == 30170 and b["days"] == days == 183 and abs(b["daily_average_kwh"] - 30170 / 183) < 1e-3 and b["label"] == "HISTORICAL BILL REFERENCE" and len(b["months"]) == 6)
check("26 bill validation: 7 months / bad month / negative kWh -> 400", adm.put(f"{B}/api/energy/bills", json=body(wing="A", months=months + [{"month": "2026-10", "consumption_kwh": 1}])).status_code == 400 and adm.put(f"{B}/api/energy/bills", json=body(wing="A", months=[{"month": "2026/04", "consumption_kwh": 1}])).status_code == 400 and adm.put(f"{B}/api/energy/bills", json=body(wing="A", months=[{"month": "2026-04", "consumption_kwh": -1}])).status_code == 400)
check("27 target for a wing without bills -> 409", adm.post(f"{B}/api/energy/targets", json=body(wing="B", adjustment_percent=10)).status_code == 409)
t = adm.post(f"{B}/api/energy/targets", json=body(wing="A", adjustment_percent=10, effective_from=D1.isoformat(), reason="summer")).json()["target"]
check("28 +10% target = 164.8634 x 1.10 = 181.35 kWh/day; basis persisted; bills unchanged", abs(t["target_kwh_per_day"] - 181.35) < 0.01 and t["adjustment_percent"] == 10.0 and t["basis"]["days"] == 183 and adm.get(f"{B}/api/energy/bills", params={**Q, "wing": "A"}).json()["total_kwh"] == 30170)
t2 = adm.post(f"{B}/api/energy/targets", json=body(wing="A", adjustment_percent=-20, effective_from=T.isoformat())).json()["target"]
check("29 negative adjustment (-20%) accepted = 131.89; GET targets current = latest effective, history has 2", abs(t2["target_kwh_per_day"] - 30170 / 183 * 0.8) < 0.01 and len(adm.get(f"{B}/api/energy/targets", params={**Q, "wing": "A"}).json()["history"]) == 2)
s = adm.get(f"{B}/api/energy/summary", params=Q).json()["wings"]["A"]["required_generation"]
check("30 summary wing A required target (today) 131.89, generated 2.5, achievement 1.9%, NOT_REACHED", abs(s["target_kwh_per_day"] - 131.89) < 0.01 and s["generated_today_kwh"] == 2.5 and s["achievement_percent"] == 1.9 and s["status"] == "NOT_REACHED")

# ---------------------------------------------------------------- graph (aligned dates) / monthly / history / adjustments
gr = adm.get(f"{B}/api/energy/graph/wing", params={**Q, "wing": "A", "range": "7d"}).json(); rows = {r["date"]: r for r in gr["rows"]}
check("31 wing graph 7d: 7 aligned rows, one per operating date, required/generated/consumed on the SAME date; D1 uses 181.35, T uses 131.89", len(gr["rows"]) == 7 and gr["rows"][-1]["date"] == T.isoformat() and rows[D1.isoformat()]["generated_kwh"] == 10.0 and rows[D1.isoformat()]["consumed_kwh"] == 6.0 and abs(rows[D1.isoformat()]["required_kwh"] - 181.35) < 0.01 and abs(rows[T.isoformat()]["required_kwh"] - 131.89) < 0.01 and rows[D1.isoformat()]["generation_minus_consumption_kwh"] == 4.0)
missing = rows[(T - timedelta(days=5)).isoformat()]
check("32 dates without data: generated/consumed None (UNAVAILABLE), target_status UNAVAILABLE, source UNAVAILABLE — never 0", missing["generated_kwh"] is None and missing["consumed_kwh"] is None and missing["target_status"] == "UNAVAILABLE" and missing["source"] == "UNAVAILABLE" and missing["required_kwh"] is None)
check("33 target status/achievement per date: D1 NOT_REACHED 5.5%; source PHYSICAL", rows[D1.isoformat()]["target_status"] == "NOT_REACHED" and rows[D1.isoformat()]["target_achievement_percent"] == 5.5 and rows[D1.isoformat()]["source"] == "PHYSICAL")
adm.post(f"{B}/api/energy/adjustments", json=body(wing="A", operating_date=D1.isoformat(), kind="MANUAL_GENERATION", value_kwh=180.0, reason="meter outage estimate entered by admin"))
rows = {r["date"]: r for r in adm.get(f"{B}/api/energy/graph/wing", params={**Q, "wing": "A", "range": "7d"}).json()["rows"]}
check("34 manual adjustment: physical kept, combined 190 shown with source MIXED, target REACHED; adjustment listed with source MANUAL", rows[D1.isoformat()]["generated_kwh"] == 190.0 and rows[D1.isoformat()]["source"] == "MIXED" and rows[D1.isoformat()]["target_status"] == "REACHED" and psql(f"select generation_kwh from energy_daily where device_id='{dev}' and operating_date='{D1}'") == "10.000000" and adm.get(f"{B}/api/energy/adjustments", params=Q).json()["rows"][0]["source"] == "MANUAL")
check("35 adjustment validation: missing reason / bad kind -> 400; member -> 403", adm.post(f"{B}/api/energy/adjustments", json=body(wing="A", operating_date=D1.isoformat(), kind="MANUAL_GENERATION", value_kwh=1)).status_code == 400 and adm.post(f"{B}/api/energy/adjustments", json=body(wing="A", operating_date=D1.isoformat(), kind="GUESS", value_kwh=1, reason="x")).status_code == 400 and mem.post(f"{B}/api/energy/adjustments", json=body(wing="A", operating_date=D1.isoformat(), kind="ACCOUNTING", value_kwh=1, reason="x")).status_code == 403)
c = adm.get(f"{B}/api/energy/graph/wing", params={**Q, "wing": "A", "from": D1.isoformat(), "to": T.isoformat()}).json()
check("36 custom range works; >400 days -> 400; to<from -> 400; bad wing -> 400", len(c["rows"]) == 3 and adm.get(f"{B}/api/energy/graph/wing", params={**Q, "wing": "A", "from": "2024-01-01", "to": "2026-06-20"}).status_code == 400 and adm.get(f"{B}/api/energy/graph/wing", params={**Q, "wing": "A", "from": "2026-06-20", "to": "2026-06-01"}).status_code == 400 and adm.get(f"{B}/api/energy/graph/wing", params={**Q, "wing": "E", "range": "7d"}).status_code == 400)
mg = adm.get(f"{B}/api/energy/generation/monthly", params=Q).json()
check("37 monthly generation: 6 rows ending at the Pi's current month, 2026-06 = 24.5 PARTIAL (3 of 20 days), older months UNAVAILABLE, no consumption fields", len(mg["rows"]) == 6 and mg["rows"][-1]["month"] == "2026-06" and mg["rows"][-1]["generation_kwh"] == 24.5 and mg["rows"][-1]["completeness"] == "PARTIAL" and mg["rows"][0]["month"] == "2026-01" and mg["rows"][0]["generation_kwh"] is None and mg["rows"][0]["completeness"] == "UNAVAILABLE" and not any("consum" in k for k in mg["rows"][0]))
check("38 monthly generation 12 months / bounds", len(adm.get(f"{B}/api/energy/generation/monthly", params={**Q, "months": 12}).json()["rows"]) == 12 and adm.get(f"{B}/api/energy/generation/monthly", params={**Q, "months": 99}).status_code == 400)
h = adm.get(f"{B}/api/energy/history", params={**Q, "range": "30d"}).json(); hm = adm.get(f"{B}/api/energy/history", params={**Q, "range": "30d", "granularity": "monthly"}).json()
check("39 history daily (3 rows, bounded) and monthly (2026-06 gen 24.5, wing A cons 14.9, wing C None)", h["count"] == 3 and hm["rows"][0]["generation_kwh"] == 24.5 and hm["rows"][0]["wing_consumption"]["A"] == 14.9 and hm["rows"][0]["wing_consumption"]["C"] is None)
# ---------------------------------------------------------------- disabled generation meter => UNAVAILABLE everywhere (data retained)
adm.put(f"{B}/api/energy/meters/M1", json=body(enabled=False))
s = adm.get(f"{B}/api/energy/summary", params=Q).json(); mg = adm.get(f"{B}/api/energy/generation/monthly", params=Q).json(); gr = adm.get(f"{B}/api/energy/graph/wing", params={**Q, "wing": "A", "range": "7d"}).json()
check("40 M1 disabled: card status DISABLED, generation UNAVAILABLE, active wing UNAVAILABLE, monthly chart UNAVAILABLE, wing generation UNAVAILABLE, graph generated None (manual-entry day: generation_source MANUAL, row MIXED with physical consumption) — never zero; bills still visible", s["generation_meter"]["status"] == "DISABLED" and s["generation_meter"]["generation"]["status"] == "UNAVAILABLE" and s["generation_meter"]["active_generation_wing"] == "UNAVAILABLE" and mg["rows"][-1]["generation_kwh"] is None and s["wings"]["A"]["generation"]["status"] == "UNAVAILABLE" and {r["date"]: r for r in gr["rows"]}[T.isoformat()]["generated_kwh"] is None and {r["date"]: r for r in gr["rows"]}[D1.isoformat()]["generation_source"] == "MANUAL" and {r["date"]: r for r in gr["rows"]}[D1.isoformat()]["source"] == "MIXED" and adm.get(f"{B}/api/energy/bills", params={**Q, "wing": "A"}).json()["total_kwh"] == 30170)
check("41 history preserved while disabled (rows still in energy_daily); re-enable restores values", psql(f"select count(*) from energy_daily where device_id='{dev}'") == "3" and adm.put(f"{B}/api/energy/meters/M1", json=body(enabled=True)).json()["success"] and adm.get(f"{B}/api/energy/summary", params=Q).json()["generation_meter"]["generation"]["today"]["kwh"] == 2.5)
check("42 audit rows written for meter/bus/bill/target/adjustment writes; no api key leaked", int(psql(f"select count(*) from audit_log where society_id={sid} and action like 'ENERGY_%'")) >= 8 and psql(f"select count(*) from audit_log where details::text like '%{key}%'") == "0")

# ---------------------------------------------------------------- E3: allocation config (backend-authoritative)
al = adm.get(f"{B}/api/energy/allocation", params=Q).json()["allocation"]
check("43 default allocation: disabled, sequence A-D, tolerance 1.0, persistence 300, per-wing attribution flags", al["enabled"] is False and al["sequence"] == ["A", "B", "C", "D"] and al["tolerance_kwh"] == 1.0 and al["persistence_s"] == 300 and al["wings"]["A"]["generation_attribution_enabled"] is True)
check("44 allocation validation: wrong sequence / persistence < 10 / member -> 400/400/403", adm.put(f"{B}/api/energy/allocation", json=body(sequence=["B", "A", "C", "D"])).status_code == 400 and adm.put(f"{B}/api/energy/allocation", json=body(persistence_s=1)).status_code == 400 and mem.put(f"{B}/api/energy/allocation", json=body(enabled=True)).status_code == 403)
adm.put(f"{B}/api/energy/meters/M1", json=body(enabled=False))
check("45 enabling allocation while M1 is disabled -> 409", adm.put(f"{B}/api/energy/allocation", json=body(enabled=True)).status_code == 409)
adm.put(f"{B}/api/energy/meters/M1", json=body(enabled=True))
r = adm.put(f"{B}/api/energy/allocation", json=body(enabled=True, tolerance_kwh=0.5, persistence_s=120, wings={"D": {"manual_target_kwh": 50, "generation_attribution_enabled": True}, "B": {"generation_attribution_enabled": False}})).json()
cfgr = sync({"config_version": 0}).json()["energy_config"]
check("46 allocation saved + version bumped; energy_config delivers allocation, per-wing flags/manual target and latest targets (A 131.89 effective today)", r["success"] and r["allocation"]["enabled"] and r["allocation"]["wings"]["D"]["manual_target_kwh"] == 50 and r["allocation"]["wings"]["B"]["generation_attribution_enabled"] is False and cfgr["allocation"]["persistence_s"] == 120 and abs(cfgr["targets"]["A"]["target_kwh_per_day"] - 131.89) < 0.01 and "B" not in cfgr["targets"] and cfgr["version"] == r["config_version"])
check("47 sync reports allocation view in energy block without error (ingest ignores it)", sync({"config_version": cfgr["version"], "allocation": {"status": "RUNNING", "current_wing": "A"}, "meters": {}, "today": None}).status_code == 200)
print(f"\n{sum(R)}/{len(R)} passed"); sys.exit(0 if all(R) else 1)
