"""Cloud side of a multi-file firmware release.

The Pi still polls /api/pi/sync. This module does not open a connection to the device.
A release is installable only when it is APPROVED, signed, profile-compatible, and the
device has reported the multi-file OTA agent. Draft and revoked releases are refused.
"""
import base64
import json
import uuid
from datetime import datetime, timezone

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Json

import firmware_release as release

OPEN_STATES = tuple(sorted(release.IN_PROGRESS))
_STATE_FROM_PI = {
    "DOWNLOADING": "DOWNLOADING",
    "VERIFIED": "VERIFYING",
    "STAGED": "STAGED",
    "ACTIVATING": "INSTALLING",
    "HEALTH_CHECK": "HEALTH_CHECK",
    "FAILED": "FAILED",
    "ROLLED_BACK": "ROLLED_BACK",
    "ACTIVE": "SUCCESS",
}


def _optional(cur, fn):
    cur.execute("SAVEPOINT ems_fw")
    try:
        result = fn()
        cur.execute("RELEASE SAVEPOINT ems_fw")
        return result
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
        cur.execute("ROLLBACK TO SAVEPOINT ems_fw")
        return None


def remember_agent(cur, device_id, agent):
    if agent != release.AGENT:
        return

    def write():
        cur.execute("UPDATE pi_state SET ota_agent=%s WHERE device_id=%s AND ota_agent IS DISTINCT FROM %s", (agent, device_id, agent))

    _optional(cur, write)


def _society_timezone(cur, device_id):
    cur.execute("""SELECT COALESCE(s.timezone, %s) AS timezone
                   FROM pi_devices d LEFT JOIN societies s ON s.id=d.society_id
                   WHERE d.id=%s""", (release.DEFAULT_TIMEZONE, device_id))
    row = cur.fetchone()
    return (row or {}).get("timezone") or release.DEFAULT_TIMEZONE


def sync_offer(cur, device_id, agent, now=None):
    if agent != release.AGENT:
        return None
    now = now or datetime.now(timezone.utc)

    def read():
        cur.execute("""SELECT o.id, o.state, o.scheduled_for, r.version, r.package_sha256, r.signature, r.key_id,
                              COALESCE(s.timezone, 'Asia/Kolkata') AS timezone
                       FROM ota_operations o
                       JOIN firmware_releases r ON r.version = o.firmware_version
                       JOIN pi_devices d ON d.id = o.device_id
                       LEFT JOIN societies s ON s.id = d.society_id
                       WHERE o.device_id=%s AND r.status='APPROVED' AND o.state = ANY(%s)
                       ORDER BY o.created_at DESC LIMIT 1""", (device_id, list(OPEN_STATES)))
        row = cur.fetchone()
        if not row:
            return None
        tz_name = row["timezone"] or release.DEFAULT_TIMEZONE
        state = row["state"]
        scheduled_for = row.get("scheduled_for")
        if state in release.PENDING_SCHEDULE | {"REQUESTED", "STAGED"} and release.defer_pending_update(now, scheduled_for, tz_name, state if state in release.PENDING_SCHEDULE else "SCHEDULED"):
            if state != "DEFERRED":
                cur.execute("""UPDATE ota_operations SET state='DEFERRED', error=%s, updated_at=%s
                               WHERE id=%s AND device_id=%s AND state <> 'SUCCESS'""",
                            ("Controller was offline / precondition was not satisfied", now, row["id"], device_id))
            return None
        if state in release.PENDING_SCHEDULE | {"REQUESTED", "STAGED"} and not release.activation_permitted(now, scheduled_for, tz_name):
            return None
        if state in release.PENDING_SCHEDULE and release.activation_permitted(now, scheduled_for, tz_name):
            cur.execute("""UPDATE ota_operations SET state='WAITING', updated_at=%s
                           WHERE id=%s AND device_id=%s AND state = ANY(%s)""",
                        (now, row["id"], device_id, list(release.PENDING_SCHEDULE)))
        return {"operation_id": str(row["id"]), "firmware_version": row["version"], "package_sha256": row["package_sha256"],
                "signature": row["signature"], "key_id": row["key_id"], "timezone": tz_name, "activate": True}

    return _optional(cur, read)


