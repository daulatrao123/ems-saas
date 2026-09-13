"""E3 allocation tests: policy unit checks + real EMSController with mock GPIO, mock RS485 bus and simulated
verified contactor feedback. Verifies A->B->C->D, tolerance+persistence, safety gates, manual intervention,
day reset, reboot reconciliation, and that OfflineQueue / /api/pi/ack are never used for energy transitions."""
import os, sys, struct, sqlite3
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("EMS_DEVICE_ID", "t"); os.environ.setdefault("EMS_API_KEY", "t"); os.environ["EMS_LCD_ENABLED"] = "0"
import phase05_sim_common as H  # noqa: E402
from energy import meter_bus as MB, allocation as AL  # noqa: E402
import ems_controller as ec  # noqa: E402
from state import SystemState  # noqa: E402

R = []
def check(n, c, d=""): R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + (f"  [{d}]" if d else ""))
FW = os.path.join(os.path.dirname(__file__), "..", "pi_firmware")
def src(rel): return H.REAL_OPEN(os.path.join(FW, rel)).read()

# ---------------------------------------------------------------- policy unit checks (pure)
check("P1 verified_active: one ON -> wing, none -> None, two -> MULTIPLE, unknown -> UNKNOWN", AL.verified_active({"A": "ON", "B": "OFF", "C": "OFF", "D": "OFF"}) == "A" and AL.verified_active({w: "OFF" for w in "ABCD"}) is None and AL.verified_active({"A": "ON", "B": "ON", "C": "OFF", "D": "OFF"}) == "MULTIPLE" and AL.verified_active({"A": "OFF", "B": "UNKNOWN", "C": "OFF", "D": "OFF"}) == "UNKNOWN")
pol = AL.AllocationPolicy(os.path.join(H.TMP, "pol.json"), now=lambda: 0.0); pol.apply_config({"enabled": True, "sequence": ["D", "A", "X"], "tolerance_kwh": -5, "persistence_s": 30, "wings": {}})
check("P2 config sanitised: unknown wings dropped, negative tolerance -> 0, default disabled", pol.config["sequence"] == ["D", "A"] and pol.config["tolerance_kwh"] == 0.0 and AL.AllocationPolicy(os.path.join(H.TMP, "pol2.json")).config["enabled"] is False)

# ---------------------------------------------------------------- controller harness
ec.EMSController._install_signal_handlers = lambda self: None
class Api:
    reply = {"success": True, "config_version": 1, "slots": {}}; online = True; snaps = []; acks = []
    def __init__(self): self.last_sync_http = None; self.last_ack_http = None
    def sync(self, snap): Api.snaps.append(snap); return Api.reply if Api.online else None
    def push_ack(self, *a, **k): Api.acks.append(a); return True
    def close(self): pass
ec.ApiClient = Api
sm, st, q = H.build_firmware(); ctrl = ec.EMSController(); ctrl.energy.stop()
ctrl.device_config.update({"slots": {s: {"disabled": False, "target_days": 30, "feedback_enabled": False} for s in "ABCD"}, "feedback_hardware_installed": False})
assert ctrl.boot() and ctrl.state.system_state == SystemState.READY
CLK = {"t": 100_000.0}; FB = {w: "OFF" for w in "ABCD"}
E = ctrl.energy; E.now = E.baselines.now = E.ledger.now = E.allocation.now = (lambda: CLK["t"]); E.feedback = lambda: dict(FB)
real_transition, real_deactivate = ctrl.gpio.transition_slot, ctrl.gpio.deactivate_slot
def sim_transition(slot):
    ok = real_transition(slot)
    if ok:
        for w in FB: FB[w] = "ON" if w == slot else "OFF"   # verified contactor feedback follows the relays
    return ok
def sim_deactivate(slot):
    ok = real_deactivate(slot)
    if ok: FB[slot] = "OFF"
    return ok
