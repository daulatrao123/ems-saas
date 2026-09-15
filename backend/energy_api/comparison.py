"""Mode-aware presentation: physical generation vs metered/bill-derived consumption.

Monthly bills are daily REFERENCES for their own calendar month, never measured
consumption. Legacy adjustment/accounting views and allocation quotas stay separate.
"""
import calendar
from datetime import date, timedelta

from . import queries as Q
from .consumption_scope import consumption_scope


def balance(generation, consumption):
    return round(generation - consumption, 4) if generation is not None and consumption is not None else None


def bill_rates(cur, did, wing, frm, to):
    cur.execute("""SELECT bill_month, consumption_kwh FROM energy_bill_history
                   WHERE device_id=%s AND wing=%s AND bill_month BETWEEN %s AND %s ORDER BY bill_month""",
                (did, wing, frm.replace(day=1), to.replace(day=1)))
    return {r["bill_month"]: float(r["consumption_kwh"]) / calendar.monthrange(r["bill_month"].year, r["bill_month"].month)[1]
            for r in cur.fetchall() if r["consumption_kwh"] is not None and Q._physical(float(r["consumption_kwh"]))}


def wing_rows(cur, did, wing, mid, meters, frm, to, mode):
    # Always preserve actual M1-attributed wing generation; never substitute
    # manual generation or split society generation across wings.
    rows = Q.wing_graph_rows(cur, did, wing, frm, to, bool(meters["M1"]["enabled"]),
                            Q.consumption_meter_eligible(meters.get(mid)), "AUTO")
    rates = bill_rates(cur, did, wing, frm, to) if mode == "MANUAL" else {}
    for row in rows:
        if mode == "MANUAL":
            consumption = rates.get(date.fromisoformat(row["date"]).replace(day=1))
            row["consumed_kwh"] = round(consumption, 4) if consumption is not None else None
            row["consumption_source"] = "HISTORICAL" if consumption is not None else "UNAVAILABLE"
        row["generation_minus_consumption_kwh"] = balance(row["generated_kwh"], row["consumed_kwh"])
        row["bill_month"] = row["date"][:7] if mode == "MANUAL" and row["consumed_kwh"] is not None else None
        row["generation_reason"] = None if row["generated_kwh"] is not None else "M1_DISABLED" if not meters["M1"]["enabled"] else "PHYSICAL_GENERATION_UNAVAILABLE"
        row["consumption_reason"] = None if row["consumed_kwh"] is not None else "BILL_NOT_ENTERED_FOR_MONTH" if mode == "MANUAL" else "CONSUMPTION_METER_UNAVAILABLE"
        row["balance_reason"] = row["generation_reason"] or row["consumption_reason"]
        sources = {row[k] for k in ("generation_source", "consumption_source")} - {"UNAVAILABLE"}
        row["source"] = "MIXED_REFERENCE" if len(sources) > 1 else next(iter(sources), "UNAVAILABLE")
    return rows


def overview(cur, dev, meters, today, days):
    did, mode = str(dev["id"]), dev["energy_calculation_mode"]
    frm = today - timedelta(days=days - 1)
    scope = consumption_scope(cur, did)
    included = scope["included_wings"]
    wings = {}
    for wing, mid in zip(Q.WINGS, ("M2", "M3", "M4", "M5")):
        rows = wing_rows(cur, did, wing, mid, meters, frm, today, mode)
        wings[wing] = {"wing": wing, "today": rows[-1], "rows": rows}
    cur.execute("""SELECT operating_date, generation_kwh, generation_source FROM energy_daily
                   WHERE device_id=%s AND operating_date BETWEEN %s AND %s ORDER BY operating_date""", (did, frm, today))
    measured = {r["operating_date"].isoformat(): r for r in cur.fetchall()}
    society = []
    for i in range(days):
        day = (frm + timedelta(days=i)).isoformat()
        row = measured.get(day)
        gen = float(row["generation_kwh"]) if meters["M1"]["enabled"] and row and row["generation_source"] == "PHYSICAL" and row["generation_kwh"] is not None else None
        gen = round(gen, 4) if Q._physical(gen) else None
        consumption = [wings[w]["rows"][i]["consumed_kwh"] for w in included]
        cons = round(sum(consumption), 4) if included and all(v is not None for v in consumption) else None
        missing = [w for w in included if wings[w]["rows"][i]["consumed_kwh"] is None]
        generation_reason = None if gen is not None else "M1_DISABLED" if not meters["M1"]["enabled"] else "PHYSICAL_GENERATION_UNAVAILABLE"
        consumption_reason = "NO_ENABLED_WINGS" if not included else "INCOMPLETE_WING_CONSUMPTION" if missing else None
        society.append({"date": day, "generated_kwh": gen, "generation_source": "PHYSICAL" if gen is not None else "UNAVAILABLE",
                        "consumed_kwh": cons, "consumption_source": ("HISTORICAL" if mode == "MANUAL" else "PHYSICAL") if cons is not None else "UNAVAILABLE",
                        "generation_minus_consumption_kwh": balance(gen, cons), "generation_reason": generation_reason,
                        "consumption_reason": consumption_reason, "balance_reason": generation_reason or consumption_reason,
                        "missing_consumption_wings": missing})
    return {"device_id": did, "mode": mode, "version": dev["energy_calculation_version"], "operating_date": today.isoformat(),
            "consumption_basis": "MONTHLY_BILL_DAILY_REFERENCE" if mode == "MANUAL" else "PHYSICAL_CONSUMPTION_METERS",
            "wings": wings, "society": {"today": society[-1], "rows": society}, **scope}


def month_overview(cur, dev, meters, operating_day, month, calendar_today):
    """A selected calendar month, never a replacement for the Pi operating day.

    Current-month rows stop at UTC today; daily reference still divides by the
    full calendar month. Past months include every day, independent of Pi age.
    """
    end = min(date(month.year, month.month, calendar.monthrange(month.year, month.month)[1]), calendar_today)
    result = overview(cur, dev, meters, end, (end - month).days + 1)
    result["operating_date"] = operating_day.isoformat()
    result["calendar_today"] = calendar_today.isoformat()
    result["period"] = {"kind": "CALENDAR_MONTH", "month": month.strftime("%Y-%m"), "start": month.isoformat(),
                        "end": end.isoformat(), "calendar_days": calendar.monthrange(month.year, month.month)[1]}
    # Historical endpoint must not call the last historical row "today".
    for series in [*result["wings"].values(), result["society"]]:
        series.pop("today", None)
    return result