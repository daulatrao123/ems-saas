"""Energy API (E2). Backend-authoritative config, read models derived from energy_daily. Prefix /api/energy.
RBAC: super_admin (any society) / society_admin (own society) write; member read-only (own society)."""
import math
import calendar
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row
from psycopg.types.json import Json

from . import queries as Q
from . import references as R
from . import comparison as C
from .operating_dates import date_problem, require_qualified_open
from .target_delivery import reconcile_targets, delivery_view
from .ingest import DEFAULT_ALLOCATION, METER_IDS, METER_ROLE, METER_WING, WINGS, ensure_meter_rows

VALUE_TYPES = {"uint16", "int16", "uint32", "int32", "float32", "uint64", "int64", "float64"}
KINDS = {"MANUAL_GENERATION", "MANUAL_CONSUMPTION", "ACCOUNTING"}
WRITE_ROLES = {"super_admin", "society_admin"}


def validate_register_map(rmap):
    if not isinstance(rmap, dict):
        return ["register_map must be an object"]
    p = []
    if rmap.get("verified") is not True:
        p.append("register_map must be marked verified (from the meter datasheet)")
    regs = rmap.get("registers") if isinstance(rmap.get("registers"), dict) else {}
    e = regs.get("energy_total_kwh")
    if not isinstance(e, dict):
        return p + ["registers.energy_total_kwh is required"]
    if not isinstance(e.get("address"), int) or isinstance(e.get("address"), bool) or not 0 <= e["address"] <= 65535:
        p.append("energy_total_kwh.address must be an integer 0..65535")
    if e.get("type") not in VALUE_TYPES:
        p.append("energy_total_kwh.type invalid")
    if e.get("unit") not in ("kWh", "Wh", "MWh"):
        p.append("energy_total_kwh.unit must be kWh/Wh/MWh")
    if e.get("function", 3) not in (3, 4):
        p.append("energy_total_kwh.function must be 3 or 4")
    return p


def _date(v, what):
    try:
        return date.fromisoformat(str(v))
    except (TypeError, ValueError):
        raise HTTPException(400, f"{what} must be YYYY-MM-DD")


def _wing(v):
    w = str(v or "").upper()
    if w not in WINGS:
        raise HTTPException(400, "wing must be A, B, C or D")
    return w


def _num(v, what, lo=None, hi=None):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{what} must be numeric")
    if isinstance(v, bool) or not math.isfinite(f) or (lo is not None and f < lo) or (hi is not None and f > hi):
        raise HTTPException(400, f"{what} out of range")
    return f