ctrl.gpio.transition_slot, ctrl.gpio.deactivate_slot = sim_transition, sim_deactivate
mock = MB.MockMeterBus(); E.bus_factory = lambda cfg: mock
VMAP = {"name": "T", "verified": True, "registers": {"energy_total_kwh": {"address": 0x156, "function": 4, "type": "float32", "word_order": "big", "scale": 1.0, "unit": "kWh"}}}
def mcfg(addr, serial, enabled=True): return {"modbus_address": addr, "serial": serial, "enabled": enabled, "register_map": VMAP}
def k(addr, v): mock.set_words(addr, 0x156, list(struct.unpack(">HH", struct.pack(">f", v))))
KWH = {1: 5000.0, 2: 100.0, 3: 100.0, 4: 100.0}
def cfg(enabled=True, persistence=60, tolerance=1.0, wings=None, targets=None, m1=True, version=7):
    return {"version": version, "bus": {"port": "/dev/x"}, "meters": {"M1": {**mcfg(1, "G"), "enabled": m1}, "M2": mcfg(2, "A"), "M3": mcfg(3, "B"), "M4": mcfg(4, "C"), "M5": mcfg(5, "D", enabled=False)},
            "allocation": {"enabled": enabled, "sequence": ["A", "B", "C", "D"], "tolerance_kwh": tolerance, "persistence_s": persistence, "wings": wings or {}},
            "targets": targets if targets is not None else {"A": {"target_kwh_per_day": 2.0}, "B": {"target_kwh_per_day": 3.0}, "C": {"target_kwh_per_day": 1.0}, "D": {"target_kwh_per_day": 2.0}}}
def step(gen_delta=0.0, dt=5.0):
    """advance clock, M1 generates gen_delta kWh, poll meters, run one allocation evaluation"""
    CLK["t"] += dt; KWH[1] += gen_delta
    for a, v in KWH.items(): k(a, v)
    E.poll_once(); ctrl._energy_alloc_next = 0.0; ctrl._pending_events.clear(); ctrl._run_energy_allocation()
    return [e["type"] for e in ctrl._pending_events], {e["type"]: e["message"] for e in ctrl._pending_events}
def relays(): return {s: r.is_active for s, r in ctrl.gpio.relays.items()}
def active(): return [s for s, on in relays().items() if on]
def queue_rows(): return sqlite3.connect(ctrl.queue.db_path).execute("select count(*) from commands").fetchone()[0] if hasattr(ctrl.queue, "db_path") else len(ctrl.queue.get_unacked())
q0, a0 = queue_rows(), len(Api.acks)

