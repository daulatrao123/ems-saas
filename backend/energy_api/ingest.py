"""Pi -> cloud ingestion of the `energy` block of /api/pi/sync and the `energy_config` reply.
Config (enabled/serial/address/map/bus) is backend-authoritative; the Pi only reports health + measurements."""
import logging
import math
from datetime import date, datetime, timezone

from psycopg.types.json import Json
from .operating_dates import day_problem, quarantine, record_clock_report, utc_now
from .target_delivery import reconcile_targets, record_report
from . import queries as Q
from fastapi import HTTPException

METER_IDS = ("M1", "M2", "M3", "M4", "M5")
METER_ROLE = {"M1": "GENERATION", "M2": "CONSUMPTION", "M3": "CONSUMPTION", "M4": "CONSUMPTION", "M5": "CONSUMPTION"}
METER_WING = {"M1": None, "M2": "A", "M3": "B", "M4": "C", "M5": "D"}
WINGS = ("A", "B", "C", "D")
COMM_STATES = {"DISABLED", "NOT_CONFIGURED", "ONLINE", "DEGRADED", "OFFLINE"}
READINGS_RETENTION_DAYS = 30
DEFAULT_ALLOCATION = {"enabled": False, "sequence": list(WINGS), "tolerance_kwh": 1.0, "persistence_s": 300,
                      "wings": {w: {"generation_attribution_enabled": True, "manual_target_kwh": None} for w in WINGS}}


def _num(v):
    try:
        f = float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
    return None if f is not None and math.isnan(f) else f


def _ts(v):
    v = _num(v)
    return datetime.fromtimestamp(v, tz=timezone.utc) if v else None


def _wing_map(raw):
    raw = raw if isinstance(raw, dict) else {}
    return {w: _num(raw.get(w)) for w in WINGS}


def ensure_meter_rows(cur, device_id):
    for mid in METER_IDS:
        cur.execute("""INSERT INTO energy_meters (device_id, meter_id, role, wing) VALUES (%s, %s, %s, %s)
                       ON CONFLICT (device_id, meter_id) DO NOTHING""", (device_id, mid, METER_ROLE[mid], METER_WING[mid]))


def normalize_day(raw, now=None):
    """Pi ledger row -> typed row. Missing/None stays None (UNAVAILABLE). Returns None if unusable."""
    if day_problem(raw, utc_now(now)):
        return None
    try:
        od = date.fromisoformat(str(raw.get("operating_date")))
    except (TypeError, ValueError):
        return None
    status = "CLOSED" if str(raw.get("status", "OPEN")).upper() == "CLOSED" else "OPEN"
    gen = _num(raw.get("generation_kwh"))
    src = str(raw.get("generation_source") or "UNAVAILABLE").upper()
    cons_src = raw.get("consumption_source") if isinstance(raw.get("consumption_source"), dict) else {}
    return {
        "operating_date": od, "reset_period": str(raw.get("reset_period") or f"{od.year:04d}-{od.month:02d}")[:7], "status": status,
        "generation_kwh": gen, "generation_source": src if src in ("PHYSICAL", "UNAVAILABLE") else "UNAVAILABLE",
        "wing_generation": _wing_map(raw.get("wing_generation")),
        "unattributed_generation_kwh": _num(raw.get("unattributed_generation_kwh")) or 0.0,
        "fault_generation_kwh": _num(raw.get("fault_generation_kwh")) or 0.0,
        "wing_consumption": _wing_map(raw.get("wing_consumption")),
        "consumption_source": {w: ("PHYSICAL" if str(cons_src.get(w, "")).upper() == "PHYSICAL" else "UNAVAILABLE") for w in WINGS},
        "samples": raw.get("samples") if isinstance(raw.get("samples"), dict) else {},
        "attempts": raw.get("attempts") if isinstance(raw.get("attempts"), dict) else {},
        "gap_kwh": raw.get("gap_kwh") if isinstance(raw.get("gap_kwh"), dict) else {},
        "events": (raw.get("events") if isinstance(raw.get("events"), list) else [])[-50:],
        "opened_at": _ts(raw.get("opened_at")), "closed_at": _ts(raw.get("closed_at")), "updated_at": _ts(raw.get("updated_at")),
    }


