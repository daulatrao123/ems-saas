"""P1 targeted supplement: strict energy-calculation-mode API + DB invariants.

No auth diagnostics, no user creation, no tenant creation. Uses existing accounts via env.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import requests
from dotenv import load_dotenv
from psycopg.rows import dict_row

sys.path.append("/app/backend")
from energy_api.ingest import build_energy_config  # noqa: E402


load_dotenv("/app/backend/.env", override=False)


def need(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


APP_URL = need("APP_URL").rstrip("/")
TEST_ORIGIN = need("TEST_ORIGIN")
TEST_SUPER_EMAIL = need("TEST_SUPER_EMAIL")
TEST_SUPER_PASSWORD = need("TEST_SUPER_PASSWORD")
TEST_ADMIN_EMAIL = need("TEST_ADMIN_EMAIL")
TEST_ADMIN_PASSWORD = need("TEST_ADMIN_PASSWORD")
TEST_MEMBER_EMAIL = need("TEST_MEMBER_EMAIL")
TEST_MEMBER_PASSWORD = need("TEST_MEMBER_PASSWORD")
DATABASE_URL = need("DATABASE_URL")

API = f"{APP_URL}/api"
RESULTS: list[bool] = []
CREATED_DEVICE_IDS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    ok = bool(condition)
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f" [{detail}]" if detail else ""))


def q(session: requests.Session, method: str, path: str, **kwargs):
    return session.request(method, f"{API}{path}", timeout=40, **kwargs)


def login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(
        f"{API}/auth/login",
        json={"email": email, "password": password},
        headers={"Origin": TEST_ORIGIN, "Referer": f"{TEST_ORIGIN}/login"},
        timeout=40,
    )
    if r.status_code != 200:
        raise RuntimeError(f"login failed for {email}: {r.status_code} {r.text[:220]}")
    csrf = s.cookies.get("ems_csrf")
    if not csrf:
        raise RuntimeError("missing csrf cookie")
    s.headers.update({"Origin": TEST_ORIGIN, "Referer": f"{TEST_ORIGIN}/", "X-CSRF-Token": csrf})
    return s


def db_fetchall(sql: str, params: tuple = ()):
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def db_one(sql: str, params: tuple = ()):
    rows = db_fetchall(sql, params)
    return rows[0] if rows else None


def delete_created_devices(device_ids: list[str]) -> None:
    if not device_ids:
        return
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pi_devices WHERE id = ANY(%s)", (device_ids,))
        conn.commit()


def snapshot_nonvacuous(snap: dict) -> bool:
    return (
        bool(snap.get("energy_daily"))
        and bool(snap.get("energy_meters"))
        and bool(snap.get("slot_configs"))
        and bool(snap.get("pi_state"))
        and snap.get("build_energy_config") is not None
    )


def set_ledger_day(device_id: str, operating_date: str, generation_kwh, generation_source: str, wing_a_generation):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO energy_daily (
                    device_id, operating_date, reset_period, status, generation_kwh, generation_source,
                    wing_generation, unattributed_generation_kwh, fault_generation_kwh, wing_consumption,
                    consumption_source, samples, attempts, gap_kwh, events, updated_at
                ) VALUES (
                    %s,%s,%s,'OPEN',%s,%s,%s::jsonb,0,0,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,NOW()
                )
                ON CONFLICT (device_id, operating_date) DO UPDATE SET
                    generation_kwh=EXCLUDED.generation_kwh,
                    generation_source=EXCLUDED.generation_source,
                    wing_generation=EXCLUDED.wing_generation,
                    wing_consumption=EXCLUDED.wing_consumption,
                    consumption_source=EXCLUDED.consumption_source,
                    samples=EXCLUDED.samples,
                    attempts=EXCLUDED.attempts,
                    gap_kwh=EXCLUDED.gap_kwh,
                    events=EXCLUDED.events,
                    updated_at=NOW()
                """,
                (
                    device_id,
                    operating_date,
                    operating_date[:7],
                    generation_kwh,
                    generation_source,
                    json.dumps({"A": wing_a_generation, "B": None, "C": None, "D": None}),
                    json.dumps({"A": 1.0, "B": None, "C": None, "D": None}),
                    json.dumps({"A": "PHYSICAL", "B": "UNAVAILABLE", "C": "UNAVAILABLE", "D": "UNAVAILABLE"}),
                    json.dumps({"M1": 1}),
                    json.dumps({"M1": 1}),
                    json.dumps({}),
                    json.dumps([]),
                ),
            )
        conn.commit()