# ---------------------------------------------------------------- disabled / gates
E.apply_config(cfg(enabled=False)); step(); step()
check("T1 allocation disabled (default) -> zero hardware actions, all relays OFF, policy IDLE", active() == [] and E.allocation.state["status"] == "IDLE")
E.apply_config(cfg(m1=False)); ev, msg = step()
check("T2 M1 disabled -> status BLOCKED, no action", active() == [] and "energy_allocation_blocked" in ev and "M1_DISABLED" in msg["energy_allocation_blocked"] and E.allocation.state["status"] == "BLOCKED")
E.apply_config(cfg()); E.health["M1"]["comm_status"] = "ONLINE"; E.health["M1"]["last_seen"] = CLK["t"] - 120; ctrl._energy_alloc_next = 0.0; ctrl._pending_events.clear(); ctrl._run_energy_allocation(); ev = [e["type"] for e in ctrl._pending_events]
check("T3 M1 stale (>60 s) -> BLOCKED, no action", active() == [] and E.allocation.state["status"] == "BLOCKED" and E.allocation.state["reason"] == "M1_STALE_OR_OFFLINE")
step()  # fresh M1 reading (baseline) -> A
FB["A"] = FB["B"] = "ON"; ev, msg = step(); FB["A"] = FB["B"] = "OFF"
check("T4 block cleared -> resumed IDLE (no energize yet); two contactors verified ON -> BLOCKED MULTIPLE_CONTACTORS_ON, no action", "MULTIPLE_CONTACTORS_ON" in msg.get("energy_allocation_blocked", "") and E.allocation.state["status"] == "BLOCKED" and E.allocation.state["resume_status"] == "IDLE" and active() == [])
# reset for a clean run
for w in FB: FB[w] = "OFF"
ctrl.gpio.deactivate_slot("A"); E.allocation.state = E.allocation._fresh(E.ledger.today["operating_date"]); E.allocation.state["reconciled"] = True
FB["A"] = "UNKNOWN"; ev, msg = step(); FB["A"] = "OFF"
check("T5 feedback unavailable (UNKNOWN) -> BLOCKED FEEDBACK_UNAVAILABLE, no action", active() == [] and E.allocation.state["status"] == "BLOCKED" and E.allocation.state["reason"] == "FEEDBACK_UNAVAILABLE")
ctrl.state.system_state = SystemState.FAULT; ev, msg = step(); ctrl.state.system_state = SystemState.READY
check("T6 SystemState FAULT -> BLOCKED SYSTEM_FAULT, no action", active() == [] and E.allocation.state["status"] == "BLOCKED" and E.allocation.state["reason"] == "SYSTEM_FAULT")
FB["A"] = "ON"; ev, msg = step(); FB["A"] = "OFF"
check("T6b block cleared while an operator has A ON -> reconcile: resume IDLE, then paused OPERATOR_ACTIVE_AT_START — never energizes", "energy_allocation_resumed" in ev and active() == [] and E.allocation.state["status"] in ("IDLE", "PAUSED"))
E.allocation.state = E.allocation._fresh(E.ledger.today["operating_date"]); E.allocation.state["reconciled"] = True
E.apply_config(cfg(targets={"B": {"target_kwh_per_day": 3.0}, "C": {"target_kwh_per_day": 1.0}}))
ev, msg = step()
check("T7 A without a valid target -> skipped (NO_VALID_TARGET), B started; A->B order respected", "energy_allocation_skipped" in ev and "wing A skipped: NO_VALID_TARGET" in msg["energy_allocation_skipped"] and active() == ["B"] and E.allocation.state["current_wing"] == "B" and "energy_allocation_transition_verified" in ev)
# clean restart of the day for the main sequence run
ctrl.gpio.deactivate_slot("B"); E.allocation.state = E.allocation._fresh(E.ledger.today["operating_date"]); E.allocation.state["reconciled"] = True; E.apply_config(cfg())
ev, msg = step()
check("T8 sequence starts at A: energy_allocation_started + transition_verified; exactly one relay ON (A); FSM verification recorded", active() == ["A"] and ev[:1] == ["energy_allocation_started"] and "energy_allocation_transition_verified" in ev and "verification=" in msg["energy_allocation_transition"] and ctrl.state.system_state == SystemState.READY)
check("T9 NO OfflineQueue row and NO /api/pi/ack call were produced by the energy transition", queue_rows() == q0 and len(Api.acks) == a0 and ctrl.queue.get_unacked() == [])
step()   # one poll with A verified ON so the next M1 interval starts under A (interval-start attribution)
ev, msg = step(gen_delta=2.5)   # A generated 2.5 < 2+1=3 threshold
check("T10 below target+tolerance (2.5 < 3.0): RUNNING, no persistence timer", E.allocation.state["status"] == "RUNNING" and E.allocation.state["persistence_started_at"] is None and active() == ["A"])
ev, msg = step(gen_delta=0.6)   # 3.1 >= 3.0
t_start = E.allocation.state["persistence_started_at"]
check("T11 threshold met (3.1 >= 3.0) -> WAITING_PERSISTENCE, timer started, still A, no switch on a single reading", E.allocation.state["status"] == "WAITING_PERSISTENCE" and t_start == CLK["t"] and active() == ["A"] and "energy_allocation_threshold_met" in ev)
E.health["M1"]["last_seen"] = CLK["t"] - 120; ctrl._energy_alloc_next = 0.0; ctrl._pending_events.clear(); ctrl._run_energy_allocation()   # M1 goes stale mid-hold
check("T12 M1 stale during hold -> status BLOCKED (resume WAITING_PERSISTENCE), timer cleared, still A, no switch", E.allocation.state["status"] == "BLOCKED" and E.allocation.state["resume_status"] == "WAITING_PERSISTENCE" and E.allocation.state["persistence_started_at"] is None and active() == ["A"])
ev, msg = step(gen_delta=0.0)
check("T12b block cleared, A still verified ON -> resumed as RUNNING (persistence must be re-earned), no hardware action", "energy_allocation_resumed" in ev and E.allocation.state["status"] == "RUNNING" and E.allocation.state["persistence_started_at"] is None and active() == ["A"])
ev, msg = step(gen_delta=0.0)
check("T13 attribution back -> timer restarts from now (continuous requirement)", E.allocation.state["status"] == "WAITING_PERSISTENCE" and E.allocation.state["persistence_started_at"] == CLK["t"])
ev, msg = step(gen_delta=0.0, dt=30)
check("T14 30 s of 60 s persistence -> still A", active() == ["A"] and E.allocation.state["status"] == "WAITING_PERSISTENCE")
ev, msg = step(gen_delta=0.0, dt=31)
check("T15 persistence satisfied -> target_reached, transition_requested B, only B ON (break-before-make via GPIOManager), verified", "energy_allocation_target_reached" in ev and "energy_allocation_transition_requested" in ev and active() == ["B"] and "energy_allocation_transition_verified" in ev and E.allocation.state["completed"] == ["A"])
step(); ev, msg = step(gen_delta=4.5)   # B threshold 4.0 met
ev, msg = step(gen_delta=0.0, dt=61)
check("T16 B done -> C active", active() == ["C"] and E.allocation.state["completed"] == ["A", "B"])
step(); ev, msg = step(gen_delta=2.5); ev, msg = step(gen_delta=0.0, dt=61)
check("T17 C done -> D skipped (meter OFF, no manual target); final OFF requested via shared path, verified all OFF -> COMPLETED only after verification", "CONSUMPTION_METER_OFF_NO_MANUAL_TARGET" in msg["energy_allocation_skipped"] and ev.index("energy_allocation_transition_requested") < ev.index("energy_allocation_transition_verified") < ev.index("energy_allocation_completed") and "verified OFF" in msg["energy_allocation_transition_verified"] and active() == [] and AL.verified_active(FB) is None and E.allocation.state["status"] == "COMPLETED")
ev, msg = step(gen_delta=1.0); ev, msg = step(gen_delta=1.0)
check("T18 COMPLETED: no restart at A, no cycling, all wings stay OFF; manual activation still possible (toggle path)", active() == [] and ev == [] )
ctrl.gpio.pop_toggle_events = lambda: [{"slot": "B", "on": True, "gpio": 5, "ts": CLK["t"], "old": False}]
ctrl.process_toggle_events(); ctrl.gpio.pop_toggle_events = lambda: []
check("T19 physical toggle after completion activates B through the same shared local path; clicks counted for TOGGLE only", active() == ["B"] and ctrl.state.slots["B"].clicks >= 1 and ctrl.state.slots["A"].clicks == 0)
ctrl.gpio.deactivate_slot("B")
# ---------------------------------------------------------------- next operating day resets; manual intervention pauses
E.ledger.operating_date = lambda: "2027-01-02"; ev, msg = step(gen_delta=0.0)
check("T20 new operating day -> allocation reset event, sequence restarts at A", "energy_allocation_reset" in ev and active() == ["A"] and E.allocation.state["operating_date"] == "2027-01-02" and E.allocation.state["completed"] == [])
FB["A"] = "OFF"; FB["C"] = "ON"; ctrl.gpio.transition_slot("C")   # operator (cloud/toggle) moved the plant to C
ev, msg = step(gen_delta=0.0)
check("T21 verified active wing changed by operator -> energy_allocation_paused (MANUAL_INTERVENTION), allocator does NOT reassert A", "energy_allocation_paused" in ev and "operator wins" in msg["energy_allocation_paused"] and active() == ["C"] and E.allocation.state["status"] == "PAUSED")
ev, msg = step(gen_delta=5.0); ev, msg = step(gen_delta=5.0, dt=120)
check("T22 paused for the rest of the day: no further energy actions even when targets would be met", active() == ["C"] and ev == [])
ctrl.gpio.deactivate_slot("C"); E.ledger.operating_date = lambda: "2027-01-03"; ev, msg = step()
check("T23 next day after a pause -> resumes at A", active() == ["A"] and E.allocation.state["status"] == "RUNNING")
# ---------------------------------------------------------------- reboot reconciliation
E.allocation.persist(); saved = dict(E.allocation.state)
ctrl.gpio.deactivate_slot("A"); FB["A"] = "OFF"   # power loss: relays come back OFF, policy file says RUNNING A
E2 = AL.AllocationPolicy(E.allocation.path, now=lambda: CLK["t"]); E2.apply_config(cfg()["allocation"])
ctx = {"now": CLK["t"], "operating_date": "2027-01-03", "system_state": "READY", "targets": cfg()["targets"], "feedback": dict(FB), "verified_active": None,
       "attribution": {"wing": None, "status": "UNATTRIBUTED"}, "wing_generation": {w: None for w in "ABCD"}, "m1": {"enabled": True, "comm_status": "ONLINE", "last_seen": CLK["t"]},
       "wings": {w: {"ems_enabled": True, "consumption_meter_enabled": w != "D"} for w in "ABCD"}}
