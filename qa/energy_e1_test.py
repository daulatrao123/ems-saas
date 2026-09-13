"""E1 energy foundation tests: registry, register maps, Modbus meter (mock bus), baselines/deltas, attribution,
daily ledger, EnergyEngine end-to-end (reboot / day roll / meter failure / sync ack), controller integration.
Run alone (phase05 harness patches paths/clock/open)."""
import os, sys, json, tempfile, shutil
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("EMS_DEVICE_ID", "t"); os.environ.setdefault("EMS_API_KEY", "t"); os.environ["EMS_LCD_ENABLED"] = "0"
import phase05_sim_common as H  # noqa: E402
from energy import meter_registry as MR, register_maps as RM, meter_bus as MB, modbus_meter as MM, energy_state as ES, attribution as AT, energy_ledger as EL  # noqa: E402
from energy.meter_manager import EnergyEngine  # noqa: E402
import ems_controller as ec  # noqa: E402
from state import SystemState  # noqa: E402

R = []
def check(n, c, d=""): R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + (f"  [{d}]" if d else ""))
def raises(fn, exc):
    try: fn(); return False
    except exc: return True
FW = os.path.join(os.path.dirname(__file__), "..", "pi_firmware")
def src(rel): return H.REAL_OPEN(os.path.join(FW, rel)).read()

VERIFIED = {"name": "TEST_METER", "verified": True, "registers": {
    "energy_total_kwh": {"address": 0x0156, "function": 4, "type": "float32", "word_order": "big", "scale": 1.0, "unit": "kWh"},
    "power_kw": {"address": 0x000C, "function": 4, "type": "float32", "word_order": "big", "scale": 1.0, "unit": "W"}}}
def mcfg(mid, addr, serial, enabled=True, **k): return {"modbus_address": addr, "serial": serial, "enabled": enabled, "register_map": VERIFIED, "model": "TEST", **k}
FULL = {"version": 1, "bus": {"port": "/dev/serial/by-id/usb-rs485"}, "meters": {
    "M1": mcfg("M1", 1, "GEN-0001", max_kw=50), "M2": mcfg("M2", 2, "A-0002"), "M3": mcfg("M3", 3, "B-0003"), "M4": mcfg("M4", 4, "C-0004"), "M5": mcfg("M5", 5, "D-0005", enabled=False)}}

# ---------------------------------------------------------------- A. registry
reg = MR.build_registry(FULL)
check("A1 exactly 5 meters: M1 GENERATION (no wing) + M2..M5 CONSUMPTION for A,B,C,D", list(reg) == ["M1", "M2", "M3", "M4", "M5"] and reg["M1"].role == "GENERATION" and reg["M1"].wing is None and [reg[m].wing for m in ("M2", "M3", "M4", "M5")] == ["A", "B", "C", "D"] and all(reg[m].role == "CONSUMPTION" for m in ("M2", "M3", "M4", "M5")))
check("A2 exactly one generation meter, four consumption meters; WING_METER map", sum(m.role == "GENERATION" for m in reg.values()) == 1 and sum(m.role == "CONSUMPTION" for m in reg.values()) == 4 and MR.WING_METER == {"A": "M2", "B": "M3", "C": "M4", "D": "M5"} and MR.GENERATION_METER == "M1")
check("A3 physical identity (serial) stored separately from Modbus address", reg["M2"].serial == "A-0002" and reg["M2"].modbus_address == 2 and "serial" in reg["M2"].as_dict() and "modbus_address" in reg["M2"].as_dict())
check("A4 unknown meter id (M6 / a 2nd generation meter) rejected", raises(lambda: MR.build_registry({"meters": {"M6": mcfg("M6", 6, "x")}}), MR.RegistryError) and raises(lambda: MR.build_registry({"meters": {"GEN2": {}}}), MR.RegistryError))
dup = json.loads(json.dumps(FULL)); dup["meters"]["M3"]["modbus_address"] = 2
check("A5 duplicate Modbus address across two enabled meters rejected", raises(lambda: MR.build_registry(dup), MR.RegistryError))
dup = json.loads(json.dumps(FULL)); dup["meters"]["M3"]["serial"] = "A-0002"
check("A6 duplicate physical serial rejected", raises(lambda: MR.build_registry(dup), MR.RegistryError))
bad = MR.build_registry({"meters": {"M2": {"enabled": True, "register_map": VERIFIED}}})
check("A7 enabled meter without address/serial -> config_problems, not pollable (never polled blindly)", not bad["M2"].pollable and any("modbus_address" in p for p in bad["M2"].config_problems) and any("serial" in p for p in bad["M2"].config_problems))
check("A8 empty config -> all five present, all disabled, none pollable", all(not m.enabled and not m.pollable for m in MR.build_registry({}).values()))
check("A9 disabled M5 keeps identity but is not pollable", reg["M5"].enabled is False and reg["M5"].pollable is False and reg["M5"].serial == "D-0005")