def note_progress(cur, device_id, operation_id, pi_state):
    mapped = _STATE_FROM_PI.get(pi_state)
    if not operation_id or not mapped:
        return

    def write():
        now = datetime.now(timezone.utc)
        cur.execute("""UPDATE ota_operations
                       SET state=%s, updated_at=%s,
                           started_at=CASE WHEN %s IN ('INSTALLING','HEALTH_CHECK','DOWNLOADING') THEN COALESCE(started_at, %s) ELSE started_at END,
                           completed_at=CASE WHEN %s IN ('SUCCESS','FAILED','ROLLED_BACK') THEN COALESCE(completed_at, %s) ELSE completed_at END
                       WHERE id=%s AND device_id=%s AND state <> 'SUCCESS' AND state <> %s""",
                    (mapped, now, mapped, now, mapped, now, operation_id, device_id, mapped))

    _optional(cur, write)


def download_manifest(cur, device_id, version):
    def read():
        cur.execute("""SELECT r.manifest, r.signature, r.key_id, o.id AS operation_id
                       FROM firmware_releases r
                       JOIN ota_operations o ON o.firmware_version=r.version AND o.device_id=%s
                       WHERE r.version=%s AND r.status='APPROVED' AND o.state = ANY(%s)
                       ORDER BY o.created_at DESC LIMIT 1""", (device_id, version, list(OPEN_STATES)))
        row = cur.fetchone()
        if not row:
            return None
        manifest = row["manifest"]
        if isinstance(manifest, str):
            manifest = json.loads(manifest)
        manifest = dict(manifest)
        manifest["operation_id"] = str(row["operation_id"])
        manifest["signature"] = row["signature"]
        manifest["key_id"] = row["key_id"]
        manifest["timezone"] = _society_timezone(cur, device_id)
        manifest.pop("notes", None)
        return manifest

    return _optional(cur, read)