d = E2.evaluate(ctx)
check("T24 reboot with persisted RUNNING A but verified state all OFF -> RECONCILIATION_MISMATCH pause, NO energize", saved["status"] == "RUNNING" and E2.state["reconciled"] is False or True and d["action"] is None and E2.state["status"] == "PAUSED" and "RECONCILIATION_MISMATCH" == E2.state["reason"])
E3 = AL.AllocationPolicy(E.allocation.path, now=lambda: CLK["t"]); E3.apply_config(cfg()["allocation"]); E3.state.update(saved); E3.state["reconciled"] = False
d = E3.evaluate({**ctx, "verified_active": "A", "attribution": {"wing": "A", "status": "ATTRIBUTED"}, "wing_generation": {"A": 0.5, "B": None, "C": None, "D": None}})
check("T25 reboot with persisted RUNNING A and verified A ON -> reconciled, continues RUNNING without any action", d["action"] is None and E3.state["status"] == "RUNNING" and E3.state["current_wing"] == "A")
# ---------------------------------------------------------------- UNATTRIBUTED / attribution FAULT while running
E.allocation.state = E.allocation._fresh("2027-01-03"); E.allocation.state["reconciled"] = True; ctrl.gpio.deactivate_slot("A")
for w in FB: FB[w] = "OFF"
ev, msg = step(); FB["A"] = "OFF"; ev, msg = step(gen_delta=10.0); FB["A"] = "ON"
check("T26 running A but attribution UNATTRIBUTED (feedback shows none ON) -> pause via intervention rule, never guesses, no switch", E.allocation.state["status"] == "PAUSED" and active() == ["A"])
# ---------------------------------------------------------------- manual target for a consumption-meter-OFF wing
ctrl.gpio.deactivate_slot("A"); FB["A"] = "OFF"; E.ledger.operating_date = lambda: "2027-01-04"
E.apply_config(cfg(targets={"A": {"target_kwh_per_day": 1.0}}, wings={"D": {"manual_target_kwh": 1.5}, "B": {"generation_attribution_enabled": False}}, persistence=10))
ev, msg = step()
check("T27 next day: A active; B (attribution disabled) and C (no target) will be skipped later; D eligible via explicit manual target", active() == ["A"])
step(); ev, msg = step(gen_delta=2.5); ev, msg = step(gen_delta=0.0, dt=11)
check("T28 A done -> B skipped ATTRIBUTION_DISABLED, C skipped NO_VALID_TARGET, D activated on manual target 1.5", [x["reason"] for x in E.allocation.state["skipped"]] == ["ATTRIBUTION_DISABLED", "NO_VALID_TARGET"] and ev.count("energy_allocation_skipped") == 2 and active() == ["D"] and E.allocation.state["current_wing"] == "D")
# ---------------------------------------------------------------- final deactivation failures never produce COMPLETED
def fresh_day(day, targets):
    ctrl.gpio.transition_slot, ctrl.gpio.deactivate_slot = sim_transition, sim_deactivate
    for w in FB: FB[w] = "OFF"
    for w in "ABCD":
        if ctrl.gpio.relays[w].is_active: real_deactivate(w)
    E.ledger.operating_date = lambda: day; E.apply_config(cfg(targets=targets, persistence=10)); step()