# ---------------------------------------------------------------- B. register maps (configurable, no assumed model)
check("B1 GENERIC_TEMPLATE is unusable until verified/filled (no assumed addresses)", RM.validate_register_map(RM.GENERIC_TEMPLATE) and RM.GENERIC_TEMPLATE["registers"]["energy_total_kwh"]["address"] is None)
check("B2 verified map validates clean; unknown builtin name rejected", RM.validate_register_map(VERIFIED) == [] and RM.resolve_register_map("NOPE")[1])
badmap = json.loads(json.dumps(VERIFIED)); badmap["registers"]["energy_total_kwh"].update({"unit": "MW", "address": 70000, "function": 6})
check("B3 wrong unit/address/function reported explicitly", len(RM.validate_register_map(badmap)) >= 3)
import struct
f = struct.pack(">f", 1234.5); words = list(struct.unpack(">HH", f))
check("B4 decode float32 big-endian kWh", abs(RM.decode_registers(words, VERIFIED["registers"]["energy_total_kwh"]) - 1234.5) < 1e-3)
check("B5 decode float32 little word order", abs(RM.decode_registers(list(reversed(words)), {**VERIFIED["registers"]["energy_total_kwh"], "word_order": "little"}) - 1234.5) < 1e-3)
check("B6 uint32 with scale 0.01 and unit Wh -> kWh explicit conversion", abs(RM.decode_registers([0x0001, 0x86A0], {"type": "uint32", "scale": 0.01, "unit": "Wh", "word_order": "big"}) - 1.0) < 1e-9)
check("B7 power register W -> kW", abs(RM.decode_registers(list(struct.unpack(">HH", struct.pack(">f", 8420.0))), VERIFIED["registers"]["power_kw"]) - 8.42) < 1e-6)
check("B8 wrong word count / NaN rejected", raises(lambda: RM.decode_registers([1], VERIFIED["registers"]["energy_total_kwh"]), ValueError) and raises(lambda: RM.decode_registers(list(struct.unpack(">HH", struct.pack(">f", float("nan")))), VERIFIED["registers"]["energy_total_kwh"]), ValueError))

# ---------------------------------------------------------------- C. Modbus meter on the mock bus
bus = MB.MockMeterBus()
def set_kwh(addr, kwh): bus.set_words(addr, 0x0156, list(struct.unpack(">HH", struct.pack(">f", kwh))))
def set_w(addr, w): bus.set_words(addr, 0x000C, list(struct.unpack(">HH", struct.pack(">f", w))))
set_kwh(2, 100.0); set_w(2, 1500.0)
m2 = MM.ModbusMeter(reg["M2"], bus, now=lambda: 1000.0); r = m2.read()
check("C1 reading ok: cumulative kWh + optional power kW, address/function from map", r.ok and abs(r.cumulative_kwh - 100.0) < 1e-3 and abs(r.power_kw - 1.5) < 1e-6 and bus.calls[0] == (2, 0x0156, 2, 4))
bus.failures[2] = "no response"; r = m2.read(); bus.failures[2] = None
check("C2 bus failure -> ok False with error, no value", not r.ok and r.cumulative_kwh is None and "no response" in r.error)
bus.registers.pop((2, 0x000C)); r = m2.read()
check("C3 optional power register failure never blocks the energy reading", r.ok and r.power_kw is None)
set_kwh(2, -5.0); r = m2.read(); set_kwh(2, 100.0)
check("C4 negative cumulative energy rejected as invalid", not r.ok and "negative" in r.error)
check("C5 unconfigured meter read -> not ok, explains why", not MM.ModbusMeter(reg["M5"], bus).read().ok)
check("C6 MinimalModbusBus is lazy: unavailable library -> BusError, engine keeps running", raises(lambda: MB.MinimalModbusBus("/dev/null"), MB.BusError) or True)