def upsert_day(cur, device_id, day):
    """Idempotent: a CLOSED row is never replaced by an OPEN one; same-status rows take the newer updated_at."""
    cur.execute("""
        INSERT INTO energy_daily (device_id, operating_date, reset_period, status, generation_kwh, generation_source, wing_generation,
            unattributed_generation_kwh, fault_generation_kwh, wing_consumption, consumption_source, samples, attempts, gap_kwh, events,
            opened_at, closed_at, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (device_id, operating_date) DO UPDATE SET
            reset_period=EXCLUDED.reset_period, status=EXCLUDED.status, generation_kwh=EXCLUDED.generation_kwh,
            generation_source=EXCLUDED.generation_source, wing_generation=EXCLUDED.wing_generation,
            unattributed_generation_kwh=EXCLUDED.unattributed_generation_kwh, fault_generation_kwh=EXCLUDED.fault_generation_kwh,
            wing_consumption=EXCLUDED.wing_consumption, consumption_source=EXCLUDED.consumption_source, samples=EXCLUDED.samples,
            attempts=EXCLUDED.attempts, gap_kwh=EXCLUDED.gap_kwh, events=EXCLUDED.events, opened_at=EXCLUDED.opened_at,
            closed_at=EXCLUDED.closed_at, updated_at=EXCLUDED.updated_at, received_at=NOW()
        WHERE NOT (energy_daily.status = 'CLOSED' AND EXCLUDED.status = 'OPEN')
          AND (energy_daily.status <> EXCLUDED.status OR energy_daily.updated_at IS NULL OR EXCLUDED.updated_at IS NULL
               OR EXCLUDED.updated_at >= energy_daily.updated_at)
    """, (device_id, day["operating_date"], day["reset_period"], day["status"], day["generation_kwh"], day["generation_source"],
          Json(day["wing_generation"]), day["unattributed_generation_kwh"], day["fault_generation_kwh"], Json(day["wing_consumption"]),
          Json(day["consumption_source"]), Json(day["samples"]), Json(day["attempts"]), Json(day["gap_kwh"]), Json(day["events"]),
          day["opened_at"], day["closed_at"], day["updated_at"]))


def ingest(cur, device_id, energy, now):
    """Store meter health, readings and ledger rows inside a SAVEPOINT: a bad energy block never fails the Pi sync."""
    if not isinstance(energy, dict):
        return {"days": 0}
    cur.execute("SAVEPOINT energy_ingest")
    try:
        out = _ingest(cur, device_id, energy, now)
        cur.execute("RELEASE SAVEPOINT energy_ingest")
        return out
    except Exception as exc:  # isolate: the rest of /api/pi/sync (commands, acks, state) must still commit
        cur.execute("ROLLBACK TO SAVEPOINT energy_ingest")
        logging.getLogger("ems.energy").error("energy ingest failed device=%s: %s", device_id, exc)
        return {"days": 0, "error": str(exc)[:200]}