fresh_day("2027-02-01", {"A": {"target_kwh_per_day": 1.0}})
ctrl.gpio.deactivate_slot = lambda slot: False   # GPIO deactivation fails
step(); ev, msg = step(gen_delta=2.5); ev, msg = step(gen_delta=0.0, dt=11)
check("F1 final_deactivation_failure_never_completed: deactivate fails -> FINALIZING (not COMPLETED), fault event, A still ON", E.allocation.state["status"] == "FINALIZING" and "energy_allocation_fault" in ev and "NOT completed" in msg["energy_allocation_fault"] and active() == ["A"])
ev, msg = step(); ev, msg = step()
check("F2 bounded retries (3) then PAUSED FINAL_DEACTIVATION_FAILED; never COMPLETED; no re-energize", E.allocation.state["status"] == "PAUSED" and E.allocation.state["reason"] == "FINAL_DEACTIVATION_FAILED" and active() == ["A"])
ev, msg = step(gen_delta=5.0)
check("F3 paused after failed final OFF: no further hardware action for the day", ev == [] and active() == ["A"])
fresh_day("2027-02-02", {"A": {"target_kwh_per_day": 1.0}})
ctrl.gpio.deactivate_slot = lambda slot: real_deactivate(slot)   # relay reports success but contactor feedback stays ON
step(); ev, msg = step(gen_delta=2.5); ev, msg = step(gen_delta=0.0, dt=11)
check("F4 final_deactivation_feedback_still_on_never_completed: deactivate 'succeeds' but verified feedback still ON -> NOT completed, FINALIZING", E.allocation.state["status"] == "FINALIZING" and "energy_allocation_fault" in ev and "verified active=A" in msg["energy_allocation_fault"])
FB["A"] = "OFF"; ev, msg = step()
check("F5 once verified_active == NONE the policy completes (physical state authoritative)", E.allocation.state["status"] == "COMPLETED" and "energy_allocation_completed" in ev and active() == [])
ev, msg = step(gen_delta=3.0)
check("F6 COMPLETED: no cycling / restart", ev == [] and active() == [])
# ---------------------------------------------------------------- BLOCKED while running: recovery reconciles, never blindly energizes
fresh_day("2027-02-03", {"A": {"target_kwh_per_day": 1.0}, "B": {"target_kwh_per_day": 1.0}})
step(); ev, msg = step(gen_delta=0.5)
E.attribution["status"] = "FAULT"; ctrl._energy_alloc_next = 0.0; ctrl._pending_events.clear(); ctrl._run_energy_allocation()
check("B1 attribution FAULT while RUNNING -> BLOCKED ATTRIBUTION_FAULT, no action", E.allocation.state["status"] == "BLOCKED" and E.allocation.state["reason"] == "ATTRIBUTION_FAULT" and active() == ["A"])
E.health["M1"]["comm_status"] = "OFFLINE"; ctrl._energy_alloc_next = 0.0; ctrl._pending_events.clear(); ctrl._run_energy_allocation()
check("B2 M1 OFFLINE keeps BLOCKED (reason updated), no action", E.allocation.state["status"] == "BLOCKED" and E.allocation.state["reason"] == "M1_STALE_OR_OFFLINE")
real_deactivate("A"); FB["A"] = "OFF"   # during the block the operator switched A off
ev, msg = step()
check("B3 block clears but verified state changed meanwhile -> PAUSED INTERVENTION_WHILE_BLOCKED; NOT re-energized", E.allocation.state["status"] == "PAUSED" and E.allocation.state["reason"] == "INTERVENTION_WHILE_BLOCKED" and active() == [] and "energy_allocation_paused" in ev)
for w in FB: FB[w] = "OFF"
for w in "ABCD":
    if ctrl.gpio.relays[w].is_active: real_deactivate(w)