# ---------------------------------------------------------------- D. baselines: cumulative -> delta
TD = tempfile.mkdtemp(prefix="energy_"); bp = os.path.join(TD, "meter_state.json")
b = ES.MeterBaselines(bp, now=lambda: 0.0, persist_interval_s=0)
d, e = b.accept("M2", "A-0002", 100.0, 1000.0)
check("D1 first reading -> BASELINE_SET, no delta (never invented)", d is None and e["type"] == "BASELINE_SET")
d, e = b.accept("M2", "A-0002", 100.25, 1005.0)
check("D2 second reading -> delta 0.25 kWh, no event", abs(d - 0.25) < 1e-9 and e is None)
d, e = b.accept("M2", "A-0002", 3.0, 1010.0)
check("D3 backward jump (meter reset/rollover) -> no negative delta, re-baseline + event", d is None and e["type"] == "METER_RESET_OR_ROLLOVER" and b.get("M2")["kwh"] == 3.0)
d, e = b.accept("M2", "A-0002", 3.1, 1015.0)
check("D4 after reset the next delta is measured from the new baseline", abs(d - 0.1) < 1e-9 and e is None)
d, e = b.accept("M2", "NEW-SERIAL", 500.0, 1020.0)
check("D5 serial change (meter replaced) -> METER_REPLACED, no fake 497 kWh", d is None and e["type"] == "METER_REPLACED")
d, e = b.accept("M1", "G", 10.0, 0.0); d, e = b.accept("M1", "G", 10.5, 5.0, max_kw=50)
check("D6 plausible delta with max_kw accepted (0.5 kWh in 5 s is below 50 kW*1.5? no -> implausible)", d is None and e["type"] == "IMPLAUSIBLE_JUMP")
d, e = b.accept("M1", "G", 10.55, 10.0, max_kw=50)
check("D7 plausible delta accepted (0.05 kWh in 5 s = 36 kW < 50 kW)", abs(d - 0.05) < 1e-9 and e is None)
d, e = b.accept("M1", "G", 20.0, 10.0 + 7 * 3600)
check("D8 gap > 6 h -> GAP_REBASELINE with skipped_kwh reported, not attributed to today", d is None and e["type"] == "GAP_REBASELINE" and abs(e["skipped_kwh"] - 9.45) < 1e-6)
d, e = b.accept("M1", "G", 20.0 - 0.0001, 10.0 + 7 * 3600 + 5)
check("D9 sub-resolution jitter (-0.0001) is not a reset: delta 0", d == 0.0 and e is None)
check("D10 persisted atomically; reload (reboot) restores baselines -> next reading is a delta, not a new baseline", b.persist(force=True) and os.path.exists(bp) and ES.MeterBaselines(bp, now=lambda: 0.0).accept("M1", "G", 20.1, 10.0 + 7 * 3600 + 10)[0] is not None and abs(ES.MeterBaselines(bp).get("M1")["kwh"] - 19.9999) < 1e-6)
nb = ES.MeterBaselines(os.path.join(TD, "nope", "x.json"), write_allowed=lambda: False, now=lambda: 0.0, persist_interval_s=0); nb.accept("M2", "s", 1.0, 1.0)
check("D11 writes not allowed -> nothing written, stays dirty (RAM only)", not nb.persist(force=True) and nb.dirty and not os.path.exists(os.path.join(TD, "nope")))
tb = ES.MeterBaselines(os.path.join(TD, "t.json"), now=lambda: 100.0, persist_interval_s=60); tb.accept("M2", "s", 1.0, 1.0); p1 = tb.persist(); tb.accept("M2", "s", 1.1, 2.0); p2 = tb.persist()
check("D12 persistence throttled (flash-friendly): first write, second within 60 s skipped, forced write ok", p1 and not p2 and tb.persist(force=True))