def create_router(get_db, get_current_user, log_audit, require_uuid):
    router = APIRouter(prefix="/api/energy")

    def access(user, society_id, write=False):
        if write and user.get("role") not in WRITE_ROLES:
            raise HTTPException(403, "Read-only role")
        try:
            sid = int(society_id)
        except (TypeError, ValueError):
            raise HTTPException(400, "society_id is required")
        if user.get("role") != "super_admin" and str(user.get("society_id")) != str(sid):
            raise HTTPException(403, "Cannot access other society data")
        return sid

    def device(cur, sid, device_id, ensure_meters=True, *, lock=False):
        did = require_uuid(device_id, "device_id")
        cur.execute("""SELECT d.id, d.energy_bus, d.energy_config_version,
                              d.energy_calculation_mode, d.energy_calculation_version,
                              d.grid_export_enabled, d.grid_export_limit_kwh, d.grid_reference_version,
                              COALESCE(s.reset_day, 15) AS reset_day
                       FROM pi_devices d JOIN societies s ON s.id=d.society_id WHERE d.id=%s AND d.society_id=%s""" + (" FOR UPDATE OF d" if lock else ""), (did, sid))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Device not found in this society")
        if ensure_meters:
            ensure_meter_rows(cur, did)
        return row

    def run(fn, write=False):
        conn = get_db()
        try:
            with conn.cursor(row_factory=dict_row) as cur:
                out = fn(cur)
            conn.commit() if write else conn.rollback()
            return out
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def meters_of(cur, did):
        cur.execute("SELECT * FROM energy_meters WHERE device_id=%s ORDER BY meter_id", (did,))
        out = {}
        for r in cur.fetchall():
            r = dict(r)
            for k in ("last_kwh", "power_kw", "ct_ratio", "max_kw"):
                r[k] = float(r[k]) if r[k] is not None else None
            r["last_seen"] = r["last_seen"].isoformat() if r["last_seen"] else None
            r["updated_at"] = r["updated_at"].isoformat() if r["updated_at"] else None
            r.pop("device_id", None)
            out[r["meter_id"]] = r
        return out

    # ---------------------------------------------------------------- meters / bus
    @router.get("/meters")
    def get_meters(society_id: str, device_id: str, user: dict = Depends(get_current_user)):
        sid = access(user, society_id)
        def fn(cur):
            dev = device(cur, sid, device_id)
            return {"device_id": str(dev["id"]), "config_version": dev["energy_config_version"], "bus": dev["energy_bus"] or {},
                    "meters": meters_of(cur, str(dev["id"]))}
        return run(fn)

    @router.put("/meters/{meter_id}")
    def put_meter(meter_id: str, data: dict, user: dict = Depends(get_current_user)):
        if meter_id not in METER_IDS:
            raise HTTPException(400, f"meter_id must be one of {list(METER_IDS)} (exactly 1 generation + 4 consumption meters)")
        sid = access(user, data.get("society_id"), write=True)
        def fn(cur):
            dev = device(cur, sid, data.get("device_id"), lock=True); did = str(dev["id"])
            cur.execute("SELECT * FROM energy_meters WHERE device_id=%s AND meter_id=%s", (did, meter_id))
            cur_row = dict(cur.fetchone())
            fields = {}
            for k in ("serial", "model"):
                if k in data: fields[k] = (str(data[k]).strip()[:120] or None) if data[k] is not None else None
            if "modbus_address" in data:
                a = data["modbus_address"]
                if a is not None and (not isinstance(a, int) or isinstance(a, bool) or not 1 <= a <= 247): raise HTTPException(400, "modbus_address must be 1..247")
                fields["modbus_address"] = a
            if "register_map" in data:
                rm = data["register_map"]
                if rm is not None and validate_register_map(rm): raise HTTPException(400, "; ".join(validate_register_map(rm)))
                fields["register_map"] = Json(rm) if rm is not None else None
            for k, lo, hi in (("phases", 1, 3), ("ct_ratio", 0.001, 100000), ("max_kw", 0.001, 100000)):
                if k in data:
                    fields[k] = None if data[k] is None else (int(_num(data[k], k, lo, hi)) if k == "phases" else _num(data[k], k, lo, hi))
            if "enabled" in data: fields["enabled"] = bool(data["enabled"])
            if not fields: raise HTTPException(400, "No fields to update")
            merged = {**cur_row, **{k: (v.obj if isinstance(v, Json) else v) for k, v in fields.items()}}
            if merged["enabled"]:
                missing = [k for k in ("serial", "modbus_address", "register_map") if not merged.get(k)]
                if missing: raise HTTPException(409, f"Cannot enable {meter_id}: missing {', '.join(missing)}")
                if validate_register_map(merged["register_map"]): raise HTTPException(409, f"Cannot enable {meter_id}: register map not verified")
                if not (dev["energy_bus"] or {}).get("port"): raise HTTPException(409, "Cannot enable a meter: RS485 bus port not configured")
                cur.execute("SELECT meter_id FROM energy_meters WHERE device_id=%s AND enabled AND modbus_address=%s AND meter_id<>%s", (did, merged["modbus_address"], meter_id))
                dup = cur.fetchone()
                if dup: raise HTTPException(409, f"Modbus address {merged['modbus_address']} already used by {dup['meter_id']}")
            if merged.get("serial"):
                cur.execute("SELECT meter_id FROM energy_meters WHERE device_id=%s AND serial=%s AND meter_id<>%s", (did, merged["serial"], meter_id))
                dup = cur.fetchone()
                if dup: raise HTTPException(409, f"Serial {merged['serial']} already assigned to {dup['meter_id']}")
            sets = ", ".join(f"{k}=%s" for k in fields) + ", updated_at=NOW()"
            cur.execute(f"UPDATE energy_meters SET {sets} WHERE device_id=%s AND meter_id=%s", (*fields.values(), did, meter_id))
            if not merged["enabled"]:
                cur.execute("UPDATE energy_meters SET comm_status='DISABLED' WHERE device_id=%s AND meter_id=%s", (did, meter_id))
            cur.execute("UPDATE pi_devices SET energy_config_version=energy_config_version+1 WHERE id=%s RETURNING energy_config_version", (did,))
            ver = cur.fetchone()["energy_config_version"]
            log_audit(cur, user, sid, "ENERGY_METER_CONFIG", {"device_id": did, "meter_id": meter_id, "fields": sorted(fields), "enabled": merged["enabled"], "version": ver})
            return {"success": True, "config_version": ver, "meter": meters_of(cur, did)[meter_id]}
        return run(fn, write=True)

    @router.put("/bus")
    def put_bus(data: dict, user: dict = Depends(get_current_user)):
        sid = access(user, data.get("society_id"), write=True)
        port = str(data.get("port") or "").strip()
        if port and not port.startswith("/dev/"):
            raise HTTPException(400, "port must be a /dev/... path (prefer /dev/serial/by-id/...)")
        serial = data.get("serial") if isinstance(data.get("serial"), dict) else {}
        allowed = {"baudrate": (300, 115200), "bytesize": (7, 8), "stopbits": (1, 2), "timeout_s": (0.05, 5)}
        clean = {}
        for k, (lo, hi) in allowed.items():
            if k in serial:
                clean[k] = _num(serial[k], k, lo, hi) if k == "timeout_s" else int(_num(serial[k], k, lo, hi))
        if "parity" in serial:
            if str(serial["parity"]).upper() not in ("N", "E", "O"): raise HTTPException(400, "parity must be N, E or O")
            clean["parity"] = str(serial["parity"]).upper()
        def fn(cur):
            dev = device(cur, sid, data.get("device_id"), lock=True); did = str(dev["id"])
            bus = {"port": port or None, "serial": clean}
            if not port:
                cur.execute("SELECT COUNT(*) AS n FROM energy_meters WHERE device_id=%s AND enabled", (did,))
                if cur.fetchone()["n"]: raise HTTPException(409, "Disable all meters before removing the RS485 bus port")
            cur.execute("UPDATE pi_devices SET energy_bus=%s, energy_config_version=energy_config_version+1 WHERE id=%s RETURNING energy_config_version", (Json(bus), did))
            ver = cur.fetchone()["energy_config_version"]
            log_audit(cur, user, sid, "ENERGY_BUS_CONFIG", {"device_id": did, "port": port or None, "version": ver})
            return {"success": True, "config_version": ver, "bus": bus}
        return run(fn, write=True)

    @router.get("/allocation")
    def get_allocation(society_id: str, device_id: str, user: dict = Depends(get_current_user)):
        sid = access(user, society_id)
        def fn(cur):
            dev = device(cur, sid, device_id)
            cur.execute("SELECT energy_allocation FROM pi_devices WHERE id=%s", (dev["id"],))
            return {"allocation": cur.fetchone()["energy_allocation"] or DEFAULT_ALLOCATION, "config_version": dev["energy_config_version"]}
        return run(fn)

    @router.put("/allocation")
    def put_allocation(data: dict, user: dict = Depends(get_current_user)):
        sid = access(user, data.get("society_id"), write=True)
        seq = data.get("sequence", list(WINGS))
        if not isinstance(seq, list) or [str(w).upper() for w in seq] != list(WINGS):
            raise HTTPException(400, "sequence must be exactly [A, B, C, D]")
        cfg = {"enabled": bool(data.get("enabled", False)), "sequence": list(WINGS),
               "tolerance_kwh": _num(data.get("tolerance_kwh", 1.0), "tolerance_kwh", 0, 1000),
               "persistence_s": int(_num(data.get("persistence_s", 300), "persistence_s", 10, 86400)), "wings": {}}
        wings = data.get("wings") if isinstance(data.get("wings"), dict) else {}
        for w in WINGS:
            wc = wings.get(w) if isinstance(wings.get(w), dict) else {}
            mt = wc.get("manual_target_kwh")
            cfg["wings"][w] = {"generation_attribution_enabled": bool(wc.get("generation_attribution_enabled", True)),
                               "manual_target_kwh": None if mt is None else _num(mt, f"wings.{w}.manual_target_kwh", 0.001, 1_000_000)}
        def fn(cur):
            dev = device(cur, sid, data.get("device_id"), lock=True); did = str(dev["id"])
            if cfg["enabled"]:
                cur.execute("SELECT enabled FROM energy_meters WHERE device_id=%s AND meter_id='M1'", (did,))
                if not cur.fetchone()["enabled"]: raise HTTPException(409, "Cannot enable allocation: generation meter M1 is disabled")
            cur.execute("UPDATE pi_devices SET energy_allocation=%s, energy_config_version=energy_config_version+1 WHERE id=%s RETURNING energy_config_version", (Json(cfg), did))
            ver = cur.fetchone()["energy_config_version"]
            log_audit(cur, user, sid, "ENERGY_ALLOCATION_CONFIG", {"device_id": did, "enabled": cfg["enabled"], "tolerance_kwh": cfg["tolerance_kwh"], "persistence_s": cfg["persistence_s"], "version": ver})
            return {"success": True, "config_version": ver, "allocation": cfg}
        return run(fn, write=True)

    # Data-source setting only: never bump the Pi energy_config_version or enqueue commands.
    @router.put("/calculation-mode")
    def put_calculation_mode(data: dict, user: dict = Depends(get_current_user)):
        sid = access(user, data.get("society_id"), write=True)
        mode = data.get("mode")
        if mode not in ("AUTO", "MANUAL"):
            raise HTTPException(400, "mode must be AUTO or MANUAL")
        expected = data.get("expected_version")
        if type(expected) is not int or expected < 0:
            raise HTTPException(400, "expected_version must be a non-negative integer")
        def fn(cur):
            did = str(device(cur, sid, data.get("device_id"), ensure_meters=False)["id"])
            cur.execute("SELECT energy_calculation_mode, energy_calculation_version FROM pi_devices WHERE id=%s FOR UPDATE", (did,))
            current = cur.fetchone()
            previous, version = current["energy_calculation_mode"], current["energy_calculation_version"]
            if expected != version:
                raise HTTPException(409, "Energy calculation mode changed; refresh before saving")
            if mode != previous:
                cur.execute("""UPDATE pi_devices SET energy_calculation_mode=%s,
                               energy_calculation_version=energy_calculation_version+1 WHERE id=%s
                               RETURNING energy_calculation_version""", (mode, did))
                version = cur.fetchone()["energy_calculation_version"]
                log_audit(cur, user, sid, "ENERGY_CALCULATION_MODE", {"device_id": did, "previous_mode": previous, "mode": mode, "version": version})
            return {"success": True, "device_id": did, "mode": mode, "version": version}
        return run(fn, write=True)

    # ---------------------------------------------------------------- summary
    @router.get("/summary")
    def summary(society_id: str, device_id: str, user: dict = Depends(get_current_user)):
        sid = access(user, society_id)
        def fn(cur):
            dev = device(cur, sid, device_id); did = str(dev["id"]); meters = meters_of(cur, did)
            now = datetime.now(timezone.utc)
            calendar_today = now.date()
            pi_today = Q.device_today(cur, did, None, now=now)
            today = pi_today or calendar_today  # preserve existing read-model fallback, never use it to authorize an entry
            gen_on = bool(meters["M1"]["enabled"])
            wings = {}
            for w in WINGS:
                m = meters[METER_WING_INV[w]]
                cons_eligible = Q.consumption_meter_eligible(m)
                cons = Q.metric_periods(cur, did, Q.wing_consumption_expr(w), today, dev["reset_day"]) if cons_eligible else None
                gen = Q.metric_periods(cur, did, Q.wing_generation_expr(w), today, dev["reset_day"]) if gen_on else None
                t = Q.latest_target(cur, did, w, today)
                gen_today = gen["today"]["kwh"] if gen else None
                req = float(t["target_kwh_per_day"]) if t else None
                wings[w] = {"wing": w, "consumption_meter": {"meter_id": m["meter_id"], "enabled": m["enabled"], "comm_status": m["comm_status"], "serial": m["serial"], "last_seen": m["last_seen"], "power_kw": m["power_kw"] if cons_eligible else None},
                            "consumption": cons or {"status": "UNAVAILABLE", "reason": "consumption meter disabled" if not m["enabled"] else "consumption meter not ONLINE"},
                            "generation": gen or {"status": "UNAVAILABLE", "reason": "generation meter disabled"},
                            "required_generation": {"target_kwh_per_day": req, "adjustment_percent": float(t["adjustment_percent"]) if t else None,
                                                    "base_daily_average_kwh": float(t["base_daily_average_kwh"]) if t else None, "effective_from": t["effective_from"].isoformat() if t else None,
                                                    "generated_today_kwh": gen_today,
                                                    "achievement_percent": round(gen_today / req * 100.0, 1) if gen_today is not None and req else None,
                                                    "status": "UNAVAILABLE" if gen_today is None or req is None else ("REACHED" if gen_today >= req else "NOT_REACHED")},
                            "source": "PHYSICAL" if (cons and cons["today"]["kwh"] is not None) or (gen and gen["today"]["kwh"] is not None) else "UNAVAILABLE"}
            m1 = meters["M1"]
            gen_total = Q.metric_periods(cur, did, "generation_kwh", today, dev["reset_day"]) if gen_on else None
            unattr = Q.metric_periods(cur, did, "unattributed_generation_kwh", today, dev["reset_day"]) if gen_on else None
            attr = m1.get("attribution") or {}
            active = "FAULT" if attr.get("status") == "FAULT" else (attr.get("wing") or "UNATTRIBUTED") if gen_on and m1["comm_status"] == "ONLINE" else "UNAVAILABLE"
            generation_meter = {"meter_id": "M1", "status": "DISABLED" if not gen_on else m1["comm_status"], "serial": m1["serial"], "model": m1["model"],
                                "current_power_kw": m1["power_kw"] if gen_on and m1["comm_status"] == "ONLINE" else None,
                                "generation": gen_total or {"status": "UNAVAILABLE", "reason": "generation meter disabled"},
                                "unattributed": unattr, "active_generation_wing": active, "attribution": attr if gen_on else None,
                                "source": "PHYSICAL" if gen_total and gen_total["today"]["kwh"] is not None else "UNAVAILABLE"}
            cur.execute("SELECT clock_report FROM energy_sync_state WHERE device_id=%s", (did,))
            sync_state = cur.fetchone()
            return {"device_id": did, "as_of_operating_date": today.isoformat(), "reset_day": dev["reset_day"], "reset_period": Q.reset_period_for(today, dev["reset_day"]),
                    "clock_report": sync_state["clock_report"] if sync_state else None,
                    "target_delivery": delivery_view(cur, did, dev["energy_config_version"], now),
                    "manual_generation_operating_date": pi_today.isoformat() if pi_today is not None else None,
                    "generation_meter": generation_meter, "wings": wings,
                    "calculation": Q.calculation_view(cur, did, dev["energy_calculation_mode"], dev["energy_calculation_version"], meters, today),
                    "references": R.overview(cur, dev, meters, today, calendar_today=calendar_today)}
        return run(fn)

    # ---------------------------------------------------------------- history / graph / monthly
    def _range(cur, did, dev, frm, to, rng):
        now = datetime.now(timezone.utc)
        today = Q.device_today(cur, did, now.date(), now=now)
        if rng:
            if rng == "7d": frm, to = today - timedelta(days=6), today
            elif rng == "30d": frm, to = today - timedelta(days=29), today
            elif rng == "month": frm, to = date(today.year, today.month, 1), today
            elif rng == "reset":
                cur.execute("SELECT MIN(operating_date) AS a, MAX(operating_date) AS b FROM energy_daily WHERE device_id=%s AND reset_period=%s", (did, Q.reset_period_for(today, dev["reset_day"])))
                r = cur.fetchone(); frm, to = (r["a"] or today), (r["b"] or today)
            else: raise HTTPException(400, "range must be 7d, 30d, month or reset")
        else:
            if not frm or not to: raise HTTPException(400, "from/to (YYYY-MM-DD) or range is required")
            frm, to = _date(frm, "from"), _date(to, "to")
        if date_problem(frm, now) or date_problem(to, now): raise HTTPException(400, "Requested energy dates are outside the qualified operating-date range")
        if to < frm: raise HTTPException(400, "to must be >= from")
        if (to - frm).days + 1 > Q.MAX_DAILY_RANGE_DAYS: raise HTTPException(400, f"range limited to {Q.MAX_DAILY_RANGE_DAYS} days")
        return frm, to, today

    @router.get("/graph/comparison")
    def graph_comparison(society_id: str, device_id: str, days: int = 30, month: str = None, user: dict = Depends(get_current_user)):
        sid = access(user, society_id)
        if days not in (7, 30):
            raise HTTPException(400, "days must be 7 or 30")
        selected_month = _bill_month(month) if month is not None else None
        def fn(cur):
            dev = device(cur, sid, device_id, ensure_meters=selected_month is None); did = str(dev["id"])
            now = datetime.now(timezone.utc)
            calendar_today = now.date()
            today = Q.device_today(cur, did, calendar_today, now=now)
            if selected_month is not None:
                return C.month_overview(cur, dev, meters_of(cur, did), today, selected_month, calendar_today)
            return C.overview(cur, dev, meters_of(cur, did), today, days)
        return run(fn)

    @router.get("/graph/wing")
    def graph_wing(society_id: str, device_id: str, wing: str, range: str = None, frm: str = Query(None, alias="from"), to: str = None,
                   basis: str = "legacy", user: dict = Depends(get_current_user)):
        if basis not in ("legacy", "calculation"):
            raise HTTPException(400, "basis must be legacy or calculation")
        return _graph(society_id, device_id, wing, range, frm, to, user, basis)

    def _graph(society_id, device_id, wing, rng, frm, to, user, basis):
        sid = access(user, society_id); w = _wing(wing)
        def fn(cur):
            dev = device(cur, sid, device_id); did = str(dev["id"]); meters = meters_of(cur, did)
            f, t, today = _range(cur, did, dev, frm, to, rng)
            mode = dev["energy_calculation_mode"] if basis == "calculation" else None
            rows = Q.wing_graph_rows(cur, did, w, f, t, bool(meters["M1"]["enabled"]), Q.consumption_meter_eligible(meters[METER_WING_INV[w]]), mode)
            return {"wing": w, "from": f.isoformat(), "to": t.isoformat(), "as_of_operating_date": today.isoformat(), "unit": "kWh",
                    "basis": basis, "calculation_mode": mode,
                    "series": ["required_kwh", "generated_kwh", "consumed_kwh"], "rows": rows,
                    "generation_meter_enabled": bool(meters["M1"]["enabled"]), "consumption_meter_enabled": bool(meters[METER_WING_INV[w]]["enabled"])}
        return run(fn)

    @router.get("/history")
    def history(society_id: str, device_id: str, frm: str = Query(None, alias="from"), to: str = None, range: str = None, granularity: str = "daily", user: dict = Depends(get_current_user)):
        sid = access(user, society_id)
        if granularity not in ("daily", "monthly"): raise HTTPException(400, "granularity must be daily or monthly")
        def fn(cur):
            dev = device(cur, sid, device_id); did = str(dev["id"])
            f, t, today = _range(cur, did, dev, frm, to, range)
            if granularity == "daily":
                cur.execute("""SELECT operating_date, reset_period, status, generation_kwh, generation_source, wing_generation, unattributed_generation_kwh,
                                      fault_generation_kwh, wing_consumption, consumption_source, gap_kwh FROM energy_daily
                               WHERE device_id=%s AND operating_date BETWEEN %s AND %s ORDER BY operating_date""", (did, f, t))
                rows = [{**dict(r), "operating_date": r["operating_date"].isoformat(), "generation_kwh": float(r["generation_kwh"]) if r["generation_kwh"] is not None else None,
                         "unattributed_generation_kwh": float(r["unattributed_generation_kwh"]), "fault_generation_kwh": float(r["fault_generation_kwh"])} for r in cur.fetchall()]
            else:
                cols = ", ".join(f"SUM({Q.qualified_physical_expr(Q.wing_consumption_expr(w))}) AS cons_{w}, COUNT({Q.qualified_physical_expr(Q.wing_consumption_expr(w))}) AS cons_days_{w}, "
                                 f"SUM({Q.qualified_physical_expr(Q.wing_generation_expr(w))}) AS gen_{w}" for w in WINGS)
                value = Q.qualified_physical_expr("generation_kwh")
                cur.execute(f"""SELECT to_char(operating_date,'YYYY-MM') AS month, SUM({value}) AS generation_kwh, COUNT({value}) AS generation_days, {cols}
                                FROM energy_daily WHERE device_id=%s AND operating_date BETWEEN %s AND %s GROUP BY 1 ORDER BY 1""", (did, f, t))
                rows = []
                for r in cur.fetchall():
                    rows.append({"month": r["month"], "generation_kwh": float(r["generation_kwh"]) if r["generation_days"] else None,
                                 "wing_consumption": {w: (float(r[f"cons_{w.lower()}"]) if r[f"cons_days_{w.lower()}"] else None) for w in WINGS},
                                 "wing_generation": {w: (float(r[f"gen_{w.lower()}"]) if r[f"gen_{w.lower()}"] is not None else None) for w in WINGS}})
            return {"granularity": granularity, "from": f.isoformat(), "to": t.isoformat(), "unit": "kWh", "rows": rows, "count": len(rows)}
        return run(fn)

    @router.get("/generation/monthly")
    def generation_monthly(society_id: str, device_id: str, months: int = 6, user: dict = Depends(get_current_user)):
        sid = access(user, society_id)
        if not 1 <= months <= Q.MAX_MONTHS: raise HTTPException(400, f"months must be 1..{Q.MAX_MONTHS}")
        def fn(cur):
            dev = device(cur, sid, device_id); did = str(dev["id"]); meters = meters_of(cur, did)
            now = datetime.now(timezone.utc)
            today = Q.device_today(cur, did, now.date(), now=now)
            y, m = today.year, today.month
            for _ in range(months - 1):
                y, m = (y - 1, 12) if m == 1 else (y, m - 1)
            rows = Q.monthly_generation(cur, did, date(y, m, 1), today, today, bool(meters["M1"]["enabled"]))
            return {"meter_id": "M1", "unit": "kWh", "months": months, "as_of_operating_date": today.isoformat(), "rows": rows,
                    "source": "PHYSICAL" if meters["M1"]["enabled"] else "UNAVAILABLE"}
        return run(fn)

    # ---------------------------------------------------------------- bills / targets / adjustments
    def _bills(cur, did, w):
        cur.execute("SELECT bill_month, consumption_kwh, days, note, updated_at FROM energy_bill_history WHERE device_id=%s AND wing=%s ORDER BY bill_month DESC LIMIT 6", (did, w))
        rows = [dict(r) for r in cur.fetchall()][::-1]
        stats = Q.bill_stats(rows)
        return {"wing": w, "label": "HISTORICAL BILL REFERENCE", "unit": "kWh",
                "months": [{"month": r["bill_month"].strftime("%Y-%m"), "consumption_kwh": float(r["consumption_kwh"]), "days": r["days"], "note": r["note"]} for r in rows], **stats}

    @router.get("/bills")
    def get_bills(society_id: str, device_id: str, wing: str, end_month: str = None, user: dict = Depends(get_current_user)):
        sid = access(user, society_id); w = _wing(wing)
        end = _bill_month(end_month) if end_month else None
        return run(lambda cur: R.bill_history(cur, str(device(cur, sid, device_id, ensure_meters=False)["id"]), w, datetime.now(timezone.utc).date(), end))

    @router.put("/bills")
    def put_bills(data: dict, user: dict = Depends(get_current_user)):
        sid = access(user, data.get("society_id"), write=True); w = _wing(data.get("wing"))
        months = data.get("months")
        if not isinstance(months, list) or not 0 <= len(months) <= 12: raise HTTPException(400, "months must be a list of up to 12 entries")
        end = _bill_month(data["end_month"]) if data.get("end_month") else None
        clean, seen = {}, set()
        for e in months:
            if not isinstance(e, dict): raise HTTPException(400, "each month must be an object")
            bm = _bill_month(e.get("month"))
            if bm in seen: raise HTTPException(400, f"duplicate month {e.get('month')}")
            seen.add(bm)
            if end and not R.month_shift(end, -11) <= bm <= end: raise HTTPException(400, "month outside selected 12-month window")
            val = e.get("consumption_kwh")
            if val is None or isinstance(val, str) and not val.strip(): continue  # omitted, not deleted or zeroed
            if isinstance(val, bool): raise HTTPException(400, "consumption_kwh must be numeric, not boolean")
            days = calendar.monthrange(bm.year, bm.month)[1]
            if e.get("days") is not None and (type(e["days"]) is not int or e["days"] != days): raise HTTPException(400, "days must match the actual calendar month")
            clean[bm] = (_num(val, "consumption_kwh", 0, 10_000_000), days, str(e["note"])[:200] if e.get("note") else None)
        def fn(cur):
            did = str(device(cur, sid, data.get("device_id"), ensure_meters=False)["id"])
            cur.execute("SELECT id FROM pi_devices WHERE id=%s FOR UPDATE", (did,))
            changes = []
            for bm, (kwh, days, note) in clean.items():
                cur.execute("SELECT consumption_kwh FROM energy_bill_history WHERE device_id=%s AND wing=%s AND bill_month=%s", (did, w, bm))
                old = cur.fetchone()
                cur.execute("""INSERT INTO energy_bill_history (device_id, wing, bill_month, consumption_kwh, days, note, created_by, updated_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (device_id, wing, bill_month) DO UPDATE SET consumption_kwh=EXCLUDED.consumption_kwh,
                    days=EXCLUDED.days, note=COALESCE(EXCLUDED.note, energy_bill_history.note), updated_by=EXCLUDED.updated_by, updated_at=NOW()""",
                    (did, w, bm, kwh, days, note, user.get("id"), user.get("id")))
                changes.append({"month": bm.strftime("%Y-%m"), "previous_kwh": float(old["consumption_kwh"]) if old else None, "kwh": kwh})
            if changes: log_audit(cur, user, sid, "ENERGY_BILL_HISTORY", {"device_id": did, "wing": w, "semantics": "MONTH_UPSERT", "changes": changes})
            return {"success": True, "updated_months": len(clean), **R.bill_history(cur, did, w, datetime.now(timezone.utc).date(), end)}
        return run(fn, write=True)

    @router.put("/grid-reference")
    def put_grid_reference(data: dict, user: dict = Depends(get_current_user)):
        sid = access(user, data.get("society_id"), write=True)
        enabled, expected = data.get("enabled"), data.get("expected_version")
        if type(enabled) is not bool: raise HTTPException(400, "enabled must be true or false")
        if type(expected) is not int or expected < 0: raise HTTPException(400, "expected_version must be a non-negative integer")
        raw = data.get("limit_kwh_day")
        if isinstance(raw, bool): raise HTTPException(400, "limit_kwh_day must be numeric")
        limit = _num(raw, "limit_kwh_day", 0, 10_000_000) if raw is not None else None
        if enabled and limit is None: raise HTTPException(400, "limit_kwh_day is required when enabled")
        def fn(cur):
            did = str(device(cur, sid, data.get("device_id"), ensure_meters=False)["id"])
            cur.execute("SELECT grid_export_enabled, grid_export_limit_kwh, grid_reference_version FROM pi_devices WHERE id=%s FOR UPDATE", (did,))
            old = cur.fetchone()
            if old["grid_reference_version"] != expected: raise HTTPException(409, "Grid reference changed; refresh before saving")
            if old["grid_export_enabled"] != enabled or (float(old["grid_export_limit_kwh"]) if old["grid_export_limit_kwh"] is not None else None) != limit:
                cur.execute("""UPDATE pi_devices SET grid_export_enabled=%s, grid_export_limit_kwh=%s,
                    grid_reference_version=grid_reference_version+1 WHERE id=%s RETURNING grid_reference_version""", (enabled, limit, did))
                expected_new = cur.fetchone()["grid_reference_version"]
                log_audit(cur, user, sid, "ENERGY_GRID_REFERENCE", {"device_id": did, "enabled": enabled, "limit_kwh_day": limit, "version": expected_new,
                    "previous_enabled": old["grid_export_enabled"], "previous_limit_kwh_day": float(old["grid_export_limit_kwh"]) if old["grid_export_limit_kwh"] is not None else None})
            else: expected_new = expected
            return {"success": True, "device_id": did, "enabled": enabled, "limit_kwh_day": limit, "version": expected_new}
        return run(fn, write=True)

    @router.get("/targets")
    def get_targets(society_id: str, device_id: str, wing: str, user: dict = Depends(get_current_user)):
        sid = access(user, society_id); w = _wing(wing)
        def fn(cur):
            dev = device(cur, sid, device_id, ensure_meters=False); did = str(dev["id"])
            now = datetime.now(timezone.utc)
            as_of = Q.device_today(cur, did, now.date(), now=now)
            cur.execute("SELECT * FROM energy_generation_targets WHERE device_id=%s AND wing=%s ORDER BY effective_from DESC, id DESC LIMIT 20", (did, w))
            rows = [_target_row(r) for r in cur.fetchall()]
            current = Q.latest_target(cur, did, w, as_of)
            return {"wing": w, "current": _target_row(current) if current else None, "history": rows, "bills": _bills(cur, did, w),
                    "as_of_operating_date": as_of.isoformat(), "delivery": delivery_view(cur, did, dev["energy_config_version"], now)}
        return run(fn)

    @router.post("/targets")
    def post_target(data: dict, user: dict = Depends(get_current_user)):
        sid = access(user, data.get("society_id"), write=True); w = _wing(data.get("wing"))
        pct = _num(data.get("adjustment_percent"), "adjustment_percent", -100, 500)
        eff = _date(data["effective_from"], "effective_from") if data.get("effective_from") else datetime.now(timezone.utc).date()
        def fn(cur):
            dev = device(cur, sid, data.get("device_id"), ensure_meters=False, lock=True); did = str(dev["id"])
            now = datetime.now(timezone.utc)
            as_of = Q.device_today(cur, did, now.date(), now=now)
            bills = _bills(cur, did, w)
            if not bills["months"] or bills["daily_average_kwh"] is None: raise HTTPException(409, f"No bill history for wing {w}: enter the six-month bills first")
            target = _num(Q.compute_target(bills["daily_average_kwh"], pct), "target_kwh_per_day", 0)
            basis = {"months": [m["month"] for m in bills["months"]], "total_kwh": bills["total_kwh"], "days": bills["days"], "formula": "daily_average x (1 + adjustment_percent/100)"}
            cur.execute("""INSERT INTO energy_generation_targets (device_id, wing, base_daily_average_kwh, adjustment_percent, target_kwh_per_day, basis, effective_from, reason, created_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""", (did, w, bills["daily_average_kwh"], pct, target, Json(basis), eff, (str(data.get("reason"))[:300] if data.get("reason") else None), user.get("id")))
            row = _target_row(cur.fetchone())
            _, version = reconcile_targets(cur, did, as_of, dev["energy_config_version"], force=True)
            log_audit(cur, user, sid, "ENERGY_TARGET", {"device_id": did, "wing": w, "adjustment_percent": pct, "target_kwh_per_day": target, "effective_from": eff.isoformat(), "energy_config_version": version})
            return {"success": True, "target": row, "config_version": version, "delivery_status": "PENDING_CONTROLLER_REPORT",
                    "as_of_operating_date": as_of.isoformat()}
        return run(fn, write=True)

    @router.post("/adjustments")
    def post_adjustment(data: dict, user: dict = Depends(get_current_user)):
        sid = access(user, data.get("society_id"), write=True)
        kind = str(data.get("kind") or "").upper()
        if kind not in KINDS: raise HTTPException(400, f"kind must be one of {sorted(KINDS)}")
        w = _wing(data.get("wing")) if kind != "ACCOUNTING" or data.get("wing") else None
        od = _date(data.get("operating_date"), "operating_date"); val = _num(data.get("value_kwh"), "value_kwh", -1_000_000, 1_000_000)
        reason = str(data.get("reason") or "").strip()
        if not reason: raise HTTPException(400, "reason is required for manual entries")
        if data.get("unit", "kWh") != "kWh": raise HTTPException(400, "unit must be kWh")
        def fn(cur):
            did = str(device(cur, sid, data.get("device_id"), ensure_meters=(kind != "MANUAL_GENERATION"))["id"])
            if kind == "MANUAL_GENERATION":
                now = datetime.now(timezone.utc)
                qualified_day = Q.device_today(cur, did, None, now=now)
                # Same latest-OPEN ordering as device_today, with no date fallback.
                # Hold a read lock on this record until insert/audit commit so its
                # OPEN state cannot be closed concurrently during acceptance.
                cur.execute("""SELECT operating_date FROM energy_daily WHERE device_id=%s AND status='OPEN'
                               ORDER BY operating_date DESC LIMIT 1 FOR SHARE""", (did,))
                opened = cur.fetchone()
                if not opened or opened["operating_date"] is None:
                    raise HTTPException(409, "Authoritative OPEN Pi operating day is unavailable; manual generation cannot be recorded")
                require_qualified_open(opened["operating_date"], now)
                if opened["operating_date"] != qualified_day:
                    raise HTTPException(409, "Pi operating day changed; refresh before entering generation")
                if od != opened["operating_date"]:
                    raise HTTPException(409, f"Manual generation is allowed only for current Pi operating day {opened['operating_date'].isoformat()}; refresh before entering generation")
            cur.execute("""INSERT INTO energy_adjustments (device_id, wing, operating_date, kind, value_kwh, reason, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id, created_at""",
                        (did, w, od, kind, val, reason[:300], user.get("id")))
            r = cur.fetchone()
            log_audit(cur, user, sid, "ENERGY_ADJUSTMENT", {"device_id": did, "id": r["id"], "wing": w, "operating_date": od.isoformat(), "kind": kind, "value_kwh": val})
            return {"success": True, "adjustment": {"id": r["id"], "wing": w, "operating_date": od.isoformat(), "kind": kind, "value_kwh": val, "unit": "kWh", "source": "MANUAL", "reason": reason, "created_at": r["created_at"].isoformat()}}
        return run(fn, write=True)

    @router.get("/adjustments")
    def get_adjustments(society_id: str, device_id: str, limit: int = 50, user: dict = Depends(get_current_user)):
        sid = access(user, society_id)
        if not 1 <= limit <= 200: raise HTTPException(400, "limit must be 1..200")
        def fn(cur):
            did = str(device(cur, sid, device_id)["id"])
            cur.execute("SELECT a.*, u.email FROM energy_adjustments a LEFT JOIN users u ON u.id=a.created_by WHERE a.device_id=%s ORDER BY a.operating_date DESC, a.id DESC LIMIT %s", (did, limit))
            return {"rows": [{"id": r["id"], "wing": r["wing"], "operating_date": r["operating_date"].isoformat(), "kind": r["kind"], "value_kwh": float(r["value_kwh"]), "unit": r["unit"],
                              "source": "MANUAL", "reason": r["reason"], "created_by": r["email"], "created_at": r["created_at"].isoformat()} for r in cur.fetchall()]}
        return run(fn)

    return router


METER_WING_INV = {v: k for k, v in METER_WING.items() if v}


def _bill_month(value):
    try:
        month = datetime.strptime(str(value), "%Y-%m").date()
        if month.strftime("%Y-%m") != value or month > datetime.now(timezone.utc).date().replace(day=1) or month.year < 1971:
            raise ValueError()
        return month
    except (ValueError, TypeError):
        raise HTTPException(400, "month must be YYYY-MM, from 1971 through the current month")


def _target_row(r):
    return {"id": r["id"], "wing": r["wing"], "base_daily_average_kwh": float(r["base_daily_average_kwh"]), "adjustment_percent": float(r["adjustment_percent"]),
            "target_kwh_per_day": float(r["target_kwh_per_day"]), "basis": r["basis"], "effective_from": r["effective_from"].isoformat(), "reason": r["reason"],
            "created_at": r["created_at"].isoformat()}


__all__ = ["create_router", "METER_ROLE"]
