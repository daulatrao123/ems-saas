"""P2 targeted verification only: energy references + grid reference behavior.

No app-code edits. Uses existing users from TEST_* env vars and creates isolated
devices only. Cleanup deletes only invocation-created devices.
"""

from __future__ import annotations

import json
import calendar
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import requests
from dotenv import load_dotenv
from psycopg.rows import dict_row


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
    return session.request(method, f"{API}{path}", timeout=45, **kwargs)


def login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(
        f"{API}/auth/login",
        json={"email": email, "password": password},
        headers={"Origin": TEST_ORIGIN, "Referer": f"{TEST_ORIGIN}/login"},
        timeout=45,
    )
    if r.status_code != 200:
        raise RuntimeError(f"login failed for {email}: {r.status_code} {r.text[:240]}")
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


def db_exec(sql: str, params: tuple = ()) -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()


def canonical_rows(rows) -> str:
    def norm(v):
        if isinstance(v, dict):
            return {k: norm(v[k]) for k in sorted(v.keys())}
        if isinstance(v, list):
            return [norm(x) for x in v]
        if isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, date):
            return v.isoformat()
        if isinstance(v, Decimal):
            return str(v)
        if isinstance(v, uuid.UUID):
            return str(v)
        return v

    clean = []
    for r in rows:
        item = dict(r)
        clean.append(norm(item))
    return json.dumps(clean, sort_keys=True)


def snapshot_tables(device_id: str) -> dict:
    snap = {}
    snap["device"] = db_one(
        """SELECT id, energy_config_version, energy_calculation_mode, energy_calculation_version,
                  grid_export_enabled, grid_export_limit_kwh, grid_reference_version, energy_allocation, energy_bus
           FROM pi_devices WHERE id=%s""",
        (device_id,),
    )
    snap["energy_daily"] = canonical_rows(
        db_fetchall("SELECT * FROM energy_daily WHERE device_id=%s ORDER BY operating_date", (device_id,))
    )
    snap["energy_meters"] = canonical_rows(
        db_fetchall("SELECT * FROM energy_meters WHERE device_id=%s ORDER BY meter_id", (device_id,))
    )
    snap["targets"] = canonical_rows(
        db_fetchall("SELECT * FROM energy_generation_targets WHERE device_id=%s ORDER BY wing, effective_from, id", (device_id,))
    )
    snap["slots"] = canonical_rows(
        db_fetchall("SELECT * FROM slot_configs WHERE device_id=%s ORDER BY slot", (device_id,))
    )
    snap["state"] = canonical_rows(
        db_fetchall("SELECT * FROM pi_state WHERE device_id=%s ORDER BY device_id", (device_id,))
    )
    snap["commands"] = canonical_rows(
        db_fetchall("SELECT * FROM pi_commands WHERE device_id=%s ORDER BY id", (device_id,))
    )
    snap["bill_count"] = int(
        db_one("SELECT COUNT(*) AS n FROM energy_bill_history WHERE device_id=%s", (device_id,))["n"]
    )
    return snap


def delete_created_devices(device_ids: list[str]) -> None:
    if not device_ids:
        return
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pi_devices WHERE id = ANY(%s)", (device_ids,))
        conn.commit()