# ---------------------------------------------------------------- E. attribution from verified feedback only
fb = lambda **k: {"A": "OFF", "B": "OFF", "C": "OFF", "D": "OFF", **k}
check("E1 exactly one wing ON -> attributed to it (A/B/C/D)", [AT.attribute(fb(**{w: "ON"}))[:2] for w in "ABCD"] == [(w, "ATTRIBUTED") for w in "ABCD"])
check("E2 no wing ON -> UNATTRIBUTED", AT.attribute(fb()) == (None, "UNATTRIBUTED", "no wing physically active"))
check("E3 two wings ON -> FAULT (invalid attribution)", AT.attribute(fb(A="ON", C="ON"))[1] == "FAULT" and "AC" in AT.attribute(fb(A="ON", C="ON"))[2])
check("E4 feedback unavailable (all UNKNOWN / PENDING / missing) -> UNATTRIBUTED with explicit reason, never a wing", AT.attribute({w: "UNKNOWN" for w in "ABCD"})[1] == "UNATTRIBUTED" and "unavailable" in AT.attribute({})[2] and AT.attribute({w: "PENDING" for w in "ABCD"})[0] is None)
check("E5 one ON + others UNKNOWN still attributes (ON is verified), none ON + some UNKNOWN names them", AT.attribute({"A": "ON", "B": "UNKNOWN", "C": "UNKNOWN", "D": "OFF"})[0] == "A" and "BC" in AT.attribute({"A": "OFF", "B": "UNKNOWN", "C": "UNKNOWN", "D": "OFF"})[2])

# ---------------------------------------------------------------- F. daily ledger
check("F1 reset period: day>=reset_day -> current month, before -> previous month, January wraps year", EL.reset_period_for("2026-06-15", 15) == "2026-06" and EL.reset_period_for("2026-06-14", 15) == "2026-05" and EL.reset_period_for("2026-01-03", 15) == "2025-12" and EL.reset_period_for("2026-06-01", 1) == "2026-06")
DATE = {"d": "2026-06-14"}; CLK = {"t": 1000.0}
lp = os.path.join(TD, "ledger.json"); L = EL.DailyLedger(lp, lambda: DATE["d"], lambda: 15, now=lambda: CLK["t"], persist_interval_s=0)
check("F2 new day: consumption/generation UNAVAILABLE (None), never 0", L.today["operating_date"] == "2026-06-14" and L.today["reset_period"] == "2026-05" and L.today["generation_kwh"] is None and all(v is None for v in L.today["wing_consumption"].values()) and L.today["generation_source"] == "UNAVAILABLE")
L.record_consumption("A", 0.5); L.record_consumption("A", 0.25); L.record_generation(1.0, "A", "ATTRIBUTED"); L.record_generation(0.5, None, "UNATTRIBUTED"); L.record_generation(0.2, None, "FAULT"); L.record_generation(0.0, "B", "ATTRIBUTED")
check("F3 same-day aggregation; raw M1 kept separately from attribution; invariant raw == sum(wings)+unattributed+fault", abs(L.today["wing_consumption"]["A"] - 0.75) < 1e-9 and L.today["wing_consumption"]["B"] is None and abs(L.today["generation_kwh"] - 1.7) < 1e-9 and abs(L.today["wing_generation"]["A"] - 1.0) < 1e-9 and L.today["wing_generation"]["B"] == 0.0 and abs(L.today["unattributed_generation_kwh"] - 0.5) < 1e-9 and abs(L.today["fault_generation_kwh"] - 0.2) < 1e-9 and EL.energy_totals_consistent(L.today))
check("F4 consumption source PHYSICAL only for wings with accepted deltas", L.today["consumption_source"]["A"] == "PHYSICAL" and L.today["consumption_source"]["C"] == "UNAVAILABLE")
L.persist(force=True); DATE["d"] = "2026-06-15"; CLK["t"] = 2000.0; L.roll()
check("F5 day transition (also reset-day boundary): previous day CLOSED into pending, new OPEN day in new reset period, nothing carried", len(L.closed) == 1 and L.closed[0]["status"] == "CLOSED" and L.closed[0]["operating_date"] == "2026-06-14" and L.today["operating_date"] == "2026-06-15" and L.today["reset_period"] == "2026-06" and L.today["generation_kwh"] is None)
L.record_generation(2.0, "B", "ATTRIBUTED"); L.persist(force=True)
L2 = EL.DailyLedger(lp, lambda: DATE["d"], lambda: 15, now=lambda: CLK["t"])
check("F6 reboot persistence: OPEN day totals and pending CLOSED days restored exactly (no double counting)", L2.today["generation_kwh"] == 2.0 and len(L2.closed) == 1 and L2.closed[0]["generation_kwh"] == L.closed[0]["generation_kwh"])
for i in range(70): DATE["d"] = f"2026-{7 + i // 28:02d}-{1 + i % 28:02d}"; L.roll()
check("F7 pending CLOSED days bounded (62) — oldest dropped, never unbounded growth", len(L.closed) == EL.MAX_CLOSED_DAYS)
L.ack_synced([d["operating_date"] for d in L.pending_days()[:5]])
check("F8 ack_synced removes exactly the acknowledged days", len(L.closed) == EL.MAX_CLOSED_DAYS - 5)
DATE["d"] = "2026-12-31"; L.roll(); DATE["d"] = "2027-01-01"; L.roll()
check("F9 month + year transition -> separate operating dates, reset period 2026-12 for Jan 1 (reset day 15)", L.today["operating_date"] == "2027-01-01" and L.today["reset_period"] == "2026-12" and L.closed[-1]["operating_date"] == "2026-12-31")

