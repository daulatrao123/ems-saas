"""Historical demand and sequential allocation REFERENCES. No physical-control writes.

Bill rates use actual calendar days and an unweighted arithmetic mean. Existing
target snapshots remain authoritative; neither bills nor grid settings edit them.
"""
import calendar
import math
from datetime import date, timedelta
from decimal import Decimal

from . import queries as Q

FORMULA = "SUM(monthly_kwh / actual_calendar_days) / valid_months"


def valid_number(value):
    return value is not None and math.isfinite(float(value)) and float(value) >= 0


def rounded(value):
    return round(float(value), 4) if value is not None else None


def month_start(value):
    return value.replace(day=1)


def month_shift(value, offset):
    index = value.year * 12 + value.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def historical_stats(rows):
    valid = [r for r in rows if valid_number(r.get("consumption_kwh"))]
    rates = [float(r["consumption_kwh"]) / calendar.monthrange(r["bill_month"].year, r["bill_month"].month)[1] for r in valid]
    return {"reference_daily_kwh": rounded(sum(rates) / len(rates)) if rates else None,
            "valid_months": len(rates), "source": "HISTORICAL" if rates else "UNAVAILABLE", "formula": FORMULA}


def bill_history(cur, did, wing, today, end=None):
    # Default to latest entered non-future month, or last complete calendar month.
    # Older rows are retained, and remain accessible through end_month windows.
    if end is None:
        cur.execute("SELECT MAX(bill_month) AS month FROM energy_bill_history WHERE device_id=%s AND wing=%s AND bill_month<=%s", (did, wing, month_start(today)))
        end = cur.fetchone()["month"] or month_shift(month_start(today), -1)
    start = month_shift(end, -11)
    cur.execute("""SELECT bill_month, consumption_kwh, note, source, created_by, updated_by, created_at, updated_at
                   FROM energy_bill_history WHERE device_id=%s AND wing=%s AND bill_month BETWEEN %s AND %s ORDER BY bill_month""", (did, wing, start, end))
    rows = list(cur.fetchall())
    by_month = {r["bill_month"]: r for r in rows}
    months = []
    for n in range(12):
        month = month_shift(start, n)
        row = by_month.get(month)
        days = calendar.monthrange(month.year, month.month)[1]
        value = float(row["consumption_kwh"]) if row and valid_number(row["consumption_kwh"]) else None
        months.append({"month": month.strftime("%Y-%m"), "month_name": month.strftime("%B %Y"),
                       "days": days, "consumption_kwh": value, "daily_kwh": rounded(value / days) if value is not None else None,
                       "source": "HISTORICAL" if value is not None else "UNAVAILABLE", "note": row["note"] if row else None,
                       "created_by": row["created_by"] if row else None, "updated_by": row["updated_by"] if row else None,
                       "updated_at": row["updated_at"].isoformat() if row else None})
    stats = historical_stats(rows)
    latest = max((r["bill_month"] for r in rows if valid_number(r["consumption_kwh"])), default=None)
    return {"device_id": did, "wing": wing, "label": "Historical Consumption Baseline", "unit": "kWh",
            "end_month": end.strftime("%Y-%m"), "months": months, "months_count": stats["valid_months"],
            "daily_average_kwh": stats["reference_daily_kwh"], **stats,
            "latest_history_end": Q.month_bounds(latest.year, latest.month)[1].isoformat() if latest else None}


def wing_reference(cur, did, wing, today, meter_enabled, *, calendar_today):
    # Historical bills follow the same UTC calendar as GET/PUT bills, even if
    # the Pi's operating day is stale. Physical queries below still use today.
    history = bill_history(cur, did, wing, calendar_today)
    effective = {"daily_kwh": history["reference_daily_kwh"], "source": history["source"], "operating_date": None}
    # A partial OPEN day is not a completed daily demand reference. Prefer a newer
    # CLOSED physical day only, under the existing per-meter enablement semantics.
    if meter_enabled:
        cur.execute("""SELECT operating_date, (wing_consumption->>%s)::numeric AS kwh FROM energy_daily
            WHERE device_id=%s AND status='CLOSED' AND operating_date<%s
              AND (%s::date IS NULL OR operating_date>%s::date)
              AND consumption_source->>%s='PHYSICAL' AND (wing_consumption->>%s)::numeric>=0
            ORDER BY operating_date DESC LIMIT 1""",
                    (wing, did, today, history["latest_history_end"], history["latest_history_end"], wing, wing))
        actual = cur.fetchone()
        if actual and valid_number(actual["kwh"]):
            effective = {"daily_kwh": rounded(actual["kwh"]), "source": "PHYSICAL", "operating_date": actual["operating_date"].isoformat()}
    return {"history": history, "effective": effective}


