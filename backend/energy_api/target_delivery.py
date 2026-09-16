"""Persist only the selected target IDs and the controller's reported version.

Caller holds pi_devices FOR UPDATE before touching meters or sync state. A
target save or effective-target change advances the EXISTING integer protocol;
unchanged acknowledged snapshots do not cause repeated Pi config-file writes.
"""
from psycopg.types.json import Json
import math
from fastapi import HTTPException


def reconcile_targets(cur, did, as_of, version, *, force=False):
    cur.execute("""SELECT DISTINCT ON (wing) id, wing, target_kwh_per_day, effective_from FROM energy_generation_targets
        WHERE device_id=%s AND effective_from<=%s ORDER BY wing,effective_from DESC,id DESC""", (did, as_of))
    rows = cur.fetchall()
    for row in rows:
        value = float(row["target_kwh_per_day"])
        if not math.isfinite(value) or value < 0:
            raise HTTPException(409, "Effective generation target is invalid; correct the target before delivery")
    signature = {r["wing"]: r["id"] for r in rows}
    cur.execute("SELECT target_signature FROM energy_sync_state WHERE device_id=%s", (did,))
    previous = cur.fetchone()
    old_signature = (previous["target_signature"] if previous else None) or {}
    if force or signature != old_signature:
        cur.execute("UPDATE pi_devices SET energy_config_version=energy_config_version+1 WHERE id=%s RETURNING energy_config_version", (did,))
        version = cur.fetchone()["energy_config_version"]
        cur.execute("""INSERT INTO energy_sync_state (device_id,target_signature) VALUES (%s,%s)
            ON CONFLICT (device_id) DO UPDATE SET target_signature=EXCLUDED.target_signature""", (did, Json(signature)))
    targets = {r["wing"]: {"target_kwh_per_day": float(r["target_kwh_per_day"]), "effective_from": r["effective_from"].isoformat()} for r in rows}
    return targets, int(version)


def record_report(cur, did, energy, now):
    value = energy.get("config_version") if isinstance(energy, dict) else None
    version = value if type(value) is int and 0 <= value <= 2147483647 else None
    cur.execute("""INSERT INTO energy_sync_state (device_id,reported_config_version,reported_at) VALUES (%s,%s,%s)
        ON CONFLICT (device_id) DO UPDATE SET reported_config_version=EXCLUDED.reported_config_version, reported_at=EXCLUDED.reported_at""",
                (did, version, now))
    return version


def delivery_view(cur, did, desired_version, now):
    cur.execute("SELECT reported_config_version,reported_at FROM energy_sync_state WHERE device_id=%s", (did,))
    row = cur.fetchone()
    version = row["reported_config_version"] if row else None
    stamp = row["reported_at"] if row else None
    fresh = stamp is not None and -30 <= (now - stamp).total_seconds() <= 120
    status = "UNKNOWN" if version is None else "STALE_REPORT" if not fresh else "REPORTED_CURRENT" if version == desired_version else "PENDING"
    return {"desired_version": desired_version, "reported_version": version,
            "reported_at": stamp.isoformat() if stamp else None, "status": status,
            "evidence": "PI_REPORTED_VERSION_NOT_HARDWARE_VERIFICATION"}