def seed_slot_and_state_rows(device_id: str) -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for slot in ("A", "B", "C", "D"):
                cur.execute(
                    """
                    INSERT INTO slot_configs (device_id, slot, disabled, feedback_enabled)
                    VALUES (%s, %s, FALSE, FALSE)
                    ON CONFLICT (device_id, slot) DO NOTHING
                    """,
                    (device_id, slot),
                )
            cur.execute(
                """
                INSERT INTO pi_state (device_id, config_state, config_version)
                VALUES (%s, 'DESIRED', 0)
                ON CONFLICT (device_id) DO NOTHING
                """,
                (device_id,),
            )
        conn.commit()


def get_graph_row(admin: requests.Session, sid: int, device_id: str, day: str):
    r = q(admin, "GET", f"/energy/graph/wing?society_id={sid}&device_id={device_id}&wing=A&from={day}&to={day}&basis=calculation")
    rows = r.json().get("rows", []) if r.status_code == 200 else []
    return r, rows[0] if rows else {}


def snapshot_for_mode_put(device_id: str):
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT energy_config_version, energy_bus, energy_allocation FROM pi_devices WHERE id=%s",
                (device_id,),
            )
            pi_device = cur.fetchone()
            cur.execute("SELECT * FROM energy_daily WHERE device_id=%s ORDER BY operating_date, received_at, updated_at", (device_id,))
            daily = cur.fetchall()
            cur.execute("SELECT * FROM energy_meters WHERE device_id=%s ORDER BY meter_id", (device_id,))
            meters = cur.fetchall()
            cur.execute("SELECT * FROM pi_commands WHERE device_id=%s ORDER BY id", (device_id,))
            commands = cur.fetchall()
            cur.execute("SELECT * FROM slot_configs WHERE device_id=%s ORDER BY slot", (device_id,))
            slots = cur.fetchall()
            cur.execute("SELECT * FROM pi_state WHERE device_id=%s", (device_id,))
            state = cur.fetchall()
            cfg = build_energy_config(cur, device_id)
    return {
        "pi_device": pi_device,
        "energy_daily": daily,
        "energy_meters": meters,
        "pi_commands": commands,
        "slot_configs": slots,
        "pi_state": state,
        "build_energy_config": cfg,
    }


def current_mode_version(admin: requests.Session, sid: int, device_id: str):
    s = q(admin, "GET", f"/energy/summary?society_id={sid}&device_id={device_id}")
    j = s.json() if s.status_code == 200 else {}
    calc = j.get("calculation", {})
    return s, calc.get("mode"), calc.get("version")