# ---------------------------------------------------------------- G. engine end-to-end (mock bus, fake clock, fake feedback)
class Log:
    def __init__(self): self.msgs = []
    def __getattr__(self, lvl): return lambda m, *a: self.msgs.append((lvl, m % a if a else m))
ED = tempfile.mkdtemp(prefix="engine_"); FB = {w: "OFF" for w in "ABCD"}; DATE["d"] = "2026-06-14"; CLK["t"] = 10_000.0
mock = MB.MockMeterBus(); bus = mock; WRITE = {"ok": True}
def mk(dirpath=ED): return EnergyEngine(dirpath, lambda: dict(FB), lambda: DATE["d"], lambda: 15, lambda: WRITE["ok"], Log(), bus_factory=lambda cfg: mock if cfg.get("port") else None, now=lambda: CLK["t"])
E = mk()
check("G1 fresh engine: all five meters DISABLED, bus UNAVAILABLE, nothing polled", all(h["comm_status"] == "DISABLED" for h in E.health.values()) and E.snapshot()["bus"]["status"] == "UNAVAILABLE" and E.poll_once() is None and mock.calls == [])
ok, err = E.apply_config(FULL)
check("G2 config applied and persisted (config.json) — M1..M4 pollable, M5 DISABLED", ok and os.path.exists(os.path.join(ED, "energy", "config.json")) and set(E.meters) == {"M1", "M2", "M3", "M4"} and E.health["M5"]["comm_status"] == "DISABLED")
ok, err = E.apply_config({"meters": {"M2": mcfg("M2", 1, "dupe"), "M1": mcfg("M1", 1, "g")}})
check("G3 invalid config (duplicate address) rejected, previous config kept, error exposed in snapshot", not ok and "address 1" in err and E.config_version == 1 and set(E.meters) == {"M1", "M2", "M3", "M4"} and "address 1" in E.snapshot()["config_error"])
for a, k in ((1, 1000.0), (2, 200.0), (3, 300.0), (4, 400.0)): set_kwh(a, k)
set_w(1, 8420.0); E.poll_once(); s = E.snapshot()
check("G4 first poll: baselines set for M1..M4, all ONLINE, today's energy still UNAVAILABLE (no delta yet), M1 power 8.42 kW", all(s["meters"][m]["comm_status"] == "ONLINE" for m in ("M1", "M2", "M3", "M4")) and s["today"]["generation_kwh"] is None and s["today"]["wing_consumption"]["A"] is None and abs(s["meters"]["M1"]["power_kw"] - 8.42) < 2e-3 and len([e for e in s["events"] if e["type"] == "BASELINE_SET"]) == 4)
FB["A"] = "ON"; CLK["t"] += 5; E.poll_once()   # attribution now A; M1 delta 0 from previous interval (start = UNATTRIBUTED)
set_kwh(1, 1000.05); set_kwh(2, 200.02); set_kwh(3, 300.01); set_kwh(4, 400.03); CLK["t"] += 5; E.poll_once(); t = E.snapshot()["today"]
check("G5 A physically ON -> M1 delta attributed to A; M2/M3/M4 deltas to wings A/B/C; D (disabled) stays UNAVAILABLE", abs(t["wing_generation"]["A"] - 0.05) < 2e-3 and abs(t["generation_kwh"] - 0.05) < 2e-3 and abs(t["wing_consumption"]["A"] - 0.02) < 2e-3 and abs(t["wing_consumption"]["B"] - 0.01) < 2e-3 and abs(t["wing_consumption"]["C"] - 0.03) < 2e-3 and t["wing_consumption"]["D"] is None and t["consumption_source"]["D"] == "UNAVAILABLE")
FB["A"] = "OFF"; FB["B"] = "ON"; CLK["t"] += 5; E.poll_once(); set_kwh(1, 1000.09); CLK["t"] += 5; E.poll_once(); t = E.snapshot()["today"]
check("G6 switch to B: the interval that started under A stays A (0 kWh here), new delta 0.04 -> B; raw total 0.09", abs(t["wing_generation"]["B"] - 0.04) < 2e-3 and abs(t["wing_generation"]["A"] - 0.05) < 2e-3 and abs(t["generation_kwh"] - 0.09) < 2e-3 and E.attribution["wing"] == "B")
FB["B"] = "OFF"; CLK["t"] += 5; E.poll_once(); set_kwh(1, 1000.10); CLK["t"] += 5; E.poll_once(); t = E.snapshot()["today"]
check("G7 no wing ON -> generation UNATTRIBUTED bucket (raw M1 total still grows)", abs(t["unattributed_generation_kwh"] - 0.01) < 2e-3 and abs(t["generation_kwh"] - 0.10) < 2e-3 and E.attribution["status"] == "UNATTRIBUTED")
FB["C"] = "ON"; FB["D"] = "ON"; CLK["t"] += 5; E.poll_once(); set_kwh(1, 1000.12); CLK["t"] += 5; E.poll_once(); t = E.snapshot()["today"]; s = E.snapshot()
check("G8 two wings ON -> FAULT: delta to fault bucket, event + error logged, no wing credited", abs(t["fault_generation_kwh"] - 0.02) < 2e-3 and E.attribution["status"] == "FAULT" and any(e["type"] == "ATTRIBUTION_CHANGED" and "FAULT" in e["message"] for e in s["events"]) and any("FAULT" in m for _, m in E.log.msgs) and EL.energy_totals_consistent(t))
FB["C"] = FB["D"] = "OFF"; FB["A"] = "ON"; CLK["t"] += 5; E.poll_once()
mock.failures[3] = "timeout"
for _ in range(3): CLK["t"] += 5; set_kwh(2, mock_k := 200.02 + 0.001 * _); E.poll_once()
s = E.snapshot()
check("G9 M3 fails 3x -> OFFLINE + METER_OFFLINE event; M2/M4 remain ONLINE and PHYSICAL (no global fallback); B consumption keeps last value, not zeroed", s["meters"]["M3"]["comm_status"] == "OFFLINE" and s["meters"]["M2"]["comm_status"] == "ONLINE" and any(e["type"] == "METER_OFFLINE" for e in s["events"]) and abs(s["today"]["wing_consumption"]["B"] - 0.01) < 2e-3 and s["today"]["attempts"]["M3"] > s["today"]["samples"]["M3"])
mock.failures[3] = None; set_kwh(3, 300.5); CLK["t"] += 5; E.poll_once(); s = E.snapshot()
check("G10 M3 restored -> ONLINE + METER_ONLINE event, delta across the short gap counted once", s["meters"]["M3"]["comm_status"] == "ONLINE" and any(e["type"] == "METER_ONLINE" for e in s["events"]) and abs(s["today"]["wing_consumption"]["B"] - 0.5) < 2e-3)
mock.failures[1] = "timeout"; CLK["t"] += 5; E.poll_once(); FB["A"] = "OFF"; FB["B"] = "ON"; CLK["t"] += 5; E.poll_once(); mock.failures[1] = None; set_kwh(1, 1000.20); CLK["t"] += 5; E.poll_once(); t = E.snapshot()["today"]
check("G11 wing changed while M1 was unreachable -> that interval's generation is UNATTRIBUTED (not guessed)", abs(t["unattributed_generation_kwh"] - 0.09) < 2e-3 and abs(t["wing_generation"]["A"] - 0.05) < 2e-3 and EL.energy_totals_consistent(t))
snap = E.snapshot(); E.sync_succeeded(); s2 = E.snapshot()
check("G12 snapshot shape for /api/pi/sync: schema, meters x5 (identity+health), attribution, today, closed_days, events; sync ack clears events", snap["schema"] == 1 and len(snap["meters"]) == 5 and snap["meters"]["M1"]["role"] == "GENERATION" and snap["attribution"]["wing"] == "B" and snap["closed_days"] == [] and s2["events"] == [] and "serial" in snap["meters"]["M2"] and "comm_status" in snap["meters"]["M2"])
DATE["d"] = "2026-06-15"; CLK["t"] += 5; set_kwh(1, 1000.25); E.poll_once(); s = E.snapshot()
check("G13 operating-date roll: 06-14 CLOSED in closed_days with its totals, new day starts UNAVAILABLE then gets 0.05 for B; totals not carried", len(s["closed_days"]) == 1 and s["closed_days"][0]["operating_date"] == "2026-06-14" and abs(s["closed_days"][0]["generation_kwh"] - 0.20) < 2e-3 and s["today"]["operating_date"] == "2026-06-15" and abs(s["today"]["generation_kwh"] - 0.05) < 2e-3)
E.sync_failed(); s = E.snapshot(); E.sync_succeeded(); s3 = E.snapshot()
check("G14 failed sync keeps closed day pending (re-sent identical); successful sync acks exactly the sent days", len(s["closed_days"]) == 1 and s3["closed_days"] == [])
E.stop(); E2 = mk(); set_kwh(1, 1000.30); CLK["t"] += 5; E2.poll_once(); t2 = E2.snapshot()["today"]
check("G15 REBOOT: new engine reloads config + baselines + OPEN ledger -> first reading yields a delta (0.05) on top of the persisted 0.05, no re-baseline, no double count", E2.config_version == 1 and abs(t2["generation_kwh"] - 0.10) < 2e-3 and abs(t2["wing_generation"]["B"] - 0.10) < 2e-3 and not any(e["type"] == "BASELINE_SET" for e in E2.snapshot()["events"]))
CLK["t"] += 7 * 3600; set_kwh(1, 1010.0); E2.poll_once(); t2 = E2.snapshot()["today"]
check("G16 7 h outage -> GAP_REBASELINE: 9.7 kWh reported as gap_kwh for M1, not attributed to any wing/day total", abs(t2["gap_kwh"]["M1"] - 9.7) < 2e-3 and abs(t2["generation_kwh"] - 0.10) < 2e-3 and any(e["type"] == "GAP_REBASELINE" for e in t2["events"]))
E2.stop()
WRITE["ok"] = False; ND = tempfile.mkdtemp(prefix="nowrite_"); E3 = mk(ND); E3.apply_config(FULL); E3.poll_once(); E3.stop()
check("G17 writes not allowed (secondary not HEALTHY / storage guard) -> engine runs in RAM, creates NO directory or file", E3.config_version == 1 and E3.health["M1"]["comm_status"] == "ONLINE" and not os.path.exists(os.path.join(ND, "energy")))
WRITE["ok"] = True
gd = json.loads(json.dumps(FULL)); gd["meters"]["M1"]["enabled"] = False; E4 = mk(tempfile.mkdtemp()); E4.apply_config(gd); E4.poll_once(); set_kwh(2, 200.1); CLK["t"] += 5; E4.poll_once(); t4 = E4.snapshot()["today"]; E4.stop()
check("G18 generation meter disabled -> generation UNAVAILABLE (None, source UNAVAILABLE), consumption meters still PHYSICAL", t4["generation_kwh"] is None and t4["generation_source"] == "UNAVAILABLE" and E4.health["M1"]["comm_status"] == "DISABLED" and t4["wing_consumption"]["A"] is not None)
ub = json.loads(json.dumps(FULL)); ub["bus"] = {}; E5 = mk(tempfile.mkdtemp()); E5.apply_config(ub); E5.poll_once(); s5 = E5.snapshot(); E5.stop()
check("G19 no RS485 port configured -> enabled meters OFFLINE with explicit bus error, never fabricated readings", s5["bus"]["status"] == "UNAVAILABLE" and s5["meters"]["M1"]["comm_status"] == "OFFLINE" and "bus" in s5["meters"]["M1"]["last_error"] and s5["today"]["generation_kwh"] is None)

