"""Aggregations over the authoritative energy_daily ledger (daily -> monthly/yearly/reset/lifetime).
NULL means UNAVAILABLE and is never coerced to 0; a period without any measured day returns None."""
import calendar
import math
from datetime import date, timedelta

WINGS = ("A", "B", "C", "D")
MAX_DAILY_RANGE_DAYS = 400
MAX_MONTHS = 60


def consumption_meter_eligible(meter):
    """Current physical consumption uses the Pi's existing per-meter ONLINE gate."""
    return bool(meter and meter.get("enabled") and meter.get("comm_status") == "ONLINE")


def reset_period_for(d, reset_day):
    reset_day = max(1, min(28, int(reset_day or 15)))
    if d.day >= reset_day:
        return f"{d.year:04d}-{d.month:02d}"
    y, m = (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)
    return f"{y:04d}-{m:02d}"


def month_bounds(y, m):
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def device_today(cur, device_id, fallback):
    """Operating 'today' = the Pi's latest OPEN ledger day (Pi-local date), else the fallback date."""
    cur.execute("SELECT MAX(operating_date) AS d FROM energy_daily WHERE device_id=%s AND status='OPEN'", (device_id,))
    row = cur.fetchone()
    return row["d"] if row and row["d"] else fallback


def period_ranges(today, reset_day):
    prev_y, prev_m = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    return {
        "today": (today, today),
        "yesterday": (today - timedelta(days=1), today - timedelta(days=1)),
        "this_month": (date(today.year, today.month, 1), today),
        "previous_month": month_bounds(prev_y, prev_m),
        "this_year": (date(today.year, 1, 1), today),
        "lifetime": (date(1970, 1, 1), today),
    }


def qualified_physical_expr(expr):
    """Whitelist internal metrics and qualify each value BEFORE aggregation.

PostgreSQL numeric NaN sorts above Infinity; [0, Infinity) therefore excludes
NaN, infinities, negatives and NULL while retaining genuine physical zero.
    """
    if expr in ("generation_kwh", "unattributed_generation_kwh", "fault_generation_kwh") or expr in {wing_generation_expr(w) for w in WINGS}:
        source = "generation_source"
    else:
        sources = {wing_consumption_expr(w): f"consumption_source->>'{w}'" for w in WINGS}
        if expr not in sources:
            raise ValueError("Unsupported physical energy metric")
        source = sources[expr]
    return f"(CASE WHEN {source}='PHYSICAL' AND {expr}>=0 AND {expr}<'Infinity'::numeric THEN {expr} END)"


def _agg(cur, device_id, expr, frm, to, reset_period=None):
    where = "device_id=%s AND operating_date BETWEEN %s AND %s" if reset_period is None else "device_id=%s AND reset_period=%s"
    params = (device_id, frm, to) if reset_period is None else (device_id, reset_period)
    value = qualified_physical_expr(expr)
    cur.execute(f"SELECT SUM({value}) AS total, COUNT({value}) AS days FROM energy_daily WHERE {where}", params)
    r = cur.fetchone()
    return (float(r["total"]) if r["days"] else None), int(r["days"] or 0)


def metric_periods(cur, device_id, expr, today, reset_day):
    """expr = SQL numeric expression over energy_daily -> {period: {kwh, days, status}}."""
    out = {}
    for name, (frm, to) in period_ranges(today, reset_day).items():
        total, days = _agg(cur, device_id, expr, frm, to)
        out[name] = {"kwh": round(total, 3) if total is not None else None, "days": days, "status": "PHYSICAL" if total is not None else "UNAVAILABLE"}
    total, days = _agg(cur, device_id, expr, None, None, reset_period=reset_period_for(today, reset_day))
    out["reset_period"] = {"kwh": round(total, 3) if total is not None else None, "days": days, "status": "PHYSICAL" if total is not None else "UNAVAILABLE",
                           "period": reset_period_for(today, reset_day)}
    return out


def wing_consumption_expr(w):
    return f"(wing_consumption->>'{w}')::numeric"


def wing_generation_expr(w):
    return f"(wing_generation->>'{w}')::numeric"