def main() -> int:
    created = CREATED_DEVICE_IDS
    fixture: dict = {}

    try:
        super_admin = login(TEST_SUPER_EMAIL, TEST_SUPER_PASSWORD)
        admin = login(TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD)
        member = login(TEST_MEMBER_EMAIL, TEST_MEMBER_PASSWORD)
    except Exception as exc:
        check("0 logins via env credentials", False, str(exc))
        print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
        return 1

    me = q(admin, "GET", "/auth/me")
    sid = int(me.json().get("society_id")) if me.status_code == 200 and me.json().get("society_id") is not None else None
    check("1 derive tenant from existing society_admin session", me.status_code == 200 and sid is not None)
    if sid is None:
        print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
        return 1

    tag = uuid.uuid4().hex[:6]
    d1 = q(admin, "POST", "/admin/devices/register", json={"name": f"iter22-main-{tag}"})
    d2 = q(admin, "POST", "/admin/devices/register", json={"name": f"iter22-alt-{tag}"})

    dev_a = d1.json().get("device_id") if d1.status_code == 200 else None
    dev_b = d2.json().get("device_id") if d2.status_code == 200 else None
    if dev_a and dev_a not in created:
        created.append(dev_a)
    if dev_b and dev_b not in created:
        created.append(dev_b)

    ok_devices = d1.status_code == 200 and d2.status_code == 200
    check("2 register two isolated devices in existing tenant", ok_devices)
    if not ok_devices:
        print(d1.status_code, d1.text[:200], d2.status_code, d2.text[:200])
        print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
        return 1

    key_a = d1.json()["api_key"]
    fixture = {
        "society_id": sid,
        "device_main": dev_a,
        "device_alt": dev_b,
        "admin_email": TEST_ADMIN_EMAIL,
        "member_email": TEST_MEMBER_EMAIL,
        "note": "SIMULATED fixtures; no user creation",
    }

    vmap = {
        "name": "ITER22",
        "verified": True,
        "registers": {"energy_total_kwh": {"address": 342, "function": 4, "type": "float32", "word_order": "big", "scale": 1.0, "unit": "kWh"}},
    }
    bus = q(admin, "PUT", "/energy/bus", json={"society_id": sid, "device_id": dev_a, "port": "/dev/serial/by-id/usb-rs485", "serial": {"baudrate": 9600, "parity": "N"}})
    m1 = q(admin, "PUT", "/energy/meters/M1", json={"society_id": sid, "device_id": dev_a, "enabled": True, "serial": f"IT22M1-{tag}", "modbus_address": 1, "register_map": vmap})
    m2 = q(admin, "PUT", "/energy/meters/M2", json={"society_id": sid, "device_id": dev_a, "enabled": True, "serial": f"IT22M2-{tag}", "modbus_address": 2, "register_map": vmap})
    check("3 configure M1 enabled for source-qualification checks", bus.status_code == 200 and m1.status_code == 200 and m2.status_code == 200)

    today = datetime.now(timezone.utc).date().isoformat()
    seed_slot_and_state_rows(dev_a)
    set_ledger_day(dev_a, today, 5.0, "PHYSICAL", 5.0)
    pre_snap = snapshot_for_mode_put(dev_a)
    check("3b seed nonempty ledger+meter+slot+state fixtures before snapshot", snapshot_nonvacuous(pre_snap))
    if not snapshot_nonvacuous(pre_snap):
        print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
        return 1

    # (1) Strict before/after row-content snapshots around only mode PUT.
    _, _, version_before = current_mode_version(admin, sid, dev_a)
    snap_before = pre_snap
    put_mode = q(admin, "PUT", "/energy/calculation-mode", json={"society_id": sid, "device_id": dev_a, "mode": "MANUAL", "expected_version": version_before})
    snap_after = snapshot_for_mode_put(dev_a)
    unchanged = (
        snap_before["pi_device"] == snap_after["pi_device"]
        and snap_before["energy_daily"] == snap_after["energy_daily"]
        and snap_before["energy_meters"] == snap_after["energy_meters"]
        and snap_before["pi_commands"] == snap_after["pi_commands"]
        and snap_before["slot_configs"] == snap_after["slot_configs"]
        and snap_before["pi_state"] == snap_after["pi_state"]
        and snap_before["build_energy_config"] == snap_after["build_energy_config"]
    )
    check("4 mode PUT changed only calculation fields (full row snapshots unchanged elsewhere)", put_mode.status_code == 200 and unchanged)

    # (3) invalid mode/version payloads + stale409 + no-op audit exact.
    _, mode_now, ver_now = current_mode_version(admin, sid, dev_a)
    audit_before = db_one("SELECT COUNT(*) AS n FROM audit_log WHERE action='ENERGY_CALCULATION_MODE' AND details->>'device_id'=%s", (dev_a,))["n"]
    bad_mode = q(admin, "PUT", "/energy/calculation-mode", json={"society_id": sid, "device_id": dev_a, "mode": "AUTOX", "expected_version": ver_now})
    bad_missing = q(admin, "PUT", "/energy/calculation-mode", json={"society_id": sid, "device_id": dev_a, "mode": "AUTO"})
    bad_neg = q(admin, "PUT", "/energy/calculation-mode", json={"society_id": sid, "device_id": dev_a, "mode": "AUTO", "expected_version": -1})
    bad_bool = q(admin, "PUT", "/energy/calculation-mode", json={"society_id": sid, "device_id": dev_a, "mode": "AUTO", "expected_version": True})
    bad_str = q(admin, "PUT", "/energy/calculation-mode", json={"society_id": sid, "device_id": dev_a, "mode": "AUTO", "expected_version": "1"})
    stale = q(admin, "PUT", "/energy/calculation-mode", json={"society_id": sid, "device_id": dev_a, "mode": "AUTO", "expected_version": ver_now - 1 if isinstance(ver_now, int) and ver_now > 0 else 0})
    noop = q(admin, "PUT", "/energy/calculation-mode", json={"society_id": sid, "device_id": dev_a, "mode": mode_now, "expected_version": ver_now})
    audit_after = db_one("SELECT COUNT(*) AS n FROM audit_log WHERE action='ENERGY_CALCULATION_MODE' AND details->>'device_id'=%s", (dev_a,))["n"]
    check(
        "5 invalid mode/version validation + stale409 + no-op audit exact",
        bad_mode.status_code == 400
        and bad_missing.status_code == 400
        and bad_neg.status_code == 400
        and bad_bool.status_code == 400
        and bad_str.status_code == 400
        and stale.status_code == 409
        and noop.status_code == 200
        and noop.json().get("version") == ver_now
        and audit_after == audit_before,
    )

    # (3) access control + independence + forged cross-tenant mismatch.
    get_super = q(super_admin, "GET", f"/energy/summary?society_id={sid}&device_id={dev_a}")
    get_member = q(member, "GET", f"/energy/summary?society_id={sid}&device_id={dev_a}")
    put_member = q(member, "PUT", "/energy/calculation-mode", json={"society_id": sid, "device_id": dev_a, "mode": "AUTO", "expected_version": ver_now})
    cross_dev_summary = q(admin, "GET", f"/energy/summary?society_id={sid}&device_id={dev_b}")
    version_b = cross_dev_summary.json().get("calculation", {}).get("version") if cross_dev_summary.status_code == 200 else None
    a_before_mode = q(admin, "GET", f"/energy/summary?society_id={sid}&device_id={dev_a}").json().get("calculation", {}).get("mode")
    a_before_version = q(admin, "GET", f"/energy/summary?society_id={sid}&device_id={dev_a}").json().get("calculation", {}).get("version")
    put_b = q(admin, "PUT", "/energy/calculation-mode", json={"society_id": sid, "device_id": dev_b, "mode": "MANUAL", "expected_version": version_b}) if isinstance(version_b, int) else None
    a_after = q(admin, "GET", f"/energy/summary?society_id={sid}&device_id={dev_a}")
    b_after = q(admin, "GET", f"/energy/summary?society_id={sid}&device_id={dev_b}")
    foreign = db_one("SELECT id FROM pi_devices WHERE society_id <> %s ORDER BY id LIMIT 1", (sid,))
    forged = q(admin, "GET", f"/energy/summary?society_id={sid}&device_id={foreign['id']}") if foreign else None
    check(
        "6 GET by superadmin/member allowed, member write blocked, cross-device independent, forged cross-tenant device 404",
        get_super.status_code == 200
        and get_member.status_code == 200
        and put_member.status_code == 403
        and put_b is not None
        and put_b.status_code == 200
        and a_after.status_code == 200
        and b_after.status_code == 200
        and a_after.json().get("calculation", {}).get("mode") == a_before_mode
        and a_after.json().get("calculation", {}).get("version") == a_before_version
        and b_after.json().get("calculation", {}).get("mode") == "MANUAL"
        and forged is not None
        and forged.status_code == 404,
    )

    # (2) AUTO source qualification with M1 enabled.
    s = q(admin, "GET", f"/energy/summary?society_id={sid}&device_id={dev_a}")
    ver = s.json().get("calculation", {}).get("version") if s.status_code == 200 else None
    if isinstance(ver, int):
        q(admin, "PUT", "/energy/calculation-mode", json={"society_id": sid, "device_id": dev_a, "mode": "AUTO", "expected_version": ver})

    set_ledger_day(dev_a, today, 8.0, "UNAVAILABLE", 8.0)
    _, wrong_source = get_graph_row(admin, sid, dev_a, today)
    set_ledger_day(dev_a, today, -2.0, "PHYSICAL", -2.0)
    _, negative_source = get_graph_row(admin, sid, dev_a, today)
    set_ledger_day(dev_a, today, None, "PHYSICAL", None)
    _, null_source = get_graph_row(admin, sid, dev_a, today)
    set_ledger_day(dev_a, today, 0.0, "PHYSICAL", 0.0)
    _, zero_source = get_graph_row(admin, sid, dev_a, today)
    check(
        "7 AUTO qualification with M1 enabled: wrong-source/negative/null hidden, zero accepted",
        wrong_source.get("generated_kwh") is None
        and wrong_source.get("generation_source") == "UNAVAILABLE"
        and negative_source.get("generated_kwh") is None
        and negative_source.get("generation_source") == "UNAVAILABLE"
        and null_source.get("generated_kwh") is None
        and null_source.get("generation_source") == "UNAVAILABLE"
        and zero_source.get("generated_kwh") == 0.0
        and zero_source.get("generation_source") == "PHYSICAL",
    )

    # (2) Manual zero-only day must be 0.00-equivalent numeric zero and distinct from null day.
    t2 = (datetime.now(timezone.utc).date() - timedelta(days=2)).isoformat()
    q(admin, "POST", "/energy/adjustments", json={"society_id": sid, "device_id": dev_a, "wing": "A", "operating_date": t2, "kind": "MANUAL_GENERATION", "value_kwh": 0.0, "reason": "zero only day"})
    s2 = q(admin, "GET", f"/energy/summary?society_id={sid}&device_id={dev_a}")
    v2 = s2.json().get("calculation", {}).get("version") if s2.status_code == 200 else None
    if isinstance(v2, int):
        q(admin, "PUT", "/energy/calculation-mode", json={"society_id": sid, "device_id": dev_a, "mode": "MANUAL", "expected_version": v2})
    g = q(admin, "GET", f"/energy/graph/wing?society_id={sid}&device_id={dev_a}&wing=A&from={t2}&to={today}&basis=calculation")
    rows = {r["date"]: r for r in g.json().get("rows", [])} if g.status_code == 200 else {}
    missing_day = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    check(
        "8 MANUAL zero-only day stays numeric 0.0, distinct from missing/null day",
        g.status_code == 200
        and rows.get(t2, {}).get("generated_kwh") == 0.0
        and rows.get(t2, {}).get("generation_source") == "MANUAL"
        and rows.get(missing_day, {}).get("generated_kwh") is None,
    )

    # (2) Manual entries are additive; they never overwrite physical ledger values.
    t3 = (datetime.now(timezone.utc).date() - timedelta(days=3)).isoformat()
    set_ledger_day(dev_a, t3, 10.0, "PHYSICAL", 10.0)
    add_pos = q(admin, "POST", "/energy/adjustments", json={"society_id": sid, "device_id": dev_a, "wing": "A", "operating_date": t3, "kind": "MANUAL_GENERATION", "value_kwh": 3.0, "reason": "iter22 additive +3"})
    add_neg = q(admin, "POST", "/energy/adjustments", json={"society_id": sid, "device_id": dev_a, "wing": "A", "operating_date": t3, "kind": "MANUAL_GENERATION", "value_kwh": -1.0, "reason": "iter22 additive -1"})
    mix = q(admin, "GET", f"/energy/graph/wing?society_id={sid}&device_id={dev_a}&wing=A&from={t3}&to={t3}&basis=legacy")
    mix_row = mix.json().get("rows", [{}])[0] if mix.status_code == 200 else {}
    phys_row = db_one("SELECT generation_kwh FROM energy_daily WHERE device_id=%s AND operating_date=%s", (dev_a, t3))
    check(
        "9 manual signed adjustments are additive; physical ledger value remains unchanged",
        add_pos.status_code == 200
        and add_neg.status_code == 200
        and mix.status_code == 200
        and mix_row.get("generated_kwh") == 12.0
        and mix_row.get("generation_source") == "MIXED"
        and phys_row is not None
        and float(phys_row.get("generation_kwh")) == 10.0,
    )

    # Keep fixture for browser validations (no credentials written here).
    with open("/tmp/energy_mode_fixture.json", "w", encoding="utf-8") as f:
        json.dump({**fixture, "created_devices": created}, f)

    print(f"\nENERGY_MODE_FIXTURE={json.dumps(fixture)}")
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    finally:
        try:
            delete_created_devices(CREATED_DEVICE_IDS)
            if CREATED_DEVICE_IDS:
                print(f"CLEANUP deleted created devices: {CREATED_DEVICE_IDS}")
        except Exception as cleanup_exc:  # keep explicit failure visible to report
            print(f"CLEANUP ERROR: {cleanup_exc}")
            rc = 1
    sys.exit(rc)