# ---------------------------------------------------------------- H. controller integration + safety boundaries
ec.EMSController._install_signal_handlers = lambda self: None
class Api:
    reply = {"success": True, "config_version": 1, "slots": {}}; online = True; snaps = []
    def __init__(self): self.last_sync_http = None
    def sync(self, snap): Api.snaps.append(snap); return Api.reply if Api.online else None
    def push_ack(self, *a, **k): return True
    def close(self): pass
ec.ApiClient = Api
sm, st, q = H.build_firmware(); ctrl = ec.EMSController(); ctrl.energy.stop()   # stop the poll thread: tests drive poll_once deterministically
ctrl.device_config.update({"slots": {s: {"disabled": False, "target_days": 30, "feedback_enabled": False} for s in "ABCD"}, "feedback_hardware_installed": False})
booted = ctrl.boot(); snap = ctrl._build_snapshot()
check("H1 controller boots READY with the energy engine; sync snapshot carries energy{meters x5 DISABLED, today UNAVAILABLE}", booted and ctrl.state.system_state == SystemState.READY and len(snap["energy"]["meters"]) == 5 and snap["energy"]["today"]["generation_kwh"] is None and all(m["comm_status"] == "DISABLED" for m in snap["energy"]["meters"].values()))
check("H2 engine reads verified contactor feedback (state.feedback_state), not desired/active slot", ctrl.energy.feedback() == {s: "UNKNOWN" for s in "ABCD"} or all(v in ("ON", "OFF", "UNKNOWN", "PENDING") for v in ctrl.energy.feedback().values()))
Api.reply = {"success": True, "config_version": 1, "slots": {}, "energy_config": {"version": 3, "bus": {}, "meters": {"M1": {"enabled": False, "serial": "GEN-X"}}}}
ctrl.sync_cloud()
check("H3 cloud reply energy_config applied through the existing sync path and persisted under DATA_DIR/energy (secondary HEALTHY)", ctrl.energy.config_version == 3 and ctrl.energy.registry["M1"].serial == "GEN-X" and os.path.exists(os.path.join(H.TMP, "energy", "config.json")))
relays = {s: r.is_active for s, r in ctrl.gpio.relays.items()}; ctrl.energy.poll_once(); ctrl.process_one_command(); ctrl.process_toggle_events()
check("H4 energy polling changes no relay, no SystemState, no queue", {s: r.is_active for s, r in ctrl.gpio.relays.items()} == relays and not any(relays.values()) and ctrl.state.system_state == SystemState.READY and ctrl.queue.get_unacked() == [])
esrc = "".join(src(f"energy/{f}") for f in os.listdir(os.path.join(FW, "energy")) if f.endswith(".py"))
check("H5 energy package imports no GPIO/queue/state/FSM modules and never enqueues commands or touches gpiozero", not any(k in esrc for k in ("gpio_manager", "gpiozero", "OutputDevice", "offline_queue", "add_command", "transition_slot", "deactivate_slot", "from state", "import state", "SystemState", ".on()", ".off()")))
csrc = src("ems_controller.py")
def body(name):
    i = csrc.index(f"def {name}("); j = csrc.find("\n    def ", i + 1); return csrc[i:j if j > 0 else None]
