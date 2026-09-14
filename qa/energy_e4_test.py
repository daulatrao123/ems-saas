"""E4 energy UI tests: (1) structural checks on the frontend energy components, (2) compiled TypeScript helpers executed under
node (source labels / never-zero formatting), (3) live-backend contract the UI consumes, seeding a society the UI can be opened on.
Logins: 3 (limit 5/min). Prints ENERGY_E4_SOCIETY=<id> for browser verification."""
import os, sys, re, json, uuid, hashlib, subprocess, requests
from datetime import datetime, timezone, timedelta
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FE = os.path.join(ROOT, "frontend/src/components/ops")
R = []
def check(n, c, d=""): R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + (f"  [{d}]" if d else ""))
def src(rel): return open(os.path.join(FE, rel), encoding="utf-8").read()
types, hook, panel, wing, gen, bars, tl, dash = (src(f) for f in ("energy/types.ts", "energy/useEnergy.ts", "energy/EnergyPanel.tsx", "energy/WingEnergyCard.tsx", "energy/GenerationCard.tsx", "energy/MonthlyBars.tsx", "energy/AllocationTimeline.tsx", "OperationalDashboard.tsx"))
energy_all = types + hook + panel + wing + gen + bars + tl

# ---------------------------------------------------------------- structural
check("1 exactly four wings A-D: WINGS constant and EnergyPanel maps WINGS -> WingEnergyCard", 'WINGS = ["A", "B", "C", "D"]' in types and "WINGS.map((w) =>" in panel and panel.count("<WingEnergyCard") == 1 and 'data-testid={`energy-wing-card-${w}`}' in wing)
check("2 M1 generation card rendered by EnergyPanel", "<GenerationCard" in panel and 'data-testid="energy-generation-card"' in gen and "M1 · Common Generation Meter" in gen)
check("3 six-month monthly bar chart is the default", "DEFAULT_MONTHS = 6" in types and "months=${DEFAULT_MONTHS}" in hook and "<MonthlyBars" in gen and "<svg" in bars and "<rect" in bars)
check("4 Required Target displayed", "REQUIRED TARGET" in wing and "energy-wing-target-${w}" in wing and "req.target_kwh_per_day" in wing)
check("5 Actual Generation displayed", "ACTUAL GENERATION" in wing and "energy-wing-generation-${w}" in wing)
check("6 Actual Consumption displayed", "ACTUAL CONSUMPTION" in wing and "energy-wing-consumption-${w}" in wing)
check("7-10 source labels Physical / Adaptive Estimate / Manual / Mixed / Unavailable defined and shown on wing + M1 cards", all(f'{k}: "{v}"' in types for k, v in (("PHYSICAL", "Physical"), ("ADAPTIVE_ESTIMATE", "Adaptive Estimate"), ("MANUAL", "Manual"), ("MIXED", "Mixed"), ("UNAVAILABLE", "Unavailable"))) and "sourceLabel(wing.source)" in wing and "sourceLabel(meter.source)" in gen)
check("11 no hidden fallback-to-zero on energy values (only the chart scale uses ?? 0); null renders UNAVAILABLE / N/A", re.findall(r"\?\?\s*0\b|\|\|\s*0\b", energy_all) == ["?? 0"] and "r.generation_kwh ?? 0" in bars and 'v == null ? "UNAVAILABLE"' in types and 'v == null' in bars and ">N/A<" in bars)
check("12 M1 unavailable state represented (health badge, UNAVAILABLE banner with backend reason, monthly-unavailable)", "energy-generation-health" in gen and "energy-generation-unavailable" in gen and "reasonOf(meter.generation)" in gen and "energy-monthly-unavailable" in gen)
check("13 per-wing meter health and UNAVAILABLE reason shown per wing (a failed meter affects only its wing)", "energy-wing-meter-${w}" in wing and "reasonOf(wing.consumption)" in wing and "reasonOf(wing.generation)" in wing)
check("14 today's allocation events displayed from the existing pi-events feed (energy_allocation_* only, operating-date filtered)", "/api/admin/pi-events" in hook and "isAllocationEvent" in hook and 'ALLOCATION_EVENT_PREFIX = "ENERGY_ALLOCATION_"' in types and "=== operatingDate" in tl and "energy-allocation-timeline" in tl)
check("15 no switching logic in the UI: energy components never write, never queue commands, never reference set_active_slot/off_slot/pi-command", not re.search(r"api\.(post|put|delete|patch)\(|pi-command|set_active_slot|off_slot|\bqueue\(|setSlotConfig|transition", energy_all))
check("16 member gains no E4 write controls: EnergyPanel receives neither queue nor setSlotConfig nor readOnly-gated controls; GET-only hook", "queue" not in panel and "setSlotConfig" not in panel and hook.count("api.get(") == 4 and "api.post" not in hook)
check("17 existing dashboard untouched apart from the additive EnergyPanel line: 4 gated control panels + SlotCard + logs still present", all(t in dash for t in ("{!readOnly && <SystemControls", "{!readOnly && <ResetDayControl", "<UnitAllotment", "<LcdControl", "<SlotCard", "<OperationalLogs")) and dash.count("<EnergyPanel") == 1 and "{societyId && <EnergyPanel" in dash)
sha = lambda rel: hashlib.sha256(open(os.path.join(ROOT, rel), "rb").read()).hexdigest()
check("18 GPIO/logger untouched by E4 (current blobs unchanged)", sha("pi_firmware/gpio_manager.py") == "fdd3f38ca366e44ba1aff690e69240d6c314b9b2f7fe4d5063d932db821f9136" and sha("pi_firmware/logger.py") == "8d62ae5ef5bff7a259b6612e3f7508b0585082f980d2a4a8df5c5060a036fffb")

