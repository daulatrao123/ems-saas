"""Cloud qualification, not a replacement for the Pi's physical operating date.

No per-device timezone is supplied by the protocol. Allow the latest civil date
possible at UTC+14 (plus five minutes of clock skew). Older offline ledger days
remain valid. Implausible input is retained separately, never made authoritative.
"""
import json
import logging
import math
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from psycopg.types.json import Json

MIN_DAY = date(1971, 1, 1)
CLOCK_SKEW = timedelta(minutes=5)
MAX_ZONE_OFFSET = timedelta(hours=14)


def utc_now(now=None):
    now = now if now is not None else datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Energy qualification requires an aware server time")
    return now.astimezone(timezone.utc)


def latest_possible_day(now):
    return (utc_now(now) + MAX_ZONE_OFFSET + CLOCK_SKEW).date()


def date_problem(day, now):
    if type(day) is not date or day < MIN_DAY:
        return "INVALID_OPERATING_DATE"
    if day > latest_possible_day(now):
        return "FUTURE_OPERATING_DATE"
    return None


def day_problem(raw, now):
    if not isinstance(raw, dict):
        return "INVALID_LEDGER_ROW"
    try:
        day = date.fromisoformat(str(raw.get("operating_date")))
    except (TypeError, ValueError):
        return "INVALID_OPERATING_DATE"
    problem = date_problem(day, now)
    if problem:
        return problem
    if str(raw.get("status", "OPEN")).upper() not in ("OPEN", "CLOSED"):
        return "INVALID_LEDGER_STATUS"
    for key in ("opened_at", "closed_at", "updated_at"):
        value = raw.get(key)
        if value is None:
            continue
        try:
            if isinstance(value, bool) or not math.isfinite(float(value)):
                return "INVALID_LEDGER_TIMESTAMP"
            stamp = datetime.fromtimestamp(float(value), timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            return "INVALID_LEDGER_TIMESTAMP"
        if stamp > utc_now(now) + CLOCK_SKEW:
            return "FUTURE_LEDGER_TIMESTAMP"
    return None


def require_qualified_open(day, now, count=1, earliest=None):
    if day is None:
        return
    problem = date_problem(day, now) or (date_problem(earliest, now) if earliest is not None else None)
    if problem:
        raise HTTPException(409, f"Energy operating day unavailable: {problem} (reported {day}). Correct the controller clock/ledger and reconcile; stored dates have not been replaced.")
    if count > 1:
        logging.getLogger("ems.energy").warning("Multiple OPEN energy days (%s); latest qualified day=%s; history unchanged", count, day)


def quarantine(cur, did, raw, reason, now):
    # Bounded diagnostic evidence, explicitly a preview when the source is large.
    preview = json.dumps(raw, default=str, ensure_ascii=True)[:65500]
    key = date_key(raw)
    cur.execute("""INSERT INTO energy_day_quarantine (device_id, date_key, reason, payload_preview, first_seen, last_seen)
        VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (device_id,date_key) DO UPDATE SET
        reason=EXCLUDED.reason, payload_preview=EXCLUDED.payload_preview, last_seen=EXCLUDED.last_seen""",
                (did, key, reason, preview, now, now))
    return {"operating_date": key, "reason": reason}


def date_key(raw):
    # PostgreSQL text/JSONB cannot store NUL. Retain a visible escaped marker in
    # diagnostic metadata so one malformed date cannot roll back valid rows.
    return str(raw.get("operating_date")).replace("\x00", "\\0")[:40] if isinstance(raw, dict) else "INVALID_ROW"


def record_clock_report(cur, did, today, rejected, now):
    cur.execute("SELECT COUNT(*) AS open_count FROM energy_daily WHERE device_id=%s AND status='OPEN'", (did,))
    open_count = cur.fetchone()["open_count"]
    report = {"status": "REJECTED" if day_problem(today, now) else "WARNING" if rejected else "QUALIFIED",
              "reported_operating_date": date_key(today) if isinstance(today, dict) else None,
              "sampled_at": utc_now(now).isoformat(), "rejected_rows": rejected, "open_days": open_count,
              "policy": "PI_LOCAL_DATE_UTC_PLUS_14_MAX_WITH_5_MIN_SKEW"}
    if open_count > 1 and report["status"] == "QUALIFIED":
        report["status"] = "WARNING"
    cur.execute("""INSERT INTO energy_sync_state (device_id,clock_report) VALUES (%s,%s)
        ON CONFLICT (device_id) DO UPDATE SET clock_report=EXCLUDED.clock_report""", (did, Json(report)))
    # Do not evict older diagnostic evidence during ingestion. Each preview and
    # each incoming batch is bounded; total retention requires an approved plan.
    return report