def allocation_reference(generation, targets, enabled, limit):
    """A→B→C→D waterfall; missing quota blocks downstream allocation, never zero demand."""
    generation = Decimal(str(generation)) if valid_number(generation) else None
    quotas = {w: Decimal(str(targets[w])) if valid_number(targets.get(w)) else None for w in Q.WINGS}
    all_known = all(v is not None for v in quotas.values())
    required = sum(quotas.values()) if all_known else None
    remaining = generation
    wings = []
    for wing, quota in quotas.items():
        allocated = min(remaining, quota) if remaining is not None and quota is not None else None
        unmet = max(quota - allocated, 0) if allocated is not None else None
        wings.append({"wing": wing, "required_kwh": rounded(quota), "allocated_kwh": rounded(allocated),
                      "unmet_kwh": rounded(unmet), "status": "UNAVAILABLE" if allocated is None else "SATISFIED" if unmet == 0 else "UNMET"})
        remaining = remaining - allocated if allocated is not None else None
    satisfied = all_known and generation is not None and generation >= required
    excess = max(generation - required, 0) if generation is not None and all_known else None
    unmet = max(required - generation, 0) if generation is not None and all_known else None
    grid = Decimal("0")
    if generation is None:
        status, reason = "UNAVAILABLE", "SOCIETY_GENERATION_UNAVAILABLE"
    elif not all_known:
        status, reason = "UNAVAILABLE", "REQUIRED_WING_QUOTA_UNAVAILABLE"
    elif not satisfied:
        status, reason = "NOT_ELIGIBLE", "WING_QUOTAS_UNMET"
    elif not enabled:
        status, reason = "NOT_ELIGIBLE", "GRID_EXPORT_DISABLED"
    elif not valid_number(limit):
        status, reason = "UNAVAILABLE", "GRID_LIMIT_UNAVAILABLE"
    elif excess <= 0 or float(limit) == 0:
        status, reason = "NOT_ELIGIBLE", "NO_POSITIVE_EXCESS" if excess <= 0 else "GRID_LIMIT_ZERO"
    else:
        grid = min(excess, Decimal(str(limit)))
        status, reason = "ELIGIBLE", "REFERENCE_ONLY"
    return {"label": "Allocation reference — not live dispatch", "generation_kwh": rounded(generation),
            "wings": wings, "required_kwh": rounded(required), "all_quotas_known": all_known,
            "all_quotas_satisfied": satisfied, "unmet_kwh": rounded(unmet), "excess_kwh": rounded(excess),
            "grid_allocation_kwh": rounded(grid), "unassigned_excess_kwh": rounded(excess - grid) if excess is not None else None,
            "status": status, "reason": reason}


def overview(cur, dev, meters, today, *, calendar_today):
    did = str(dev["id"])
    wings = {w: wing_reference(cur, did, w, today, meters[mid]["enabled"], calendar_today=calendar_today) for w, mid in zip(Q.WINGS, ("M2", "M3", "M4", "M5"))}
    history = [wings[w]["history"]["reference_daily_kwh"] for w in Q.WINGS]
    effective = [wings[w]["effective"]["daily_kwh"] for w in Q.WINGS]
    all_history, all_effective = all(v is not None for v in history), all(v is not None for v in effective)
    sources = {wings[w]["effective"]["source"] for w in Q.WINGS}
    cur.execute("SELECT generation_kwh, generation_source FROM energy_daily WHERE device_id=%s AND operating_date=%s", (did, today))
    measured = cur.fetchone()
    generation = measured["generation_kwh"] if dev["energy_calculation_mode"] == "AUTO" and meters["M1"]["enabled"] and measured and measured["generation_source"] == "PHYSICAL" else None
    targets = {}
    for w in Q.WINGS:
        row = Q.latest_target(cur, did, w, today)
        targets[w] = row["target_kwh_per_day"] if row else None
    grid = {"enabled": dev["grid_export_enabled"], "limit_kwh_day": rounded(dev["grid_export_limit_kwh"]), "version": dev["grid_reference_version"]}
    allocation = allocation_reference(generation, targets, grid["enabled"], grid["limit_kwh_day"])
    allocation["generation_source"] = "PHYSICAL" if allocation["generation_kwh"] is not None else "UNAVAILABLE"
    allocation["quota_source"] = "energy_generation_targets.target_kwh_per_day"
    return {"wings": wings, "society_historical_daily_kwh": rounded(sum(history)) if all_history else None,
            "society_reference_daily_kwh": rounded(sum(effective)) if all_effective else None,
            "society_reference_source": next(iter(sources)) if all_effective and len(sources) == 1 else "MIXED_REFERENCE" if all_effective else "UNAVAILABLE",
            "grid": grid, "allocation": allocation}