# ---------------------------------------------------------------- compiled helpers under node (real behaviour, not grep)
out = "/tmp/e4_types_js"; subprocess.run(["npx", "--yes", "--package=typescript@5.9.3", "tsc", "src/components/ops/energy/types.ts", "--outDir", out, "--module", "commonjs", "--target", "es2020", "--skipLibCheck"], cwd=os.path.join(ROOT, "frontend"), check=True)
node = subprocess.run(["node", "-e", f"""const t=require('{out}/types.js');console.log(JSON.stringify({{
 p:t.sourceLabel('PHYSICAL'),a:t.sourceLabel('ADAPTIVE_ESTIMATE'),m:t.sourceLabel('MANUAL'),x:t.sourceLabel('MIXED'),u:t.sourceLabel('UNAVAILABLE'),n:t.sourceLabel(null),k:t.sourceLabel('weird'),
 z:t.fmtKwh(null),zero:t.fmtKwh(0),v:t.fmtKwh(12.345),tu:t.todayKwh({{status:'UNAVAILABLE',reason:'meter off'}}),tp:t.todayKwh({{today:{{kwh:1.5,days:1,status:'PHYSICAL'}}}}),tn:t.todayKwh({{today:{{kwh:null,days:0,status:'UNAVAILABLE'}}}}),
 ev:t.isAllocationEvent('ENERGY_ALLOCATION_BLOCKED'),ev2:t.isAllocationEvent('TOGGLE'),kind:t.allocationEventKind('ENERGY_ALLOCATION_PAUSED'),months:t.DEFAULT_MONTHS,wings:t.WINGS}}))"""], capture_output=True, text=True)
j = json.loads(node.stdout or "{}")
check("H1 sourceLabel: PHYSICAL->Physical, ADAPTIVE_ESTIMATE->Adaptive Estimate, MANUAL->Manual, MIXED->Mixed, UNAVAILABLE->Unavailable", (j.get("p"), j.get("a"), j.get("m"), j.get("x"), j.get("u")) == ("Physical", "Adaptive Estimate", "Manual", "Mixed", "Unavailable"), node.stderr[-200:])
check("H2 sourceLabel: null -> Unavailable; unknown value is surfaced, never relabelled as physical", j.get("n") == "Unavailable" and j.get("k") == "Unknown (WEIRD)")
check("H3 fmtKwh(null) == UNAVAILABLE (never 0.00 kWh); fmtKwh(0) is a real zero; fmtKwh(12.345) == 12.35 kWh", j.get("z") == "UNAVAILABLE" and j.get("zero") == "0.00 kWh" and j.get("v") == "12.35 kWh")
check("H4 todayKwh: UNAVAILABLE metric -> null, physical -> 1.5, null kwh -> null", j.get("tu") is None and j.get("tp") == 1.5 and j.get("tn") is None)
check("H5 allocation event helpers: prefix filter + kind extraction; defaults months=6, wings A-D", j.get("ev") is True and j.get("ev2") is False and j.get("kind") == "PAUSED" and j.get("months") == 6 and j.get("wings") == ["A", "B", "C", "D"])

# ---------------------------------------------------------------- live backend contract consumed by the UI (seeds a society for browser checks)
B = "http://localhost:8001"; O = "http://localhost:3000"
def login(e, p):
    s = requests.Session(); r = s.post(f"{B}/api/auth/login", json={"email": e, "password": p}, headers={"Origin": O}); assert r.status_code == 200, r.text
    for ck in s.cookies: ck.secure = False
    s.headers.update({"Origin": O, "X-CSRF-Token": s.cookies.get("ems_csrf")}); return s