def create_router(get_db, get_current_user, log_audit, require_role):
    router = APIRouter()

    def _device(cur, user, device_id):
        cur.execute("""SELECT d.id, d.name, d.society_id, d.status, d.hardware_profile, d.firmware_version,
                              p.ota_agent, p.ota_state, c.key_id
                       FROM pi_devices d
                       LEFT JOIN pi_state p ON p.device_id=d.id
                       LEFT JOIN pi_device_credentials c ON c.device_id=d.id AND c.status='active'
                       WHERE d.id=%s""", (device_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Device not found")
        if user.get("role") != "super_admin" and user.get("society_id") != row["society_id"]:
            raise HTTPException(403, "Device is outside this society")
        if row["status"] == "RETIRED":
            raise HTTPException(409, "Device is retired")
        return row

    @router.get("/api/super-admin/firmware/releases")
    def list_releases(user: dict = Depends(require_role("super_admin"))):
        conn = get_db()
        try:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                def read():
                    cur.execute("""SELECT version, hardware_profile, status, package_sha256, key_id, notes, created_at, approved_at
                                   FROM firmware_releases ORDER BY created_at DESC""")
                    return cur.fetchall()
                rows = _optional(cur, read) or []
                return {"releases": rows}
        finally:
            conn.close()

    @router.post("/api/super-admin/firmware/releases")
    def create_release(data: dict, user: dict = Depends(require_role("super_admin"))):
        manifest = data.get("manifest")
        if not isinstance(manifest, dict):
            raise HTTPException(400, "manifest required")
        try:
            files = manifest.get("files") or []
            if not isinstance(files, list):
                raise release.ReleaseError("INVALID_MANIFEST")
            built = release.build_release(
                {item["path"]: base64.b64decode(item["content_b64"], validate=True) for item in files},
                manifest.get("version"), manifest.get("hardware_profile") or release.HARDWARE_PROFILE,
                manifest.get("key_id"), manifest.get("signature"), manifest.get("min_firmware_version") or "",
                manifest.get("notes") or data.get("notes") or "")
        except (release.ReleaseError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(400, getattr(exc, "code", "INVALID_MANIFEST"))
        built["created_at"] = datetime.now(timezone.utc).isoformat()
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO firmware_releases (version, hardware_profile, status, manifest, package_sha256, signature, key_id, notes, created_by, created_at)
                               VALUES (%s, %s, 'DRAFT', %s, %s, %s, %s, %s, %s, %s)
                               ON CONFLICT (version) DO UPDATE SET manifest=EXCLUDED.manifest, package_sha256=EXCLUDED.package_sha256,
                                   signature=EXCLUDED.signature, key_id=EXCLUDED.key_id, notes=EXCLUDED.notes, status='DRAFT', approved_by=NULL, approved_at=NULL
                               WHERE firmware_releases.status <> 'APPROVED'""",
                            (built["version"], built["hardware_profile"], Json(built), built["package_sha256"], built["signature"], built["key_id"],
                             built["notes"], str(user.get("id") or ""), datetime.now(timezone.utc)))
                if cur.rowcount != 1:
                    raise HTTPException(409, "An approved release with this version cannot be replaced")
                log_audit(cur, user, 0, "FIRMWARE_RELEASE_CREATED", {"version": built["version"], "package_sha256": built["package_sha256"], "key_id": built["key_id"]})
            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {"version": built["version"], "status": "DRAFT", "package_sha256": built["package_sha256"]}

    def _set_status(version, status, user):
        conn = get_db()
        try:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute("SELECT version, status, signature, key_id, package_sha256 FROM firmware_releases WHERE version=%s", (version,))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(404, "Release not found")
                if status == "APPROVED" and not (row["signature"] and row["key_id"]):
                    raise HTTPException(400, "UNSIGNED_RELEASE")
                now = datetime.now(timezone.utc)
                cur.execute("""UPDATE firmware_releases SET status=%s, approved_by=%s, approved_at=%s WHERE version=%s""",
                            (status, str(user.get("id") or ""), now if status == "APPROVED" else None, version))
                log_audit(cur, user, 0, "FIRMWARE_RELEASE_APPROVED" if status == "APPROVED" else "FIRMWARE_RELEASE_REVOKED",
                          {"version": version, "package_sha256": row["package_sha256"]})
            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {"version": version, "status": status}

    @router.post("/api/super-admin/firmware/releases/{version}/approve")
    def approve_release(version: str, user: dict = Depends(require_role("super_admin"))):
        return _set_status(version, "APPROVED", user)

    @router.post("/api/super-admin/firmware/releases/{version}/revoke")
    def revoke_release(version: str, user: dict = Depends(require_role("super_admin"))):
        return _set_status(version, "REVOKED", user)

    @router.get("/api/admin/devices/{device_id}/firmware")
    def firmware_status(device_id: str, user: dict = Depends(get_current_user)):
        if user.get("role") not in ("super_admin", "society_admin"):
            raise HTTPException(403, "Firmware status is not available to this role")
        conn = get_db()
        try:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                row = _device(cur, user, device_id)
                def read():
                    cur.execute("""SELECT id, firmware_version, state FROM ota_operations
                                   WHERE device_id=%s ORDER BY created_at DESC LIMIT 1""", (device_id,))
                    operation = cur.fetchone()
                    cur.execute("""SELECT version, status, hardware_profile, signature, key_id, notes, approved_at
                                   FROM firmware_releases WHERE hardware_profile=%s AND status='APPROVED'
                                   ORDER BY approved_at DESC NULLS LAST LIMIT 1""", (row["hardware_profile"],))
                    approved = cur.fetchone()
                    return operation, approved
                found = _optional(cur, read)
                operation, approved = found if found else (None, None)
                device = {"firmware_version": row["firmware_version"], "ota_agent": row["ota_agent"], "hardware_profile": row["hardware_profile"],
                          "credential_state": "active" if row["key_id"] else "none"}
                offer = release.install_decision(approved, device, {"id": str(operation["id"]), "state": operation["state"], "firmware_version": operation["firmware_version"]} if operation else None)
                offer["device_name"] = row["name"]
                offer["release_notes"] = (approved or {}).get("notes") or offer.get("release_notes") or ""
                offer["latest_firmware"] = (approved or {}).get("version")
                offer["release_date"] = (approved or {}).get("approved_at")
                offer["latest_installed"] = bool(offer.get("latest_firmware")) and offer.get("current_firmware") == offer.get("latest_firmware")
                offer["notification"] = offer.get("status") == "UPDATE_AVAILABLE" and not offer["latest_installed"]
                offer["maintenance_window"] = "02:00-04:00"
                offer["timezone"] = _optional(cur, lambda: _society_timezone(cur, device_id)) or release.DEFAULT_TIMEZONE
                schedule = None
                history = []
                def extra():
                    nonlocal schedule, history
                    cur.execute("""SELECT id, firmware_version, state, created_by, scheduled_for, started_at, completed_at, created_at, error, previous_version
                                   FROM ota_operations WHERE device_id=%s ORDER BY created_at DESC LIMIT 20""", (device_id,))
                    history = cur.fetchall()
                    if operation:
                        cur.execute("""SELECT id, firmware_version, state, scheduled_for, error, previous_version, created_by
                                       FROM ota_operations WHERE id=%s AND device_id=%s""", (operation["id"], device_id))
                        schedule = cur.fetchone()
                _optional(cur, extra)
                offer["schedule"] = dict(schedule) if schedule else None
                offer["history"] = [dict(item) for item in history]
                if offer["latest_installed"]:
                    offer["notification"] = False
                    offer["status"] = "LATEST_INSTALLED" if offer.get("reason") == "VERSION_ALREADY_RUNNING" else offer["status"]
                return offer
        finally:
            conn.close()

    @router.post("/api/admin/devices/{device_id}/firmware/install")
    def install_firmware(device_id: str, user: dict = Depends(get_current_user)):
        if user.get("role") not in ("super_admin", "society_admin"):
            raise HTTPException(403, "This role cannot install firmware")
        conn = get_db()
        try:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                row = _device(cur, user, device_id)
                cur.execute("""SELECT id, firmware_version, state FROM ota_operations
                               WHERE device_id=%s AND state = ANY(%s) ORDER BY created_at DESC LIMIT 1""", (device_id, list(OPEN_STATES)))
                existing = cur.fetchone()
                cur.execute("""SELECT version, status, hardware_profile, signature, key_id, notes
                               FROM firmware_releases WHERE hardware_profile=%s AND status='APPROVED'
                               ORDER BY approved_at DESC NULLS LAST LIMIT 1""", (row["hardware_profile"],))
                approved = cur.fetchone()
                device = {"firmware_version": row["firmware_version"], "ota_agent": row["ota_agent"], "hardware_profile": row["hardware_profile"],
                          "credential_state": "active" if row["key_id"] else "none"}
                if existing and approved and existing["firmware_version"] == approved["version"]:
                    return {"operation_id": str(existing["id"]), "status": existing["state"], "duplicate": True}
                decision = release.install_decision(approved, device, dict(existing) if existing else None)
                if not decision["install_allowed"]:
                    raise HTTPException(409, decision["reason"] or decision["bootstrap_message"] or "INSTALL_REFUSED")
                tz_name = _society_timezone(cur, device_id)
                if not release.activation_permitted(datetime.now(timezone.utc), None, tz_name):
                    raise HTTPException(409, "OUTSIDE_MAINTENANCE_WINDOW")
                operation_id = str(uuid.uuid4())
                now = datetime.now(timezone.utc)
                cur.execute("""INSERT INTO ota_operations (id, device_id, firmware_version, state, created_by, created_at, updated_at)
                               VALUES (%s, %s, %s, 'REQUESTED', %s, %s, %s)""",
                            (operation_id, device_id, approved["version"], str(user.get("id") or ""), now, now))
                log_audit(cur, user, row["society_id"], "OTA_REQUESTED",
                          {"device_id": str(device_id), "operation_id": operation_id, "firmware_version": approved["version"], "role": user.get("role")})
            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {"operation_id": operation_id, "status": "REQUESTED", "firmware_version": approved["version"], "duplicate": False}

    @router.post("/api/admin/devices/{device_id}/firmware/schedule")
    def schedule_firmware(device_id: str, data: dict, user: dict = Depends(get_current_user)):
        if user.get("role") not in ("super_admin", "society_admin"):
            raise HTTPException(403, "This role cannot schedule firmware")
        conn = get_db()
        try:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                row = _device(cur, user, device_id)
                tz_name = _society_timezone(cur, device_id)
                try:
                    when = release.scheduled_instant(data.get("date"), data.get("time"), tz_name)
                except (ValueError, TypeError):
                    raise HTTPException(409, "OUTSIDE_MAINTENANCE_WINDOW")
                cur.execute("""SELECT id, firmware_version, state FROM ota_operations
                               WHERE device_id=%s AND state = ANY(%s) ORDER BY created_at DESC LIMIT 1""", (device_id, list(OPEN_STATES)))
                existing = cur.fetchone()
                cur.execute("""SELECT version, status, hardware_profile, signature, key_id, notes
                               FROM firmware_releases WHERE hardware_profile=%s AND status='APPROVED'
                               ORDER BY approved_at DESC NULLS LAST LIMIT 1""", (row["hardware_profile"],))
                approved = cur.fetchone()
                device = {"firmware_version": row["firmware_version"], "ota_agent": row["ota_agent"], "hardware_profile": row["hardware_profile"],
                          "credential_state": "active" if row["key_id"] else "none"}
                if existing and existing["state"] in release.PENDING_SCHEDULE:
                    if approved and existing["firmware_version"] == approved["version"]:
                        return {"operation_id": str(existing["id"]), "status": existing["state"], "duplicate": True, "scheduled_for": when.isoformat()}
                    raise HTTPException(409, "UPDATE_IN_PROGRESS")
                decision = release.install_decision(approved, device, dict(existing) if existing else None)
                if existing and existing["state"] in release.IN_PROGRESS:
                    raise HTTPException(409, "UPDATE_IN_PROGRESS")
                if not decision["install_allowed"]:
                    raise HTTPException(409, decision["reason"] or "INSTALL_REFUSED")
                operation_id = str(uuid.uuid4())
                now = datetime.now(timezone.utc)
                cur.execute("""INSERT INTO ota_operations (id, device_id, firmware_version, state, created_by, created_at, updated_at, scheduled_for, previous_version)
                               VALUES (%s, %s, %s, 'SCHEDULED', %s, %s, %s, %s, %s)""",
                            (operation_id, device_id, approved["version"], str(user.get("id") or ""), now, now, when, row["firmware_version"]))
                log_audit(cur, user, row["society_id"], "OTA_SCHEDULED",
                          {"device_id": str(device_id), "operation_id": operation_id, "firmware_version": approved["version"], "scheduled_for": when.isoformat(), "timezone": tz_name})
            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {"operation_id": operation_id, "status": "SCHEDULED", "firmware_version": approved["version"], "scheduled_for": when.isoformat(), "duplicate": False}

    @router.post("/api/admin/devices/{device_id}/firmware/schedule/cancel")
    def cancel_schedule(device_id: str, user: dict = Depends(get_current_user)):
        if user.get("role") not in ("super_admin", "society_admin"):
            raise HTTPException(403, "This role cannot cancel a firmware schedule")
        conn = get_db()
        try:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                row = _device(cur, user, device_id)
                cur.execute("""UPDATE ota_operations SET state='CANCELLED', updated_at=%s
                               WHERE device_id=%s AND state = ANY(%s)""",
                            (datetime.now(timezone.utc), device_id, list(release.PENDING_SCHEDULE)))
                if cur.rowcount != 1:
                    raise HTTPException(409, "No scheduled firmware update can be cancelled")
                log_audit(cur, user, row["society_id"], "OTA_SCHEDULE_CANCELLED", {"device_id": str(device_id)})
            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {"status": "CANCELLED"}

    return router
