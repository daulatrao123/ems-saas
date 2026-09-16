"""Operational presentation contract; accounting adjustments never enter here."""
import calendar
import math
from datetime import date


def bill_rates(cur, device_id, wing, frm, to):
    cur.execute("""SELECT bill_month, consumption_kwh FROM energy_bill_history
                   WHERE device_id=%s AND wing=%s AND bill_month BETWEEN %s AND %s ORDER BY bill_month""",
                (device_id, wing, frm.replace(day=1), to.replace(day=1)))
    rates = {}
    for row in cur.fetchall():
        value = row["consumption_kwh"]
        if value is not None and math.isfinite(float(value)) and float(value) >= 0:
            month = row["bill_month"]
            rates[month] = float(value) / calendar.monthrange(month.year, month.month)[1]
    return rates


def operational_rows(cur, device_id, wing, frm, to, rows, mode, generation_enabled):
    """Decorate already PHYSICAL-only rows; only consumption can change source."""
    if mode not in ("AUTO", "MANUAL"):
        raise ValueError("Unknown energy calculation mode")
    rates = bill_rates(cur, device_id, wing, frm, to) if mode == "MANUAL" else {}
    for row in rows:
        if mode == "MANUAL":
            value = rates.get(date.fromisoformat(row["date"]).replace(day=1))
            row["consumed_kwh"] = round(value, 4) if value is not None else None
            row["consumption_source"] = "HISTORICAL" if value is not None else "UNAVAILABLE"
        gen, cons = row["generated_kwh"], row["consumed_kwh"]
        row["generation_minus_consumption_kwh"] = round(gen - cons, 4) if gen is not None and cons is not None else None
        row["bill_month"] = row["date"][:7] if mode == "MANUAL" and cons is not None else None
        row["generation_reason"] = None if gen is not None else "M1_DISABLED" if not generation_enabled else "PHYSICAL_GENERATION_UNAVAILABLE"
        row["consumption_reason"] = None if cons is not None else "BILL_NOT_ENTERED_FOR_MONTH" if mode == "MANUAL" else "CONSUMPTION_METER_UNAVAILABLE"
        row["balance_reason"] = row["generation_reason"] or row["consumption_reason"]
        sources = {row[k] for k in ("generation_source", "consumption_source")} - {"UNAVAILABLE"}
        row["source"] = "MIXED_REFERENCE" if len(sources) > 1 else next(iter(sources), "UNAVAILABLE")
    return rows