tag = uuid.uuid4().hex[:6]; sa = login("admin@ems.com", os.environ.get("EMS_BOOTSTRAP_PASSWORD", "Boot#Pass123"))
sid = sa.post(f"{B}/api/super-admin/societies", json={"name": f"E4-UI-{tag}", "reset_day": 15}).json()["society_id"]
u = sa.post(f"{B}/api/super-admin/users", json={"society_id": sid, "role": "society_admin", "email": f"e4a-{tag}@t.test", "name": "A"}).json(); adm = login(u["email"], u["temporary_password"])
mu = sa.post(f"{B}/api/super-admin/users", json={"society_id": sid, "role": "member", "email": f"e4m-{tag}@t.test", "name": "M"}).json(); mem = login(mu["email"], mu["temporary_password"])
reg = adm.post(f"{B}/api/admin/devices/register", json={"name": f"e4-{tag}"}).json(); dev, key = reg["device_id"], reg["api_key"]
Q = {"society_id": sid, "device_id": dev}; body = lambda **k: {"society_id": sid, "device_id": dev, **k}
VMAP = {"name": "TEST", "verified": True, "registers": {"energy_total_kwh": {"address": 342, "function": 4, "type": "float32", "word_order": "big", "scale": 1.0, "unit": "kWh"}}}
adm.put(f"{B}/api/energy/bus", json=body(port="/dev/serial/by-id/usb-rs485", serial={"baudrate": 9600, "parity": "N"}))
adm.put(f"{B}/api/energy/meters/M1", json=body(enabled=True, serial="G-1", modbus_address=1, register_map=VMAP, max_kw=50))
adm.put(f"{B}/api/energy/meters/M2", json=body(enabled=True, serial="A-1", modbus_address=2, register_map=VMAP))
adm.put(f"{B}/api/energy/meters/M3", json=body(enabled=True, serial="B-1", modbus_address=3, register_map=VMAP))
ver = adm.get(f"{B}/api/energy/meters", params=Q).json()["config_version"]
adm.put(f"{B}/api/energy/bills", json=body(wing="A", months=[{"month": f"2025-{mm:02d}", "consumption_kwh": 300.0, "days": 30} for mm in range(1, 7)]))   # 1800 kWh / 180 d = 10.0 kWh/day
tgt = adm.post(f"{B}/api/energy/targets", json=body(wing="A", adjustment_percent=10.0))
check("L0 target = six-month daily average x (1 + adjustment%): 10.0 x 1.10 = 11.0", tgt.status_code == 200 and float(tgt.json().get("target", tgt.json()).get("target_kwh_per_day", 0) if isinstance(tgt.json().get("target", None), dict) else tgt.json().get("target_kwh_per_day", 0)) == 11.0, tgt.text[:160])
T = datetime.now(timezone.utc).date(); ts = datetime.now(timezone.utc).timestamp()
def day(d, status, gen, wa, wb, ga, upd):
    return {"operating_date": d.isoformat(), "reset_period": T.strftime("%Y-%m"), "status": status, "generation_kwh": gen, "generation_source": "PHYSICAL",
            "wing_generation": {"A": ga, "B": None, "C": None, "D": None}, "unattributed_generation_kwh": 0.0, "fault_generation_kwh": 0.0,
            "wing_consumption": {"A": wa, "B": wb, "C": None, "D": None}, "consumption_source": {"A": "PHYSICAL", "B": "PHYSICAL" if wb is not None else "UNAVAILABLE", "C": "UNAVAILABLE", "D": "UNAVAILABLE"},
            "samples": {"M1": 10}, "attempts": {"M1": 10}, "gap_kwh": {}, "events": [], "opened_at": 1.0, "closed_at": None, "updated_at": upd}
closed = [day(T - timedelta(days=n), "CLOSED", 9.0 + n, 5.0, 3.0, 9.0 + n, 1.0) for n in range(1, 40)]
E = {"config_version": ver, "meters": {"M1": {"comm_status": "ONLINE", "last_seen": ts, "last_kwh": 1000.5, "power_kw": 8.42}, "M2": {"comm_status": "ONLINE", "last_seen": ts, "last_kwh": 200.0, "power_kw": 3.1}, "M3": {"comm_status": "OFFLINE", "last_error": "timeout"}},
     "attribution": {"wing": "A", "status": "ATTRIBUTED", "reason": None, "ts": ts}, "today": day(T, "OPEN", 12.5, 4.2, None, 12.5, 2.0), "closed_days": closed}
p = {"slots": {s: {"physical_toggle": "ON" if s == "A" else "OFF", "used_days": 0, "clicks": 0} for s in "ABCD"}, "energy": E,
     "events": [{"eventId": str(uuid.uuid4()), "timestamp": datetime.now(timezone.utc).isoformat(), "type": "energy_allocation_blocked", "message": "no action: M1_STALE_OR_OFFLINE (was RUNNING wing=A); persistence timer cleared"},
                {"eventId": str(uuid.uuid4()), "timestamp": datetime.now(timezone.utc).isoformat(), "type": "energy_allocation_resumed", "message": "safety condition cleared; reconciled verified active=A; state=RUNNING"}]}