def seed_day(
    device_id: str,
    operating_date: str,
    status: str,
    generation_kwh,
    generation_source: str,
    wing_a,
    wing_b,
    wing_c,
    wing_d,
    source_a,
    source_b,
    source_c,
    source_d,
):
    data = {
        "A": wing_a,
        "B": wing_b,
        "C": wing_c,
        "D": wing_d,
    }
    sources = {
        "A": source_a,
        "B": source_b,
        "C": source_c,
        "D": source_d,
    }
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO energy_daily (
                    device_id, operating_date, reset_period, status,
                    generation_kwh, generation_source, wing_generation,
                    unattributed_generation_kwh, fault_generation_kwh,
                    wing_consumption, consumption_source,
                    samples, attempts, gap_kwh, events, updated_at
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s::jsonb,
                    0, 0,
                    %s::jsonb, %s::jsonb,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, NOW()
                )
                ON CONFLICT (device_id, operating_date) DO UPDATE SET
                    status=EXCLUDED.status,
                    generation_kwh=EXCLUDED.generation_kwh,
                    generation_source=EXCLUDED.generation_source,
                    wing_generation=EXCLUDED.wing_generation,
                    wing_consumption=EXCLUDED.wing_consumption,
                    consumption_source=EXCLUDED.consumption_source,
                    updated_at=NOW()
                """,
                (
                    device_id,
                    operating_date,
                    operating_date[:7],
                    status,
                    generation_kwh,
                    generation_source,
                    json.dumps({"A": None, "B": None, "C": None, "D": None}),
                    json.dumps(data),
                    json.dumps(sources),
                    json.dumps({"M1": 1}),
                    json.dumps({"M1": 1}),
                    json.dumps({}),
                    json.dumps([]),
                ),
            )
        conn.commit()


def put_target(device_id: str, wing: str, eff: str, value) -> None:
    db_exec(
        """INSERT INTO energy_generation_targets
           (device_id, wing, base_daily_average_kwh, adjustment_percent, target_kwh_per_day, basis, effective_from, reason, created_by)
           VALUES (%s,%s,%s,0,%s,%s::jsonb,%s,'P2 QA seed',NULL)""",
        (device_id, wing, value, value, json.dumps({"source": "qa"}), eff),
    )


def ensure_meter_rows(device_id: str) -> None:
    rows = [
        ("M1", "GENERATION", None),
        ("M2", "CONSUMPTION", "A"),
        ("M3", "CONSUMPTION", "B"),
        ("M4", "CONSUMPTION", "C"),
        ("M5", "CONSUMPTION", "D"),
    ]
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for meter_id, role, wing in rows:
                cur.execute(
                    """INSERT INTO energy_meters (device_id, meter_id, role, wing)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT (device_id, meter_id) DO NOTHING""",
                    (device_id, meter_id, role, wing),
                )
        conn.commit()


def main() -> int:
    created = CREATED_DEVICE_IDS
    try:
        _super_admin = login(TEST_SUPER_EMAIL, TEST_SUPER_PASSWORD)
        admin = login(TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD)
        member = login(TEST_MEMBER_EMAIL, TEST_MEMBER_PASSWORD)
    except Exception as exc:
        check("0 logins", False, str(exc))
        print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
        return 1

    me = q(admin, "GET", "/auth/me")
    sid = int(me.json().get("society_id")) if me.status_code == 200 and me.json().get("society_id") is not None else None
    check("1 society derived from existing admin", me.status_code == 200 and sid is not None)
    if sid is None:
        print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
        return 1

    tag = uuid.uuid4().hex[:8]
    d1 = q(admin, "POST", "/admin/devices/register", json={"name": f"p2-main-{tag}"})
    dev_a = d1.json().get("device_id") if d1.status_code == 200 else None
    if dev_a:
        created.append(dev_a)
    d2 = q(admin, "POST", "/admin/devices/register", json={"name": f"p2-alt-{tag}"})
    dev_b = d2.json().get("device_id") if d2.status_code == 200 else None
    if dev_b:
        created.append(dev_b)
    check("2 register two isolated devices", d1.status_code == 200 and d2.status_code == 200 and bool(dev_a) and bool(dev_b))
    if not dev_a or not dev_b:
        print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
        return 1

    ensure_meter_rows(dev_a)
    db_exec("UPDATE energy_meters SET enabled=TRUE WHERE device_id=%s AND meter_id IN ('M1','M2','M3','M4','M5')", (dev_a,))

    # --- migration/data-shape sanity
    source_col = db_one(
        """SELECT column_default IS NOT NULL AS has_default
             FROM information_schema.columns
             WHERE table_name='energy_bill_history' AND column_name='source'"""
    )
    grid_ver_col = db_one(
        """SELECT column_default IS NOT NULL AS has_default
             FROM information_schema.columns
             WHERE table_name='pi_devices' AND column_name='grid_reference_version'"""
    )
    check("3 migration columns present with defaults", bool(source_col and source_col["has_default"]) and bool(grid_ver_col and grid_ver_col["has_default"]))

    # --- bills: validation and 12-month window behavior
    body = {"society_id": sid, "device_id": dev_a, "wing": "A", "end_month": "2024-12"}
    bad_dup = q(admin, "PUT", "/energy/bills", json={**body, "months": [
        {"month": "2024-02", "consumption_kwh": 58},
        {"month": "2024-02", "consumption_kwh": 59},
    ]})
    check("4 duplicate request months rejected pre-write", bad_dup.status_code == 400)

    bad_month = q(admin, "PUT", "/energy/bills", json={**body, "months": [{"month": "2024/02", "consumption_kwh": 1}]})
    bad_days = q(admin, "PUT", "/energy/bills", json={**body, "months": [{"month": "2024-02", "consumption_kwh": 1, "days": 28}]})
    bad_neg = q(admin, "PUT", "/energy/bills", json={**body, "months": [{"month": "2024-02", "consumption_kwh": -1}]})
    bad_bool = q(admin, "PUT", "/energy/bills", json={**body, "months": [{"month": "2024-02", "consumption_kwh": True}]})
    bad_inf = q(admin, "PUT", "/energy/bills", json={**body, "months": [{"month": "2024-02", "consumption_kwh": "inf"}]})
    bad_nan = q(admin, "PUT", "/energy/bills", json={**body, "months": [{"month": "2024-02", "consumption_kwh": "NaN"}]})
    check("5 bad month/days/negative/bool/nonfinite rejected", all(r.status_code == 400 for r in [bad_month, bad_days, bad_neg, bad_bool, bad_inf, bad_nan]))

    put_main = q(admin, "PUT", "/energy/bills", json={**body, "months": [
        {"month": "2024-01", "consumption_kwh": 31},
        {"month": "2024-02", "consumption_kwh": 58},
        {"month": "2024-03", "consumption_kwh": ""},
        {"month": "2024-04", "consumption_kwh": 150},
        {"month": "2024-12", "consumption_kwh": 310},
    ]})
    pj = put_main.json() if put_main.status_code == 200 else {}
    expected_unweighted = (31 / 31 + 58 / 29 + 150 / 30 + 310 / 31) / 4
    weighted = (31 + 58 + 150 + 310) / (31 + 29 + 30 + 31)
    check(
        "6 12-month upsert + arithmetic mean formula (not weighted)",
        put_main.status_code == 200
        and pj.get("valid_months") == 4
        and abs(float(pj.get("reference_daily_kwh")) - expected_unweighted) < 1e-6
        and abs(float(pj.get("reference_daily_kwh")) - weighted) > 0.01
        and pj.get("formula") == "SUM(monthly_kwh / actual_calendar_days) / valid_months",
    )

    get_a = q(admin, "GET", f"/energy/bills?society_id={sid}&device_id={dev_a}&wing=A&end_month=2024-12")
    ga = get_a.json() if get_a.status_code == 200 else {}
    m = {x["month"]: x for x in ga.get("months", [])}
    check(
        "7 12 aligned rows + leap/actual day counts + blanks excluded",
        get_a.status_code == 200
        and len(ga.get("months", [])) == 12
        and m.get("2024-02", {}).get("days") == 29
        and m.get("2024-03", {}).get("consumption_kwh") is None,
    )
    check(
        "8 month-day checks Jan31/Apr30 + blank row null",
        m.get("2024-01", {}).get("days") == 31
        and m.get("2024-04", {}).get("days") == 30
        and m.get("2024-03", {}).get("consumption_kwh") is None,
    )

    # 2025-Feb=28 in a separate aligned window
    put_2025 = q(admin, "PUT", "/energy/bills", json={"society_id": sid, "device_id": dev_a, "wing": "A", "end_month": "2025-12", "months": [
        {"month": "2025-02", "consumption_kwh": 280},
    ]})
    get_2025 = q(admin, "GET", f"/energy/bills?society_id={sid}&device_id={dev_a}&wing=A&end_month=2025-12")
    gm = {x["month"]: x for x in get_2025.json().get("months", [])} if get_2025.status_code == 200 else {}
    check("9 February 2025 uses 28 actual days", put_2025.status_code == 200 and gm.get("2025-02", {}).get("days") == 28)

    # Upsert same month should update row, preserve omitted/outside rows, and keep count stable.
    before_count = int(db_one("SELECT COUNT(*) AS n FROM energy_bill_history WHERE device_id=%s AND wing='A'", (dev_a,))["n"])
    q(admin, "PUT", "/energy/bills", json={**body, "months": [{"month": "2024-02", "consumption_kwh": 87}]})
    after_count = int(db_one("SELECT COUNT(*) AS n FROM energy_bill_history WHERE device_id=%s AND wing='A'", (dev_a,))["n"])
    feb_val = float(db_one("SELECT consumption_kwh FROM energy_bill_history WHERE device_id=%s AND wing='A' AND bill_month='2024-02-01'", (dev_a,))["consumption_kwh"])
    jan_val = float(db_one("SELECT consumption_kwh FROM energy_bill_history WHERE device_id=%s AND wing='A' AND bill_month='2024-01-01'", (dev_a,))["consumption_kwh"])
    check("10 month upsert updates only selected row; count stable; omitted rows preserved", before_count == after_count and feb_val == 87.0 and jan_val == 31.0)

    q(admin, "PUT", "/energy/bills", json={"society_id": sid, "device_id": dev_a, "wing": "A", "end_month": "2023-12", "months": [{"month": "2023-01", "consumption_kwh": 123}]})
    q(admin, "PUT", "/energy/bills", json={**body, "months": [{"month": "2024-12", "consumption_kwh": 311}]})
    older = q(admin, "GET", f"/energy/bills?society_id={sid}&device_id={dev_a}&wing=A&end_month=2023-12")
    om = {x["month"]: x for x in older.json().get("months", [])} if older.status_code == 200 else {}
    check("11 older rows retained and accessible via end_month window", older.status_code == 200 and om.get("2023-01", {}).get("consumption_kwh") == 123.0)

    # Wing B one-month and zero valid baseline; Wing C all blank.
    put_b = q(admin, "PUT", "/energy/bills", json={"society_id": sid, "device_id": dev_a, "wing": "B", "end_month": "2025-12", "months": [{"month": "2025-04", "consumption_kwh": 0}]})
    b = put_b.json() if put_b.status_code == 200 else {}
    put_c = q(admin, "PUT", "/energy/bills", json={"society_id": sid, "device_id": dev_a, "wing": "C", "months": [{"month": "2025-04", "consumption_kwh": ""}, {"month": "2025-05", "consumption_kwh": None}]})
    c = q(admin, "GET", f"/energy/bills?society_id={sid}&device_id={dev_a}&wing=C").json() if put_c.status_code == 200 else {}
    check("12 one-month+zero accepted as valid HISTORICAL", put_b.status_code == 200 and b.get("valid_months") == 1 and float(b.get("reference_daily_kwh")) == 0.0 and b.get("source") == "HISTORICAL")
    check("13 all blank entries excluded (no writes, UNAVAILABLE baseline)", put_c.status_code == 200 and put_c.json().get("updated_months") == 0 and c.get("valid_months") == 0 and c.get("source") == "UNAVAILABLE")

    # RBAC and tenant/device forgery checks.
    m_put = q(member, "PUT", "/energy/bills", json={**body, "months": [{"month": "2024-01", "consumption_kwh": 10}]})
    m_get = q(member, "GET", f"/energy/bills?society_id={sid}&device_id={dev_a}&wing=A")
    cross = q(admin, "GET", f"/energy/bills?society_id={sid + 99999}&device_id={dev_a}&wing=A")
    missing = q(admin, "GET", f"/energy/bills?society_id={sid}&device_id={uuid.uuid4()}&wing=A")
    bad_id = q(admin, "GET", f"/energy/bills?society_id={sid}&device_id=not-a-uuid&wing=A")
    check("14 GET/PUT RBAC and tenant/device forgery", m_put.status_code == 403 and m_get.status_code == 200 and cross.status_code == 403 and missing.status_code == 404 and bad_id.status_code == 400)

    # bills/grid mutations should not touch protected paths/state except allowed grid columns.
    pre = snapshot_tables(dev_a)
    put_grid = q(admin, "PUT", "/energy/grid-reference", json={"society_id": sid, "device_id": dev_a, "enabled": True, "limit_kwh_day": 100, "expected_version": int(pre["device"]["grid_reference_version"])})
    post = snapshot_tables(dev_a)
    audit_before_noop = int(db_one("SELECT COUNT(*) AS n FROM audit_log WHERE action='ENERGY_GRID_REFERENCE' AND details::text LIKE %s", (f"%{dev_a}%",))["n"])
    noop = q(admin, "PUT", "/energy/grid-reference", json={"society_id": sid, "device_id": dev_a, "enabled": True, "limit_kwh_day": 100, "expected_version": int(post["device"]["grid_reference_version"])})
    audit_after_noop = int(db_one("SELECT COUNT(*) AS n FROM audit_log WHERE action='ENERGY_GRID_REFERENCE' AND details::text LIKE %s", (f"%{dev_a}%",))["n"])
    stale = q(admin, "PUT", "/energy/grid-reference", json={"society_id": sid, "device_id": dev_a, "enabled": True, "limit_kwh_day": 200, "expected_version": 0})
    alloc_cfg = q(admin, "GET", f"/energy/allocation?society_id={sid}&device_id={dev_a}").json()
    check(
        "15 grid write independent; expected_version/noop/stale behavior",
        put_grid.status_code == 200
        and noop.status_code == 200
        and stale.status_code == 409
        and int(noop.json().get("version")) == int(post["device"]["grid_reference_version"])
        and audit_before_noop == audit_after_noop
        and isinstance(alloc_cfg.get("allocation"), dict),
    )
    check(
        "16 no hardware/config side effects from bills/grid writes",
        pre["energy_daily"] == post["energy_daily"]
        and pre["energy_meters"] == post["energy_meters"]
        and pre["targets"] == post["targets"]
        and pre["slots"] == post["slots"]
        and pre["state"] == post["state"]
        and pre["commands"] == post["commands"]
        and int(pre["device"]["energy_config_version"]) == int(post["device"]["energy_config_version"])
        and int(pre["device"]["energy_calculation_version"]) == int(post["device"]["energy_calculation_version"]),
    )

    # --- explicit source/audit metadata on bill rows
    row = db_one("SELECT source, created_by, updated_by FROM energy_bill_history WHERE device_id=%s AND wing='A' AND bill_month='2024-02-01'", (dev_a,))
    check("17 bill metadata source/audit columns populated", row and row["source"] == "HISTORICAL" and row["created_by"] is not None and row["updated_by"] is not None)

    # --- fallback actual reference rule (newer CLOSED physical only)
    now_day = datetime.now(timezone.utc).date()
    closed_day = (now_day - timedelta(days=2)).isoformat()
    open_day = now_day.isoformat()
    seed_day(dev_a, "2024-12-15", "CLOSED", 500, "PHYSICAL", 99, None, None, None, "PHYSICAL", "UNAVAILABLE", "UNAVAILABLE", "UNAVAILABLE")
    seed_day(dev_a, closed_day, "CLOSED", 520, "PHYSICAL", 7, None, None, None, "PHYSICAL", "UNAVAILABLE", "UNAVAILABLE", "UNAVAILABLE")
    seed_day(dev_a, open_day, "OPEN", 530, "PHYSICAL", 8, None, None, None, "PHYSICAL", "UNAVAILABLE", "UNAVAILABLE", "UNAVAILABLE")
    summary = q(admin, "GET", f"/energy/summary?society_id={sid}&device_id={dev_a}")
    sj = summary.json() if summary.status_code == 200 else {}
    a_ref = sj.get("references", {}).get("wings", {}).get("A", {}).get("effective", {})
    check(
        "18 fallback prefers newer CLOSED physical day after latest historical month-end",
        summary.status_code == 200 and a_ref.get("source") == "PHYSICAL" and a_ref.get("daily_kwh") is not None and abs(float(a_ref.get("daily_kwh")) - 7.0) < 1e-9,
        detail=f"effective={a_ref}",
    )

    # society all-four known rules
    refs = sj.get("references", {})
    check(
        "19 society references require all four wings (no partial masquerade)",
        refs.get("society_historical_daily_kwh") is None and refs.get("society_reference_daily_kwh") is None,
    )

    # --- allocation reference / quotas / generation behavior
    today = sj.get("as_of_operating_date")
    if not today:
        today = datetime.now(timezone.utc).date().isoformat()
    # clear and seed targets
    db_exec("DELETE FROM energy_generation_targets WHERE device_id=%s", (dev_a,))
    put_target(dev_a, "A", "2024-01-01", 210)
    put_target(dev_a, "A", "2025-01-01", 200)  # latest authoritative
    put_target(dev_a, "B", "2025-01-01", 220)
    put_target(dev_a, "C", "2025-01-01", 180)
    put_target(dev_a, "D", "2025-01-01", 210)

    # Generation known in AUTO via M1 PHYSICAL today.
    seed_day(dev_a, today, "OPEN", 700, "PHYSICAL", 1, 1, 1, 1, "PHYSICAL", "PHYSICAL", "PHYSICAL", "PHYSICAL")
    db_exec("UPDATE pi_devices SET energy_calculation_mode='AUTO' WHERE id=%s", (dev_a,))
    q(admin, "PUT", "/energy/grid-reference", json={"society_id": sid, "device_id": dev_a, "enabled": True, "limit_kwh_day": 100, "expected_version": int(db_one("SELECT grid_reference_version FROM pi_devices WHERE id=%s", (dev_a,))["grid_reference_version"])})
    db_exec("UPDATE energy_meters SET enabled=TRUE WHERE device_id=%s AND meter_id='M1'", (dev_a,))
    auto = q(admin, "GET", f"/energy/summary?society_id={sid}&device_id={dev_a}").json()
    alloc = auto["references"]["allocation"]
    wings = {w["wing"]: w for w in alloc["wings"]}
    check(
        "20 sequence A→B→C→D with generation700 required810 unmet110 grid0",
        alloc.get("required_kwh") is not None
        and alloc.get("unmet_kwh") is not None
        and alloc.get("excess_kwh") is not None
        and abs(float(alloc["required_kwh"]) - 810.0) < 1e-9
        and abs(float(alloc["unmet_kwh"]) - 110.0) < 1e-9
        and abs(float(alloc["excess_kwh"]) - 0.0) < 1e-9
        and abs(float(alloc["grid_allocation_kwh"]) - 0.0) < 1e-9
        and abs(float(wings["A"]["allocated_kwh"]) - 200.0) < 1e-9
        and abs(float(wings["B"]["allocated_kwh"]) - 220.0) < 1e-9
        and abs(float(wings["C"]["allocated_kwh"]) - 180.0) < 1e-9
        and abs(float(wings["D"]["allocated_kwh"]) - 100.0) < 1e-9,
        detail=f"allocation={alloc}",
    )

    seed_day(dev_a, today, "OPEN", 900, "PHYSICAL", 1, 1, 1, 1, "PHYSICAL", "PHYSICAL", "PHYSICAL", "PHYSICAL")
    auto900 = q(admin, "GET", f"/energy/summary?society_id={sid}&device_id={dev_a}").json()["references"]["allocation"]
    seed_day(dev_a, today, "OPEN", 1000, "PHYSICAL", 1, 1, 1, 1, "PHYSICAL", "PHYSICAL", "PHYSICAL", "PHYSICAL")
    auto1000 = q(admin, "GET", f"/energy/summary?society_id={sid}&device_id={dev_a}").json()["references"]["allocation"]
    v = int(db_one("SELECT grid_reference_version FROM pi_devices WHERE id=%s", (dev_a,))["grid_reference_version"])
    q(admin, "PUT", "/energy/grid-reference", json={"society_id": sid, "device_id": dev_a, "enabled": True, "limit_kwh_day": 0, "expected_version": v})
    auto_limit0 = q(admin, "GET", f"/energy/summary?society_id={sid}&device_id={dev_a}").json()["references"]["allocation"]
    ok900 = auto900.get("excess_kwh") is not None and auto900.get("grid_allocation_kwh") is not None
    ok1000 = auto1000.get("excess_kwh") is not None and auto1000.get("grid_allocation_kwh") is not None
    ok0 = auto_limit0.get("grid_allocation_kwh") is not None
    check(
        "21 grid min(excess,limit) only when all satisfied+enabled+positive excess",
        ok900
        and ok1000
        and ok0
        and abs(float(auto900["excess_kwh"]) - 90.0) < 1e-9
        and abs(float(auto900["grid_allocation_kwh"]) - 90.0) < 1e-9
        and abs(float(auto1000["excess_kwh"]) - 190.0) < 1e-9
        and abs(float(auto1000["grid_allocation_kwh"]) - 100.0) < 1e-9
        and float(auto_limit0["grid_allocation_kwh"]) == 0.0
        and auto_limit0["reason"] == "GRID_LIMIT_ZERO",
        detail=f"900={auto900} 1000={auto1000} lim0={auto_limit0}",
    )

    # Missing quota blocks downstream allocation.
    db_exec("DELETE FROM energy_generation_targets WHERE device_id=%s AND wing='D'", (dev_a,))
    missing_quota = q(admin, "GET", f"/energy/summary?society_id={sid}&device_id={dev_a}").json()["references"]["allocation"]
    check("22 missing required quota => UNAVAILABLE and grid0", missing_quota["status"] == "UNAVAILABLE" and missing_quota["reason"] == "REQUIRED_WING_QUOTA_UNAVAILABLE" and float(missing_quota["grid_allocation_kwh"]) == 0.0)

    # MANUAL mode: no common manual generation.
    db_exec("UPDATE pi_devices SET energy_calculation_mode='MANUAL' WHERE id=%s", (dev_a,))
    manual = q(admin, "GET", f"/energy/summary?society_id={sid}&device_id={dev_a}").json()["references"]["allocation"]
    check("23 MANUAL mode generation/excess unavailable and grid0", manual["generation_kwh"] is None and manual["excess_kwh"] is None and float(manual["grid_allocation_kwh"]) == 0.0 and manual["reason"] == "SOCIETY_GENERATION_UNAVAILABLE")

    # Decimal boundary equality checks.
    db_exec("DELETE FROM energy_generation_targets WHERE device_id=%s", (dev_a,))
    put_target(dev_a, "A", "2025-01-01", Decimal("0.1"))
    put_target(dev_a, "B", "2025-01-01", Decimal("0.2"))
    put_target(dev_a, "C", "2025-01-01", Decimal("0.3"))
    put_target(dev_a, "D", "2025-01-01", Decimal("0.4"))
    db_exec("UPDATE pi_devices SET energy_calculation_mode='AUTO' WHERE id=%s", (dev_a,))
    seed_day(dev_a, today, "OPEN", Decimal("1.0"), "PHYSICAL", 1, 1, 1, 1, "PHYSICAL", "PHYSICAL", "PHYSICAL", "PHYSICAL")
    exact = q(admin, "GET", f"/energy/summary?society_id={sid}&device_id={dev_a}").json()["references"]["allocation"]
    check("24 exact decimal boundaries: required==generation => unmet0 excess0", exact["all_quotas_satisfied"] is True and float(exact["unmet_kwh"]) == 0.0 and float(exact["excess_kwh"]) == 0.0)

    # Full-year input, exact recomputation, and actual-source negative controls.
    ensure_meter_rows(dev_b)
    db_exec("UPDATE energy_meters SET enabled=TRUE WHERE device_id=%s", (dev_b,))
    bbody = {"society_id": sid, "device_id": dev_b, "wing": "A", "end_month": "2024-12"}
    twelve = [{"month": f"2024-{n:02d}", "consumption_kwh": calendar.monthrange(2024, n)[1] * n} for n in range(1, 13)]
    full = q(admin, "PUT", "/energy/bills", json={**bbody, "months": twelve})
    check("25 all twelve supplied months produce twelve valid months and mean6.5", full.status_code == 200 and full.json()["valid_months"] == 12 and full.json()["reference_daily_kwh"] == 6.5)
    updated = q(admin, "PUT", "/energy/bills", json={**bbody, "months": [{"month": "2024-01", "consumption_kwh": 62}]})
    check("26 monthly correction recalculates mean without duplicates", updated.status_code == 200 and updated.json()["valid_months"] == 12 and updated.json()["reference_daily_kwh"] == round(79 / 12, 4)
          and db_one("SELECT COUNT(*) AS n FROM energy_bill_history WHERE device_id=%s AND wing='A'", (dev_b,))["n"] == 12)
    untouched = q(admin, "PUT", "/energy/bills", json={**bbody, "months": [{"month": "2024-01", "consumption_kwh": ""}]})
    check("27 blank existing month retains saved value", untouched.status_code == 200 and untouched.json()["updated_months"] == 0 and untouched.json()["months"][0]["consumption_kwh"] == 62)
    before_a = canonical_rows(db_fetchall("SELECT * FROM energy_bill_history WHERE device_id=%s ORDER BY id", (dev_a,)))
    def refs_b():
        return q(admin, "GET", f"/energy/summary?society_id={sid}&device_id={dev_b}").json()["references"]
    seed_day(dev_b, "2024-12-15", "CLOSED", 0, "PHYSICAL", 90, None, None, None, "PHYSICAL", "UNAVAILABLE", "UNAVAILABLE", "UNAVAILABLE")
    seed_day(dev_b, now_day.isoformat(), "OPEN", 1000, "PHYSICAL", 99, None, None, None, "PHYSICAL", "UNAVAILABLE", "UNAVAILABLE", "UNAVAILABLE")
    seed_day(dev_b, (now_day-timedelta(days=1)).isoformat(), "CLOSED", 0, "PHYSICAL", 88, None, None, None, "MANUAL", "UNAVAILABLE", "UNAVAILABLE", "UNAVAILABLE")
    old = refs_b()["wings"]["A"]["effective"]
    check("28 older physical, current OPEN and newer manual data cannot replace historical", old["source"] == "HISTORICAL" and old["daily_kwh"] == round(79 / 12, 4))
    seed_day(dev_b, (now_day-timedelta(days=2)).isoformat(), "CLOSED", 0, "PHYSICAL", 8, None, None, None, "PHYSICAL", "UNAVAILABLE", "UNAVAILABLE", "UNAVAILABLE")
    check("29 newer completed PHYSICAL consumption replaces baseline", refs_b()["wings"]["A"]["effective"]["daily_kwh"] == 8)
    db_exec("UPDATE energy_meters SET enabled=FALSE WHERE device_id=%s AND meter_id='M2'", (dev_b,))
    check("30 disabled meter does not establish actual reference", refs_b()["wings"]["A"]["effective"]["source"] == "HISTORICAL")
    db_exec("UPDATE energy_meters SET enabled=TRUE WHERE device_id=%s AND meter_id='M2'", (dev_b,))
    for w, kwh in (("B", 31), ("C", 31), ("D", 0)):
        q(admin, "PUT", "/energy/bills", json={**bbody, "wing": w, "months": [{"month": "2024-12", "consumption_kwh": kwh}]})
    all_refs = refs_b()
    check("31 all-four baseline/effective totals include explicit zero", all_refs["society_historical_daily_kwh"] == round(79/12+2, 4) and all_refs["society_reference_daily_kwh"] == 10
          and all_refs["society_reference_source"] == "MIXED_REFERENCE")
    check("32 deviceB bill changes do not alter deviceA history", before_a == canonical_rows(db_fetchall("SELECT * FROM energy_bill_history WHERE device_id=%s ORDER BY id", (dev_a,))))
    for w, val in zip("ABCD", (200, 220, 180, 210)):
        put_target(dev_b, w, "2024-01-01", val)
    def grid_b(enabled, limit):
        version = refs_b()["grid"]["version"]
        return q(admin, "PUT", "/energy/grid-reference", json={"society_id": sid, "device_id": dev_b, "enabled": enabled, "limit_kwh_day": limit, "expected_version": version})
    grid_b(True, 500)
    check("33 Grid YES high limit assigns exact excess190", refs_b()["allocation"]["grid_allocation_kwh"] == 190)
    grid_b(False, 500)
    no = refs_b()["allocation"]
    check("34 Grid NO with positive excess yields zero and disabled reason", no["excess_kwh"] == 190 and no["grid_allocation_kwh"] == 0 and no["reason"] == "GRID_EXPORT_DISABLED")
    grid_b(True, 500)
    db_exec("DELETE FROM energy_generation_targets WHERE device_id=%s AND wing='B'", (dev_b,))
    blocked = refs_b()["allocation"]
    check("35 missing B blocks C/D assignments and Grid, not zero demand", blocked["required_kwh"] is None and blocked["excess_kwh"] is None and blocked["grid_allocation_kwh"] == 0 and not blocked["all_quotas_satisfied"]
          and [r["allocated_kwh"] for r in blocked["wings"]] == [200, None, None, None])
    put_target(dev_b, "B", "2024-01-01", 220)
    for w in "ABCD":
        put_target(dev_b, w, "2025-01-01", 0)
    check("36 explicit zero quotas are valid, distinct from missing", refs_b()["allocation"]["required_kwh"] == 0 and refs_b()["allocation"]["all_quotas_known"])
    m_grid = q(member, "PUT", "/energy/grid-reference", json={"society_id": sid, "device_id": dev_b, "enabled": False, "expected_version": 0})
    x_grid = q(admin, "PUT", "/energy/grid-reference", json={"society_id": sid+99999, "device_id": dev_b, "enabled": False, "expected_version": 0})
    check("37 grid writes enforce member and tenant RBAC", m_grid.status_code == 403 and x_grid.status_code == 403)
    gbody = {"society_id": sid, "device_id": dev_b, "enabled": True, "expected_version": refs_b()["grid"]["version"]}
    invalid_grid = [q(admin, "PUT", "/energy/grid-reference", json={**gbody, **changes}).status_code for changes in ({}, {"limit_kwh_day": -1}, {"limit_kwh_day": True}, {"limit_kwh_day": "inf"}, {"enabled": "YES", "limit_kwh_day": 1})]
    check("38 grid invalid/missing limits and nonboolean enabled rejected", invalid_grid == [400]*5)
    # Nonempty full-row snapshots around BOTH bill and grid writes; preserve timestamps too.
    before = snapshot_tables(dev_b)
    q(admin, "PUT", "/energy/bills", json={**bbody, "months": [{"month": "2024-03", "consumption_kwh": 123}]})
    grid_b(False, 500)
    after = snapshot_tables(dev_b)
    protected = ("energy_daily", "energy_meters", "targets", "slots", "state", "commands")
    device_fields = ("energy_config_version", "energy_calculation_mode", "energy_calculation_version", "energy_allocation", "energy_bus")
    check("39 bill/grid writes preserve populated physical rows, targets, configs, P1mode and timestamps", all(before[k] == after[k] for k in protected)
          and all(before["device"][k] == after["device"][k] for k in device_fields)
          and json.loads(before["energy_daily"]) and json.loads(before["targets"]))

    print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    finally:
        delete_created_devices(CREATED_DEVICE_IDS)
    sys.exit(rc)