# ---------------------------------------------------------------- bills / targets
def bill_stats(rows):
    """rows: [{bill_month: date, consumption_kwh, days|None}] -> total, actual days, daily average (None if no rows)."""
    if not rows:
        return {"months_count": 0, "total_kwh": None, "days": 0, "daily_average_kwh": None}
    total = sum(float(r["consumption_kwh"]) for r in rows)
    days = sum(int(r["days"]) if r.get("days") else calendar.monthrange(r["bill_month"].year, r["bill_month"].month)[1] for r in rows)
    return {"months_count": len(rows), "total_kwh": round(total, 3), "days": days, "daily_average_kwh": round(total / days, 4) if days else None}


def compute_target(daily_average_kwh, adjustment_percent):
    return round(float(daily_average_kwh) * (1.0 + float(adjustment_percent) / 100.0), 4)


def latest_target(cur, device_id, wing, as_of):
    cur.execute("""SELECT * FROM energy_generation_targets WHERE device_id=%s AND wing=%s AND effective_from<=%s
                   ORDER BY effective_from DESC, id DESC LIMIT 1""", (device_id, wing, as_of))
    return cur.fetchone()


def targets_timeline(cur, device_id, wing, frm, to):
    """All target rows relevant to [frm, to] (latest before frm + changes inside)."""
    cur.execute("""SELECT id, base_daily_average_kwh, adjustment_percent, target_kwh_per_day, effective_from FROM energy_generation_targets
                   WHERE device_id=%s AND wing=%s AND effective_from<=%s ORDER BY effective_from ASC, id ASC""", (device_id, wing, to))
    return cur.fetchall()


def target_for_date(timeline, d):
    chosen = None
    for t in timeline:
        if t["effective_from"] <= d:
            chosen = t
    return float(chosen["target_kwh_per_day"]) if chosen else None


# ---------------------------------------------------------------- graph rows (aligned by operating_date)
def wing_graph_rows(cur, device_id, wing, frm, to, generation_enabled, consumption_enabled, calculation_mode=None):
    """Legacy accounting mix by default; explicit calculation mode selects ONE source.

The selected-source view shares this query pipeline, never rewrites the ledger,
and never treats manual entries as editable absolute daily totals.
    """
    cur.execute("""SELECT operating_date, status, (wing_generation->>%s)::numeric AS gen, (wing_consumption->>%s)::numeric AS cons,
                          consumption_source->>%s AS cons_src, generation_source
                   FROM energy_daily WHERE device_id=%s AND operating_date BETWEEN %s AND %s ORDER BY operating_date""",
                (wing, wing, wing, device_id, frm, to))
    days = {r["operating_date"]: r for r in cur.fetchall()}
    cur.execute("""SELECT operating_date, kind, SUM(value_kwh) AS v FROM energy_adjustments
                   WHERE device_id=%s AND wing=%s AND operating_date BETWEEN %s AND %s GROUP BY operating_date, kind""", (device_id, wing, frm, to))
    adj = {}
    for r in cur.fetchall():
        adj.setdefault(r["operating_date"], {})[r["kind"]] = float(r["v"])
    timeline = targets_timeline(cur, device_id, wing, frm, to)
    rows = []
    d = frm
    while d <= to:
        r = days.get(d)
        a = adj.get(d, {})
        gen_phys = float(r["gen"]) if r and r["gen"] is not None and generation_enabled else None
        cons_phys = float(r["cons"]) if r and r["cons"] is not None and consumption_enabled else None
        gen_phys = gen_phys if r and r["generation_source"] == "PHYSICAL" and _physical(gen_phys) else None
        cons_phys = cons_phys if r and r["cons_src"] == "PHYSICAL" and _physical(cons_phys) else None
        if calculation_mode is not None:
            if calculation_mode == "AUTO":
                a = {}  # No automatic substitution or addition of manual entries.
            elif calculation_mode == "MANUAL":
                gen_phys = cons_phys = None
            else:
                raise ValueError("Unknown energy calculation mode")
        gen = _combine(gen_phys, a.get("MANUAL_GENERATION"))
        cons = _combine(cons_phys, a.get("MANUAL_CONSUMPTION"))
        req = target_for_date(timeline, d) if calculation_mode != "MANUAL" else None
        gen_src, cons_src = _src(gen_phys, a.get("MANUAL_GENERATION")), _src(cons_phys, a.get("MANUAL_CONSUMPTION"))
        sources = {s for s in (gen_src, cons_src) if s != "UNAVAILABLE"}
        source = "UNAVAILABLE" if not sources else ("MIXED" if len(sources) > 1 or "MIXED" in sources else sources.pop())
        ach = round(gen / req * 100.0, 1) if gen is not None and req else None
        status = "UNAVAILABLE" if gen is None or req is None else ("REACHED" if gen >= req else "NOT_REACHED")
        rows.append({"date": d.isoformat(), "generated_kwh": _r(gen), "consumed_kwh": _r(cons), "required_kwh": _r(req),
                     "generation_minus_consumption_kwh": _r(gen - cons) if gen is not None and cons is not None else None,
                     "target_achievement_percent": ach, "target_status": status, "source": source,
                     "generation_source": gen_src, "consumption_source": cons_src,
                     "day_status": ("MANUAL_ENTRIES" if source != "UNAVAILABLE" else "NO_DATA") if calculation_mode == "MANUAL" else r["status"] if r else "NO_DATA"})
        d += timedelta(days=1)
    return rows