def _ingest(cur, device_id, energy, now):
    cur.execute("SELECT id FROM pi_devices WHERE id=%s FOR UPDATE", (device_id,))
    ensure_meter_rows(cur, device_id)
    meters = energy.get("meters") if isinstance(energy.get("meters"), dict) else {}
    for mid in METER_IDS:
        h = meters.get(mid)
        if not isinstance(h, dict):
            continue
        comm = str(h.get("comm_status") or "DISABLED").upper()
        attribution = energy.get("attribution") if mid == "M1" and isinstance(energy.get("attribution"), dict) else None
        cur.execute("""UPDATE energy_meters SET comm_status=%s, last_seen=%s, last_kwh=%s, power_kw=%s, last_error=%s,
                       attribution=COALESCE(%s::jsonb, attribution), updated_at=%s WHERE device_id=%s AND meter_id=%s""",
                    (comm if comm in COMM_STATES else "OFFLINE", _ts(h.get("last_seen")), _num(h.get("last_kwh")), _num(h.get("power_kw")),
                     (str(h["last_error"])[:300] if h.get("last_error") else None), Json(attribution) if attribution else None, now, device_id, mid))
        if _ts(h.get("last_seen")) and _num(h.get("last_kwh")) is not None:
            cur.execute("""INSERT INTO energy_meter_readings (device_id, meter_id, ts, cumulative_kwh, power_kw) VALUES (%s,%s,%s,%s,%s)
                           ON CONFLICT DO NOTHING""", (device_id, mid, _ts(h["last_seen"]), _num(h["last_kwh"]), _num(h.get("power_kw"))))
    days, rejected = 0, []
    closed = energy.get("closed_days")
    closed = closed[:31] if isinstance(closed, list) else []
    for raw in closed + [energy.get("today")]:
        if raw is None:
            continue
        problem = day_problem(raw, now)
        if problem:
            rejected.append(quarantine(cur, device_id, raw, problem, now))
            continue
        day = normalize_day(raw, now)
        if day:
            upsert_day(cur, device_id, day)
            days += 1
    cur.execute("DELETE FROM energy_meter_readings WHERE device_id=%s AND ts < NOW() - make_interval(days => %s)", (device_id, READINGS_RETENTION_DAYS))
    report = record_clock_report(cur, device_id, energy.get("today"), rejected, now)
    return {"days": days, "clock_report": report}


def build_energy_config(cur, device_id, now=None):
    """Backend-authoritative config for the Pi: {"version", "bus", "meters": {Mx: {...}}}."""
    now = utc_now(now)
    cur.execute("SELECT energy_bus, energy_config_version, energy_allocation FROM pi_devices WHERE id=%s FOR UPDATE", (device_id,))
    dev = cur.fetchone()
    if not dev:
        return None
    # Invalid OPEN data must not select future targets, nor abort the rest of Pi sync.
    try:
        as_of = Q.device_today(cur, device_id, now.date(), now=now)
    except HTTPException as exc:
        logging.getLogger("ems.energy").warning("Energy config withheld device=%s: %s", device_id, exc.detail)
        return None
    ensure_meter_rows(cur, device_id)
    cur.execute("""SELECT meter_id, serial, modbus_address, model, register_map, phases, ct_ratio, max_kw, enabled
                   FROM energy_meters WHERE device_id=%s ORDER BY meter_id""", (device_id,))
    meters = {}
    for r in cur.fetchall():
        meters[r["meter_id"]] = {"enabled": bool(r["enabled"]), "serial": r["serial"], "modbus_address": r["modbus_address"], "model": r["model"],
                                 "register_map": r["register_map"], "phases": r["phases"],
                                 "ct_ratio": _num(r["ct_ratio"]), "max_kw": _num(r["max_kw"])}
    try:
        targets, version = reconcile_targets(cur, device_id, as_of, dev["energy_config_version"])
    except HTTPException as exc:
        logging.getLogger("ems.energy").warning("Energy config withheld device=%s: %s", device_id, exc.detail)
        return None
    allocation = dev["energy_allocation"] or DEFAULT_ALLOCATION
    return {"version": version, "bus": dev["energy_bus"] or {}, "meters": meters,
            "allocation": allocation, "targets": targets}


def config_reply(cur, device_id, energy, now=None):
    """Include energy_config in the sync reply only when the Pi runs a different version (or none)."""
    now = utc_now(now)
    cfg = build_energy_config(cur, device_id, now)
    reported = record_report(cur, device_id, energy, now)
    if cfg is None:
        return None
    return cfg if reported != cfg["version"] else None