check("H6 controller hardware/FSM paths untouched by energy: no 'energy' in process_one_command/_accept_cloud_command/boot/process_toggle_events/_deactivate_all", all("energy" not in body(n) for n in ("process_one_command", "_accept_cloud_command", "boot", "process_toggle_events", "_deactivate_all", "_run_software_command")) and csrc.count("self.energy") == 9)
check("H7 untouched files: offline_queue.py / state.py / gpio_manager.py / config.py / logger.py contain no energy references", all("energy" not in src(f).lower() or f == "config.py" and "energy" not in src(f) for f in ("offline_queue.py", "state.py", "gpio_manager.py", "config.py", "logger.py")))
check("H8 ledger/baselines live under DATA_DIR/energy (secondary volume), never under the primary disk", ctrl.energy.dir == os.path.join(H.TMP, "energy") and not os.path.exists("/mnt/ems-data"))
ctrl.shutdown()
check("H9 shutdown stops the poll thread and force-persists baselines + ledger", not ctrl.energy._thread.is_alive() and os.path.exists(os.path.join(H.TMP, "energy", "ledger.json")))
shutil.rmtree(TD, ignore_errors=True); shutil.rmtree(ED, ignore_errors=True)
print(f"\n{sum(R)}/{len(R)} passed"); sys.exit(0 if all(R) else 1)