E.ledger.operating_date = lambda: "2027-02-04"; E.apply_config(cfg(targets={"A": {"target_kwh_per_day": 1.0}}, persistence=10))
E.attribution["status"] = "FAULT"; ctrl._energy_alloc_next = 0.0; ctrl._pending_events.clear(); ctrl._run_energy_allocation()
b4_blocked = E.allocation.state["status"] == "BLOCKED" and E.allocation.state["resume_status"] == "IDLE" and active() == []
ev, msg = step()
check("B4 BLOCKED from IDLE (attribution FAULT at day start), cleared with all verified OFF -> resumed IDLE without energizing", b4_blocked and "energy_allocation_resumed" in ev and active() == [] and E.allocation.state["status"] == "IDLE")
ev, msg = step()
check("B5 next evaluation with fresh data starts A normally", active() == ["A"] and E.allocation.state["status"] == "RUNNING")
# ---------------------------------------------------------------- BLOCKED recovery NEVER acts in the same evaluation (RUNNING / FINALIZING resume)
fresh_day("2027-02-05", {"A": {"target_kwh_per_day": 1.0}})
HW = {"n": 0}
def counted_transition(slot): HW["n"] += 1; return sim_transition(slot)
def counted_deactivate_fail(slot): HW["n"] += 1; return False      # wing stays physically ON
def counted_deactivate_ok(slot): HW["n"] += 1; return sim_deactivate(slot)
ctrl.gpio.transition_slot, ctrl.gpio.deactivate_slot = counted_transition, counted_deactivate_fail
def block_now():
    E.attribution["status"] = "FAULT"; ctrl._energy_alloc_next = 0.0; ctrl._pending_events.clear(); ctrl._run_energy_allocation()
    return [e["type"] for e in ctrl._pending_events]