def _physical(value):
    return value is not None and math.isfinite(value) and value >= 0


def calculation_view(cur, device_id, mode, version, meters, today):
    """Bounded seven-day generation view using the existing graph calculation.

Common generation is intentionally NOT summed from manual wing entries: there
is no authoritative common manual-generation measurement in this contract.
    """
    wings = {}
    for wing, mid in zip(WINGS, ("M2", "M3", "M4", "M5")):
        rows = wing_graph_rows(cur, device_id, wing, today - timedelta(days=6), today,
                              bool(meters["M1"]["enabled"]), consumption_meter_eligible(meters.get(mid)), mode)
        wings[wing] = {"wing": wing, "today": rows[-1],
                       "generation_trend": [{k: r[k] for k in ("date", "generated_kwh", "generation_source")} for r in rows]}
    return {"mode": mode, "version": version, "operating_date": today.isoformat(), "wings": wings,
            "manual_entry_semantics": "ADDITIVE_ENTRIES", "common_generation_basis": "PHYSICAL_M1_ONLY"}


def _combine(phys, manual):
    if phys is None and manual is None:
        return None
    return (phys or 0.0) + (manual or 0.0)


def _src(phys, manual):
    if phys is not None and manual is not None:
        return "MIXED"
    if phys is not None:
        return "PHYSICAL"
    if manual is not None:
        return "MANUAL"
    return "UNAVAILABLE"


def _r(v):
    return round(v, 3) if v is not None else None


# ---------------------------------------------------------------- monthly generation (generation only)
def monthly_generation(cur, device_id, frm_month, to_month, today, generation_enabled):
    value = qualified_physical_expr("generation_kwh")
    cur.execute(f"""SELECT to_char(operating_date, 'YYYY-MM') AS month, SUM({value}) AS kwh,
                          COUNT({value}) AS physical_days, COUNT(*) AS rows
                   FROM energy_daily WHERE device_id=%s AND operating_date BETWEEN %s AND %s GROUP BY 1 ORDER BY 1""",
                (device_id, frm_month, month_bounds(to_month.year, to_month.month)[1]))
    got = {r["month"]: r for r in cur.fetchall()}
    out = []
    y, m = frm_month.year, frm_month.month
    while (y, m) <= (to_month.year, to_month.month):
        key = f"{y:04d}-{m:02d}"
        r = got.get(key)
        first, last = month_bounds(y, m)
        expected = (min(last, today) - first).days + 1 if first <= today else 0
        phys_days = int(r["physical_days"]) if r else 0
        kwh = float(r["kwh"]) if r and r["physical_days"] and generation_enabled else None
        completeness = "UNAVAILABLE" if kwh is None else ("COMPLETE" if phys_days >= expected and expected > 0 else "PARTIAL")
        out.append({"month": key, "generation_kwh": _r(kwh), "source": "PHYSICAL" if kwh is not None else "UNAVAILABLE",
                    "completeness": completeness, "physical_days": phys_days, "expected_days": expected})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out