r = requests.post(f"{B}/api/pi/sync", json=p, headers={"X-Device-ID": dev, "X-API-Key": key})
check("L1 seed sync accepted (energy block + 2 allocation events)", r.status_code == 200, r.text[:120])
s = adm.get(f"{B}/api/energy/summary", params=Q).json(); A, Bw, C = s["wings"]["A"], s["wings"]["B"], s["wings"]["C"]
check("L2 summary: wing A PHYSICAL (consumption 4.2, generation 12.5, target 11.0 = 10 x 1.10, REACHED)", A["source"] == "PHYSICAL" and A["consumption"]["today"]["kwh"] == 4.2 and A["generation"]["today"]["kwh"] == 12.5 and A["required_generation"]["target_kwh_per_day"] == 11.0 and A["required_generation"]["status"] == "REACHED")
check("L3 failed wing meter affects only its wing: M3 OFFLINE -> wing B consumption today null (not 0), meter comm OFFLINE; wing A unaffected; wing C DISABLED -> UNAVAILABLE with reason", Bw["consumption"]["today"]["kwh"] is None and Bw["consumption_meter"]["comm_status"] == "OFFLINE" and A["consumption"]["today"]["kwh"] == 4.2 and C["consumption"]["status"] == "UNAVAILABLE" and "disabled" in C["consumption"]["reason"])
check("L4 M1 card data: ONLINE, PHYSICAL, active_generation_wing A, power 8.42", s["generation_meter"]["status"] == "ONLINE" and s["generation_meter"]["source"] == "PHYSICAL" and s["generation_meter"]["active_generation_wing"] == "A" and s["generation_meter"]["current_power_kw"] == 8.42)
m = adm.get(f"{B}/api/energy/generation/monthly", params={**Q, "months": 6}).json()
check("L5 monthly chart contract: 6 rows, last row is the current month with PHYSICAL data, early months UNAVAILABLE (null, not 0)", m["months"] == 6 and len(m["rows"]) == 6 and m["rows"][-1]["month"] == T.strftime("%Y-%m") and m["rows"][-1]["generation_kwh"] is not None and m["rows"][0]["generation_kwh"] is None and m["rows"][0]["source"] == "UNAVAILABLE")
ev = adm.get(f"{B}/api/admin/pi-events", params={**Q, "latest": 100}).json()["events"]
kinds = [e["level"] for e in ev if e["level"].startswith("ENERGY_ALLOCATION_")]
check("L6 today's allocation events available to the timeline (BLOCKED + RESUMED, verbatim messages)", set(kinds) == {"ENERGY_ALLOCATION_BLOCKED", "ENERGY_ALLOCATION_RESUMED"} and any("M1_STALE_OR_OFFLINE" in e["msg"] for e in ev))
check("L7 member: can GET summary/monthly/events (read-only view) but every energy write is 403 server-side", mem.get(f"{B}/api/energy/summary", params=Q).status_code == 200 and mem.get(f"{B}/api/energy/generation/monthly", params=Q).status_code == 200 and mem.get(f"{B}/api/admin/pi-events", params={**Q, "latest": 10}).status_code == 200
      and mem.put(f"{B}/api/energy/allocation", json=body(enabled=True, sequence=list("ABCD"))).status_code == 403 and mem.put(f"{B}/api/energy/meters/M2", json=body(serial="x")).status_code == 403 and mem.post(f"{B}/api/energy/targets", json=body(wing="A", base_daily_average_kwh=1, adjustment_percent=0)).status_code == 403)
adm.put(f"{B}/api/energy/meters/M1", json=body(enabled=False)); s2 = adm.get(f"{B}/api/energy/summary", params=Q).json()
check("L8 M1 disabled -> generation UNAVAILABLE everywhere with reason (never zero): M1 status DISABLED, wing A generation UNAVAILABLE, target status UNAVAILABLE, monthly source UNAVAILABLE", s2["generation_meter"]["status"] == "DISABLED" and s2["generation_meter"]["generation"]["status"] == "UNAVAILABLE" and s2["wings"]["A"]["generation"]["status"] == "UNAVAILABLE" and s2["wings"]["A"]["required_generation"]["status"] == "UNAVAILABLE" and s2["wings"]["A"]["consumption"]["today"]["kwh"] == 4.2
      and adm.get(f"{B}/api/energy/generation/monthly", params=Q).json()["source"] == "UNAVAILABLE")
adm.put(f"{B}/api/energy/meters/M1", json=body(enabled=True))
print(f"\nENERGY_E4_SOCIETY={sid} DEVICE={dev} ADMIN={u['email']} MEMBER={mu['email']}")
print(f"{sum(R)}/{len(R)} passed"); sys.exit(0 if all(R) else 1)