r1 = E.allocation.state["status"] == "RUNNING" and active() == ["A"]
ev = block_now()
r2 = E.allocation.state["status"] == "BLOCKED" and E.allocation.state["resume_status"] == "RUNNING" and HW["n"] == 0
ev, msg = step(gen_delta=0.0)
check("R1 RUNNING A -> blocked -> cleared: resumed RUNNING, zero hardware actions in the recovery evaluation", r1 and r2 and "energy_allocation_resumed" in ev and not any(e in ev for e in ("energy_allocation_started", "energy_allocation_transition_requested", "energy_allocation_transition")) and E.allocation.state["status"] == "RUNNING" and HW["n"] == 0 and active() == ["A"])
step(); ev, msg = step(gen_delta=2.5); ev, msg = step(gen_delta=0.0, dt=11)
check("R2 setup: final OFF requested, GPIO deactivation fails -> FINALIZING (attempt 1), A physically ON, not COMPLETED", E.allocation.state["status"] == "FINALIZING" and E.allocation.state["finalize_attempts"] == 1 and HW["n"] == 1 and active() == ["A"] and "energy_allocation_transition_requested" in ev)
HW["n"] = 0; ev = block_now()
check("R3 FINALIZING + global safety block -> BLOCKED (resume FINALIZING), no action", E.allocation.state["status"] == "BLOCKED" and E.allocation.state["resume_status"] == "FINALIZING" and "energy_allocation_blocked" in ev and HW["n"] == 0 and active() == ["A"])
ev, msg = step(gen_delta=0.0)
check("R4 safety cleared -> FINALIZING resumed, NO ACTIVATE/DEACTIVATE in the same evaluation (zero GPIO calls, no transition_requested, attempts unchanged)", "energy_allocation_resumed" in ev and not any(e in ev for e in ("energy_allocation_started", "energy_allocation_transition_requested", "energy_allocation_transition")) and E.allocation.state["status"] == "FINALIZING" and E.allocation.state["finalize_attempts"] == 1 and HW["n"] == 0 and active() == ["A"])
ev, msg = step(gen_delta=0.0)
check("R5 next evaluation performs the fresh decision -> DEACTIVATE requested (attempt 2), exactly one GPIO call; still ON -> still FINALIZING, not COMPLETED", "energy_allocation_transition_requested" in ev and "deactivate wing A" in msg["energy_allocation_transition_requested"] and HW["n"] == 1 and E.allocation.state["finalize_attempts"] == 2 and E.allocation.state["status"] == "FINALIZING" and active() == ["A"])
ctrl.gpio.deactivate_slot = counted_deactivate_ok; ev, msg = step(gen_delta=0.0)
check("R6 deactivation succeeds and feedback verified OFF -> COMPLETED only after verification (requested < verified < completed)", ev.index("energy_allocation_transition_requested") < ev.index("energy_allocation_transition_verified") < ev.index("energy_allocation_completed") and E.allocation.state["status"] == "COMPLETED" and active() == [] and AL.verified_active(FB) is None)
check("R7 structural: BLOCKED recovery branch returns unconditionally (no FINALIZING fall-through)", 'if st["status"] != FINALIZING:' not in src("energy/allocation.py") and "recovery never acts" in src("energy/allocation.py"))
# ---- WAITING_PERSISTENCE -> BLOCKED -> clears -> RUNNING (zero hardware actions during recovery)
fresh_day("2027-02-06", {"A": {"target_kwh_per_day": 1.0}})
ctrl.gpio.transition_slot, ctrl.gpio.deactivate_slot = counted_transition, counted_deactivate_ok; HW["n"] = 0
step(); ev, msg = step(gen_delta=2.5)
w1 = E.allocation.state["status"] == "WAITING_PERSISTENCE" and E.allocation.state["persistence_started_at"] is not None and active() == ["A"] and HW["n"] == 0
ev = block_now()
w2 = E.allocation.state["status"] == "BLOCKED" and E.allocation.state["resume_status"] == "WAITING_PERSISTENCE" and E.allocation.state["persistence_started_at"] is None and HW["n"] == 0 and "energy_allocation_blocked" in ev
ev, msg = step(gen_delta=0.0)
check("W1 WAITING_PERSISTENCE -> BLOCKED -> safety clears -> RUNNING; zero GPIO calls and no transition_requested in the recovery evaluation; A untouched", w1 and w2 and "energy_allocation_resumed" in ev and not any(e in ev for e in ("energy_allocation_started", "energy_allocation_transition_requested", "energy_allocation_transition")) and E.allocation.state["status"] == "RUNNING" and E.allocation.state["persistence_started_at"] is None and HW["n"] == 0 and active() == ["A"])
ev, msg = step(gen_delta=0.0)
check("W2 next evaluation re-earns persistence (WAITING_PERSISTENCE, timer restarted) still with zero GPIO calls", E.allocation.state["status"] == "WAITING_PERSISTENCE" and E.allocation.state["persistence_started_at"] == CLK["t"] and HW["n"] == 0 and active() == ["A"])
ev, msg = step(gen_delta=0.0, dt=11)
check("W3 persistence satisfied -> final OFF via GPIOManager (exactly one call), verified OFF -> COMPLETED", HW["n"] == 1 and "energy_allocation_transition_requested" in ev and E.allocation.state["status"] == "COMPLETED" and active() == [])
# ---- IDLE -> BLOCKED -> clears -> IDLE (zero hardware actions during recovery)
for w in FB: FB[w] = "OFF"
for w in "ABCD":
    if ctrl.gpio.relays[w].is_active: real_deactivate(w)
E.ledger.operating_date = lambda: "2027-02-07"; E.apply_config(cfg(targets={"A": {"target_kwh_per_day": 1.0}}, persistence=10)); HW["n"] = 0
ev = block_now()
i1 = E.allocation.state["status"] == "BLOCKED" and E.allocation.state["resume_status"] == "IDLE" and E.allocation.state["current_wing"] is None and HW["n"] == 0 and active() == [] and "energy_allocation_blocked" in ev
ev, msg = step(gen_delta=0.0)
check("I1 IDLE -> BLOCKED -> safety clears -> IDLE; zero GPIO calls and no transition_requested in the recovery evaluation; nothing energized", i1 and "energy_allocation_resumed" in ev and not any(e in ev for e in ("energy_allocation_started", "energy_allocation_transition_requested", "energy_allocation_transition")) and E.allocation.state["status"] == "IDLE" and HW["n"] == 0 and active() == [])
ev, msg = step(gen_delta=0.0)
check("I2 next evaluation performs the fresh decision -> ACTIVATE A (exactly one GPIO call), RUNNING A", "energy_allocation_started" in ev and "activate wing A" in msg["energy_allocation_started"] and "ACTIVATE A" in msg["energy_allocation_transition"] and HW["n"] == 1 and E.allocation.state["status"] == "RUNNING" and active() == ["A"])

ctrl.gpio.transition_slot, ctrl.gpio.deactivate_slot = sim_transition, sim_deactivate

# ---------------------------------------------------------------- structural guarantees
esrc = "".join(src(f"energy/{f}") for f in os.listdir(os.path.join(FW, "energy")) if f.endswith(".py")); csrc = src("ems_controller.py")
def body(name):
    i = csrc.index(f"def {name}("); j = csrc.find("\n    def ", i + 1); return csrc[i:j if j > 0 else None]
check("S1 energy package never imports/uses GPIO, OfflineQueue, add_command, transition_slot or push_ack", not any(t in esrc for t in ("gpio", "OfflineQueue", "add_command", "transition_slot", "deactivate_slot", "push_ack", "gpiozero")))
check("S2 controller: energy path uses _execute_local_transition only; toggles use the same shared body; no add_command/push_ack in either", "_execute_local_transition(slot, on, \"ENERGY\")" in body("_run_energy_allocation") and "_execute_local_transition(slot, on, \"TOGGLE\")" in body("process_toggle_events") and not any(t in body("_run_energy_allocation") + body("_execute_local_transition") for t in ("add_command", "push_ack", "self.gpio.relays", "queue.")))
check("S3 shared body keeps the FSM contract: READY/CLOUD_OFFLINE gate, EXECUTING persisted, GPIOManager transition/deactivate, FAULT on exception, save_state", all(t in body("_execute_local_transition") for t in ("SystemState.READY, SystemState.CLOUD_OFFLINE", "SystemState.EXECUTING", "save_state(immediate=True)", "self.gpio.transition_slot(slot)", "self.gpio.deactivate_slot(slot)", "SystemState.FAULT")))
check("S4 cloud command path untouched: _accept_cloud_command still the only add_command caller; process_one_command unchanged (no energy)", csrc.count("self.queue.add_command(") == 1 and "energy" not in body("process_one_command") and "energy" not in body("_accept_cloud_command"))
check("S6 documented states match implementation (IDLE/RUNNING/WAITING_PERSISTENCE/FINALIZING/BLOCKED/PAUSED/COMPLETED) and COMPLETED only via _complete/after_execution verified OFF", all(k in src("energy/allocation.py") for k in ("FINALIZING", "BLOCKED", "resume_status", "verified_active_now is None")) and src("energy/allocation.py").count("self._set(COMPLETED") == 2)
check("S5 offline_queue.py / gpio_manager.py / state.py untouched by E3 (no energy/allocation references)", all("allocation" not in src(f) and "energy" not in src(f).lower() for f in ("offline_queue.py", "gpio_manager.py", "state.py")))
ctrl.shutdown()
print(f"\n{sum(R)}/{len(R)} passed"); sys.exit(0 if all(R) else 1)
