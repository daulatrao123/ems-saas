"""
EMS SaaS Backend v6.2.3 — Industrial Production (Strict Multi-Pi & 4-Slot Contract)
"""

import os
import time
import uuid
import hmac
import hashlib
import threading
import secrets
import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException, Depends, Header, Request, Response
from fastapi.responses import PlainTextResponse, JSONResponse
from provisioning import build_provisioning_zip
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import bcrypt
from jose import JWTError, jwt
from datetime import datetime, timezone, timedelta
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# ================================================================
# CONFIG
# ================================================================

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required.")

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY environment variable is required.")

ALLOWED_ORIGINS = [
    "https://ems-saas-three.vercel.app",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5500",
    "http://localhost:5500",
]

# Browser session (T4): HttpOnly cookies, short access JWT, rotating opaque refresh token.
ACCESS_TOKEN_MINUTES = 15
REFRESH_TOKEN_DAYS = 7
ACCESS_COOKIE = "ems_access"
REFRESH_COOKIE = "ems_refresh"
CSRF_COOKIE = "ems_csrf"
CSRF_HEADER = "X-CSRF-Token"
REFRESH_COOKIE_PATH = "/api/auth"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() != "false"
TRUSTED_ORIGINS = {
    o.strip().rstrip("/") for o in os.getenv("EMS_TRUSTED_ORIGINS", ",".join(ALLOWED_ORIGINS)).split(",") if o.strip()
}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

limiter = Limiter(key_func=get_remote_address)


def pi_rate_key(request: Request) -> str:
    """Per-device limit for Pi endpoints (many Pis share one NAT IP); falls back to IP."""
    dev = (request.headers.get("X-Device-ID") or "").strip()
    return f"dev:{dev}" if dev else get_remote_address(request)


# T8 lightweight in-memory resource metrics: (day, device, metric) -> count. Never persisted,
# pruned to today+yesterday, read via /api/super-admin/metrics. Zero DB cost per increment.
from collections import defaultdict as _dd
METRICS: dict = _dd(int)
METRICS_LOCK = threading.Lock()


def metric_inc(metric: str, device_id=None, n: int = 1):
    day = datetime.now(timezone.utc).date().isoformat()
    with METRICS_LOCK:
        METRICS[(day, str(device_id) if device_id else "-", metric)] += n
        if len(METRICS) > 50000:  # hard memory bound
            for k in [k for k in METRICS if k[0] < day]:
                METRICS.pop(k, None)


TERMINAL_COMMAND_STATES = ("acked", "completed", "failed", "expired")
ARCHIVE_AGE_DAYS = 30


def archive_terminal_commands(cur, batch_size: int = 500) -> dict:
    """One bounded, transaction-safe batch: pick -> INSERT ... SELECT -> delete EXACT ids that are
    now present in the archive. Safe to run repeatedly/concurrently (SKIP LOCKED, ON CONFLICT)."""
    batch_size = max(1, min(int(batch_size), 1000))
    cur.execute(f"""
        WITH picked AS (
            SELECT id FROM pi_commands
            WHERE status = ANY(%s) AND created_at < now() - interval '{ARCHIVE_AGE_DAYS} days'
            ORDER BY created_at ASC LIMIT %s FOR UPDATE SKIP LOCKED
        ),
        ins AS (
            INSERT INTO pi_commands_archive
            SELECT c.*, now() FROM pi_commands c JOIN picked p ON p.id = c.id
            ON CONFLICT (id) DO NOTHING RETURNING id
        )
        DELETE FROM pi_commands c USING picked p
        WHERE c.id = p.id
          AND (c.id IN (SELECT id FROM ins) OR EXISTS (SELECT 1 FROM pi_commands_archive a WHERE a.id = c.id))
        RETURNING c.id
    """, (list(TERMINAL_COMMAND_STATES), batch_size))
    return {"archived": cur.rowcount, "batch_size": batch_size}
app = FastAPI(title="EMS SaaS API", version="6.4.0-targeted")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

DEFAULT_RESET_DAY = 15
SLOTS = ["A", "B", "C", "D"] # Strict 4-slot architecture
PI_ONLINE_THRESHOLD_SECONDS = 120
COMMAND_EXPIRY_SECONDS = 300
COMMAND_DELIVERY_LEASE_SECONDS = 120
EXECUTED_IDS_MAX = 50  # bound on executed_command_ids[] accepted per sync (T6)

# ---------------------------------------------------------------------------
# T7 canonical device configuration + hash. FROZEN SCHEMA v1 — byte-identical
# mirror of pi_firmware/config_hash.py (parity enforced by t7 tests).
# ---------------------------------------------------------------------------
import json as _json
CONFIG_STATES = ("DESIRED", "PENDING_APPLY", "APPLIED", "DRIFTED", "FAILED")
OTA_STATES = ("ACTIVE", "DOWNLOADING", "VERIFIED", "STAGED", "ACTIVATING", "HEALTH_CHECK", "FAILED", "ROLLED_BACK")
OTA_MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
CONFIG_ERROR_CODES = ("INVALID_HARDWARE_PROFILE", "INVALID_SLOT", "INVALID_TARGET_DAYS", "INVALID_RESET_DAY",
                      "INVALID_FEEDBACK_CONFIGURATION", "INVALID_DISPLAY_NAME", "CONFIG_PERSIST_FAILED",
                      "CONFIG_HASH_FAILED", "UNKNOWN_CONFIG_ERROR")
_CANON_SLOTS = ("A", "B", "C", "D")


def canonical_device_config(hardware_profile, feedback_hardware_installed, reset_day, slots):
    """Cloud-side canonicaliser (inputs come from our own rows; ranges already enforced upstream)."""
    profile = hardware_profile if hardware_profile not in (None, "") else "EMS-4CH-v1"
    installed = bool(feedback_hardware_installed) if feedback_hardware_installed is not None else False
    day = int(reset_day) if reset_day is not None else DEFAULT_RESET_DAY
    out_slots = {}
    for code in _CANON_SLOTS:
        raw = (slots or {}).get(code) or {}
        name = raw.get("display_name")
        fb = raw.get("feedback_enabled")
        dis = raw.get("disabled")
        tgt = raw.get("target_days")
        out_slots[code] = {
            "disabled": True if dis is None else bool(dis),
            "display_name": f"Slot {code}" if name in (None, "") else str(name),
            "feedback_enabled": bool((False if fb is None else bool(fb)) and installed),
            "target_days": 0 if tgt is None else int(tgt),
        }
    return {"feedback_hardware_installed": installed, "hardware_profile": str(profile),
            "reset_day": day, "slots": out_slots}


def config_hash(canonical) -> str:
    return hashlib.sha256(_json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                          .encode("utf-8")).hexdigest()


def derive_config_state(desired_version, desired_hash, applied_version, applied_hash, config_error) -> str:
    if config_error:
        return "FAILED"
    if applied_hash is None or applied_version is None:
        return "DESIRED"
    if int(applied_version) != int(desired_version):
        return "PENDING_APPLY"
    return "APPLIED" if applied_hash == desired_hash else "DRIFTED"

COMMAND_TRANSITIONS = {
    "queued": {"delivered", "expired"},
    "delivered": {"executing", "expired", "unknown_after_reboot"},
    "executing": {"hardware_verified", "failed", "unknown_after_reboot"},
    "hardware_verified": {"completed", "failed"},
    "unknown_after_reboot": {"hardware_verified", "completed", "failed"},
    "completed": {"acked"},
    "failed": {"acked"},
    "expired": {"acked"},
    "acked": set(),
}
VALID_ROLES = {"super_admin", "society_admin", "member"}

POSITIVE_VERIFICATION = {"VERIFIED_ON", "VERIFIED_OFF", "GPIO_CONFIRMED"}

# Two completion contracts (P0-1):
#  A) HARDWARE_COMMANDS: completed == physical state positively verified (POSITIVE_VERIFICATION).
#  B) SOFTWARE_RESULTS:  completed == the Pi validated/applied/persisted the requested software operation.
#     Each command has an explicit allowlisted result token; none of them is proof of physical state,
#     and a software command can never pass through `hardware_verified`.
HARDWARE_COMMANDS = {"set_active_slot", "off_slot", "off_all"}
SOFTWARE_RESULTS = {
    "set_days": {"CONFIG_ACCEPTED"}, "set_reset_day": {"CONFIG_ACCEPTED"},
    "reset_days": {"STATE_RESET"}, "lcd_display": {"DISPLAY_UPDATED"},
    "restart": {"RESTART_SCHEDULED"}, "reboot": {"REBOOT_SCHEDULED"},
}

def resolve_ack_path(current: str, target: str, verification: str, command: str = "") -> list[str] | None:
    """Return the FSM hops an ACK implies (current excluded, target included), or None if illegal.

    Direct edges come from COMMAND_TRANSITIONS. A Pi that executed offline and lost its
    intermediate ACKs may report only the terminal status; terminal ACKs therefore fast-forward
    through the implied hops. Hardware commands reach `completed` only with positive hardware
    verification; software commands reach `completed` only with their allowlisted result token
    and never via `hardware_verified`. No path may pass through or leave a terminal/acked state.
    """
    if command in SOFTWARE_RESULTS:
        if target == "hardware_verified":
            return None
        if target == "completed" and verification not in SOFTWARE_RESULTS[command]:
            return None
        if target in COMMAND_TRANSITIONS.get(current, set()):
            return [target]
        return {
            ("executing", "completed"): ["completed"],
            ("delivered", "completed"): ["executing", "completed"],
            ("delivered", "failed"): ["executing", "failed"],
        }.get((current, target))
    if target in COMMAND_TRANSITIONS.get(current, set()):
        return [target]
    fast_forward = {
        ("delivered", "hardware_verified"): ["executing", "hardware_verified"],
        ("delivered", "completed"): ["executing", "hardware_verified", "completed"],
        ("executing", "completed"): ["hardware_verified", "completed"],
        ("delivered", "failed"): ["executing", "failed"],
    }
    path = fast_forward.get((current, target))
    if path and target == "completed" and verification not in POSITIVE_VERIFICATION:
        return None
    return path

VALID_COMMANDS = {
    "set_active_slot", "set_days", "set_reset_day", "restart",
    "reboot", "reset_days", "off_slot", "off_all", "lcd_display",
}

# ================================================================
# DATABASE & SCHEMA OWNERSHIP
# ================================================================
# Alembic is the sole owner of the schema. The application never issues DDL; it
# only verifies at boot that the database is at this build's single migration head
# and refuses to serve otherwise (start.sh runs `alembic upgrade head` first).

ALEMBIC_INI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alembic.ini")

def get_db():
    conn = psycopg.connect(DATABASE_URL, connect_timeout=10, row_factory=dict_row)
    conn.autocommit = False
    return conn

def expected_schema_revision() -> tuple[str, set[str]]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(Config(ALEMBIC_INI))
    heads = script.get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"Migration tree must have exactly one head, found {heads}")
    return heads[0], {rev.revision for rev in script.walk_revisions()}

@app.on_event("startup")
def verify_schema_revision():
    head, known = expected_schema_revision()
    try:
        conn = get_db()
    except Exception as e:
        raise RuntimeError(f"Database unreachable during startup schema check: {e}")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.alembic_version') AS t")
            if cur.fetchone()["t"] is None:
                raise RuntimeError(
                    "SCHEMA GUARD: alembic_version table missing - database has never been migrated. "
                    f"Run `alembic upgrade head` (expected {head}). Refusing to start.")
            cur.execute("SELECT version_num FROM alembic_version")
            versions = [r["version_num"] for r in cur.fetchall()]
        conn.rollback()
    finally:
        conn.close()
    if len(versions) != 1:
        raise RuntimeError(f"SCHEMA GUARD: alembic_version must hold exactly one revision, found {versions}. Refusing to start.")
    current = versions[0]
    if current == head:
        print(f"SCHEMA GUARD OK: database at Alembic head {head}")
        return
    if current in known:
        raise RuntimeError(
            f"SCHEMA GUARD: database at {current} is BEHIND this build's head {head}. "
            "Run `alembic upgrade head`. Refusing to start.")
    raise RuntimeError(
        f"SCHEMA GUARD: database at unknown revision {current} (AHEAD of or incompatible with this build, head {head}). "
        "Deploy a build that knows this revision or restore the database. Refusing to start.")

def require_uuid(value, what: str) -> str:
    """Reject malformed UUIDs with 400 before they reach PostgreSQL (which would 500)."""
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(400, f"{what} must be a UUID")

def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Device credential lifecycle (per-device, revocable, rotatable).
# Verifier = SHA-256 of a 256-bit random secret (or an operator-supplied secret
# of >= 16 chars); plaintext exists only in the provisioning/rotation response.
# ---------------------------------------------------------------------------
MIN_DEVICE_SECRET_LEN = 16


def new_device_secret() -> str:
    return secrets.token_urlsafe(32)


def new_key_id() -> str:
    return "k_" + secrets.token_hex(6)


def issue_device_credential(cur, device_id: str, user: dict | None, origin: str, secret: str | None = None,
                            reason: str | None = None) -> dict:
    """Inside the caller's transaction: lock device, revoke any active credential,
    insert the new one. Returns the plaintext secret exactly once."""
    cur.execute("SELECT id, status FROM pi_devices WHERE id=%s FOR UPDATE", (device_id,))
    dev = cur.fetchone()
    if not dev:
        raise HTTPException(404, "Device not found")
    if str(dev["status"]).upper() == "RETIRED":
        raise HTTPException(409, "Retired device cannot receive credentials")
    if secret is not None:
        if not isinstance(secret, str) or len(secret) < MIN_DEVICE_SECRET_LEN or len(secret) > 512:
            raise HTTPException(400, f"api_key must be a string of {MIN_DEVICE_SECRET_LEN}-512 characters")
    else:
        secret = new_device_secret()
    now = datetime.now(timezone.utc)
    cur.execute("""UPDATE pi_device_credentials SET status='revoked', revoked_at=%s, revoked_by=%s, revoke_reason=%s
                   WHERE device_id=%s AND status='active' RETURNING key_id""",
                (now, (user or {}).get("id"), reason or "ROTATED", device_id))
    revoked = [r["key_id"] for r in cur.fetchall()]
    key_id = new_key_id()
    cur.execute("""INSERT INTO pi_device_credentials (id, device_id, key_id, secret_hash, hash_alg, status, origin, created_at, created_by, rotated_at)
                   VALUES (%s, %s, %s, %s, 'sha256', 'active', %s, %s, %s, %s)""",
                (str(uuid.uuid4()), device_id, key_id, hash_api_key(secret), origin, now, (user or {}).get("id"),
                 now if revoked else None))
    if user is not None:
        log_audit(cur, user, 0, "CREDENTIAL_ROTATED" if revoked else "CREDENTIAL_CREATED",
                  {"device_id": str(device_id), "key_id": key_id, "revoked_key_ids": revoked, "origin": origin})
    return {"device_id": str(device_id), "key_id": key_id, "api_key": secret, "revoked_key_ids": revoked}


def revoke_device_credentials(cur, device_id: str, user: dict | None, reason: str) -> list:
    now = datetime.now(timezone.utc)
    cur.execute("""UPDATE pi_device_credentials SET status='revoked', revoked_at=%s, revoked_by=%s, revoke_reason=%s
                   WHERE device_id=%s AND status='active' RETURNING key_id""",
                (now, (user or {}).get("id"), reason, device_id))
    revoked = [r["key_id"] for r in cur.fetchall()]
    if user is not None and revoked:
        log_audit(cur, user, 0, "CREDENTIAL_REVOKED", {"device_id": str(device_id), "key_ids": revoked, "reason": reason})
    return revoked

def assert_society_name_available(cur, name: str, exclude_id: int | None = None) -> None:
    """P1 business rule (== migration 0008 index): lower(btrim(name)) unique among non-RETIRED societies."""
    cur.execute("""SELECT id FROM societies
                   WHERE status <> 'RETIRED' AND lower(btrim(name)) = lower(btrim(%s)) AND id IS DISTINCT FROM %s
                   LIMIT 1""", (name, exclude_id))
    if cur.fetchone():
        raise HTTPException(409, "A society with this name already exists")


def society_conflict_or_raise(exc: Exception):
    """DB unique-index backstop (concurrent inserts): map the violation to the same 409."""
    if isinstance(exc, psycopg.errors.UniqueViolation) and "ux_societies_active_normalized_name" in str(exc):
        raise HTTPException(409, "A society with this name already exists")
    raise exc


def log_audit(cur, user: dict, society_id: int, action: str, details: dict):
    metric_inc("audit_rows")
    cur.execute("""INSERT INTO audit_log (society_id, user_id, action, details, created_at)
                   VALUES (%s, %s, %s, %s, %s)""",
                (society_id if society_id else 0, user.get("id"), action, psycopg.types.json.Json(details), datetime.now(timezone.utc)))

# ================================================================
# EXCEPTION HANDLERS & CORS
# ================================================================

def _cors_headers(origin: str) -> dict:
    valid = origin if origin in ALLOWED_ORIGINS else ALLOWED_ORIGINS[0]
    return {
        "Access-Control-Allow-Origin": valid,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, PATCH, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With, X-Api-Key, X-CSRF-Token",
    }

@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=_cors_headers(request.headers.get("origin", "")))

@app.exception_handler(Exception)
async def unhandled_exc_handler(request: Request, exc: Exception):
    print(f"UNHANDLED {request.method} {request.url}: {type(exc).__name__}: {exc}")
    return JSONResponse(status_code=500, content={"detail": "Internal server error"}, headers=_cors_headers(request.headers.get("origin", "")))

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With", "X-Api-Key", "X-CSRF-Token"],
)

# ================================================================
# UTILITIES
# ================================================================

def create_access_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user["id"]), "id": user["id"], "role": user["role"],
        "society_id": user["society_id"], "type": "access",
        "iat": now, "exp": now + timedelta(minutes=ACCESS_TOKEN_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def _referer_origin(request: Request) -> str:
    referer = request.headers.get("referer", "")
    parts = referer.split("/", 3)
    return f"{parts[0]}//{parts[2]}" if len(parts) >= 3 and parts[1] == "" else ""

def assert_trusted_origin(request: Request) -> None:
    # Origin and Referer are browser-controlled (forbidden) headers, so neither can be
    # forged by a cross-site page. Origin is authoritative; Referer covers proxies that
    # rewrite Origin (observed on the preview ingress). Matching is exact (scheme+host+port).
    # Missing both => reject: every browser session endpoint (login included) needs a source.
    origin = (request.headers.get("origin") or "").rstrip("/")
    referer_origin = _referer_origin(request)
    if origin in TRUSTED_ORIGINS or referer_origin in TRUSTED_ORIGINS:
        return
    raise HTTPException(status_code=403, detail="Untrusted origin")

def assert_csrf(request: Request) -> None:
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get(CSRF_HEADER, "")
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")

def set_session_cookies(response: Response, access_token: str, refresh_token: str, csrf_token: str) -> None:
    refresh_max_age = REFRESH_TOKEN_DAYS * 86400
    response.set_cookie(ACCESS_COOKIE, access_token, max_age=ACCESS_TOKEN_MINUTES * 60, path="/",
                        httponly=True, secure=COOKIE_SECURE, samesite="lax")
    response.set_cookie(REFRESH_COOKIE, refresh_token, max_age=refresh_max_age, path=REFRESH_COOKIE_PATH,
                        httponly=True, secure=COOKIE_SECURE, samesite="lax")
    response.set_cookie(CSRF_COOKIE, csrf_token, max_age=refresh_max_age, path="/",
                        httponly=False, secure=COOKIE_SECURE, samesite="lax")

def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/", secure=COOKIE_SECURE, samesite="lax")
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, secure=COOKIE_SECURE, samesite="lax")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=COOKIE_SECURE, samesite="lax")

def issue_refresh_token(cur, user_id: int, family_id: str, replaces: str | None = None) -> str:
    raw = secrets.token_urlsafe(48)
    now = datetime.now(timezone.utc)
    new_id = str(uuid.uuid4())
    cur.execute("""INSERT INTO auth_refresh_tokens (id, user_id, family_id, token_hash, created_at, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (new_id, user_id, family_id, hash_refresh_token(raw), now, now + timedelta(days=REFRESH_TOKEN_DAYS)))
    if replaces:
        cur.execute("UPDATE auth_refresh_tokens SET used_at=%s, replaced_by=%s WHERE id=%s", (now, new_id, replaces))
    return raw

def revoke_refresh_family(cur, family_id: str) -> None:
    cur.execute("UPDATE auth_refresh_tokens SET revoked_at=%s WHERE family_id=%s AND revoked_at IS NULL",
                (datetime.now(timezone.utc), family_id))

def public_user(db_user: dict) -> dict:
    return {"id": db_user["id"], "email": db_user["email"], "name": db_user["name"],
            "role": db_user["role"], "society_id": db_user["society_id"]}

def is_pi_online(pi_state: dict) -> bool:
    if not pi_state: return False
    last_sync = pi_state.get("last_sync")
    if not last_sync: return False
    if isinstance(last_sync, str):
        try:
            if last_sync.endswith("Z"): last_sync = last_sync[:-1] + "+00:00"
            last_sync = datetime.fromisoformat(last_sync)
        except (TypeError, ValueError): return False
    if last_sync.tzinfo is None: last_sync = last_sync.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last_sync).total_seconds() <= PI_ONLINE_THRESHOLD_SECONDS

def slot_is_visible(config: dict, state: dict) -> bool:
    physical = str(state.get("physical_toggle", "UNKNOWN")).upper()
    return (
        int(config.get("target_days", 0)) > 0
        and physical == "ON"
        and not bool(config.get("disabled", False))
    )

def validate_command(command: str, params: dict, slot: str = "") -> None:
    if command not in VALID_COMMANDS:
        raise HTTPException(400, f"Unsupported command: {command}")
    if command in ("set_active_slot", "set_days", "off_slot"):
        if slot not in SLOTS:
            raise HTTPException(400, f"Valid slot ({', '.join(SLOTS)}) is required")
    if command == "set_days":
        try: days = int(params.get("days"))
        except (TypeError, ValueError): raise HTTPException(400, "days must be an integer")
        if not 1 <= days <= 31: raise HTTPException(400, "days must be 1-31")
    if command == "set_reset_day":
        try: day = int(params.get("day"))
        except (TypeError, ValueError): raise HTTPException(400, "day must be an integer")
        if not 1 <= day <= 28: raise HTTPException(400, "reset day must be 1-28")

# ================================================================
# AUTH
# ================================================================

class UserLogin(BaseModel):
    email: str
    password: str

async def get_current_user(request: Request) -> dict:
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    # Cookie-authenticated state changes need CSRF proof and a trusted browser origin.
    if request.method.upper() in UNSAFE_METHODS:
        assert_trusted_origin(request)
        assert_csrf(request)
    return {"id": payload.get("id"), "role": payload.get("role"), "society_id": payload.get("society_id")}

def require_role(*roles):
    async def checker(user: dict = Depends(get_current_user)):
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return checker

async def require_society_access(request: Request, user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") not in ("super_admin", "society_admin", "member"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    if user["role"] != "super_admin":
        requested = str(request.query_params.get("society_id", ""))
        owned = str(user.get("society_id", ""))
        if requested and requested != owned:
            raise HTTPException(status_code=403, detail="Cannot access other society data")
    return user

def authenticate_pi(
    payload: dict,
    x_device_id: str | None = Header(None, alias="X-Device-ID"),
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
) -> tuple:
    # Header credentials are canonical. Body credentials remain accepted only
    # for backward compatibility with older Pi firmware.
    device_id = str(x_device_id or payload.get("deviceId") or "").strip()
    supplied_key = str(x_api_key or payload.get("key") or "").strip()
    try:
        uuid.UUID(device_id)
    except ValueError:
        raise HTTPException(403, "Invalid Pi API key or Device ID.")
    if not device_id:
        raise HTTPException(400, "Invalid deviceId")
    if not supplied_key:
        raise HTTPException(401, "Pi API key required")

    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            # credential -> device -> pi_devices -> authorization. Only the ACTIVE
            # credential of THIS device is consulted; pi_devices.api_key_hash is
            # no longer an authentication path.
            cur.execute(
                """SELECT d.id, d.society_id, d.status, c.secret_hash
                   FROM pi_devices d
                   LEFT JOIN pi_device_credentials c ON c.device_id = d.id AND c.status = 'active'
                   WHERE d.id = %s""",
                (device_id,),
            )
            dev = cur.fetchone()
            supplied_hash = hash_api_key(supplied_key)
            # Same response for unknown device / revoked / wrong secret (no enumeration).
            if not dev or not dev["secret_hash"] or not hmac.compare_digest(supplied_hash, str(dev["secret_hash"])):
                raise HTTPException(403, "Invalid Pi API key or Device ID.")
            if not dev["society_id"] or str(dev["status"]).upper() != "ASSIGNED":
                raise HTTPException(403, "Device is not assigned to an active society.")
            return dev["id"], dev["society_id"]
    finally:
        conn.close()

# ================================================================
# HEALTH & KEEPALIVE
# ================================================================

@app.get("/keepalive")
def keepalive():
    return PlainTextResponse("alive")

@app.head("/ping")
@app.get("/ping")
def ping():
    return PlainTextResponse("pong")

@app.get("/api/health")
def health():
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.commit()
        conn.close()
        return {"status": "ok", "database": "ok"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "error", "database": "unavailable"})

# ================================================================
# BOOTSTRAP
# ================================================================

@app.post("/api/bootstrap")
@limiter.limit("1/minute")
def bootstrap(request: Request):
    bootstrap_pass = os.getenv("EMS_BOOTSTRAP_PASSWORD")
    if not bootstrap_pass:
        raise HTTPException(500, "EMS_BOOTSTRAP_PASSWORD env variable is not configured.")

    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id FROM users LIMIT 1")
            if cur.fetchone():
                raise HTTPException(400, "System already initialized")

            cur.execute("INSERT INTO societies (name, location, plan, status, society_code, config_version) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                        ("Prestine Pacific", "Mumbai", "Basic", "active", "prestine", 1))
            sid = cur.fetchone()["id"]

            device_id = str(uuid.uuid4())
            raw_api_key = str(uuid.uuid4())
            cur.execute("INSERT INTO pi_devices (id, society_id, name, api_key_hash, status, feedback_hardware_installed) VALUES (%s, %s, %s, %s, %s, %s)",
                        (device_id, sid, "Main Controller", hash_api_key(raw_api_key), "ASSIGNED", True))
            issue_device_credential(cur, device_id, None, "bootstrap", secret=raw_api_key)

            cur.execute("INSERT INTO users (email, name, password, role, society_id) VALUES (%s, %s, %s, %s, %s)",
                        ("admin@ems.com", "Super Admin", bcrypt.hashpw(bootstrap_pass.encode(), bcrypt.gensalt()).decode(), "super_admin", None))

            for slot_code in SLOTS:
                is_disabled = slot_code not in ["A", "B"]
                cur.execute("""INSERT INTO slot_configs (device_id, slot, display_name, target_days, disabled, feedback_enabled)
                               VALUES (%s, %s, %s, %s, %s, %s)""",
                           (device_id, slot_code, f"Slot {slot_code}", 10, is_disabled, not is_disabled))
                cur.execute("INSERT INTO slot_state (device_id, slot) VALUES (%s, %s)", (device_id, slot_code))

            cur.execute("""INSERT INTO pi_state (device_id, active_slot, reset_day, emergency_stop, uptime_seconds, cpu_temp, disk_free_mb, last_sync, boot_count, watchdog_enabled, config_version)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (device_id, "A", DEFAULT_RESET_DAY, False, 0, 0.0, 0.0, datetime.now(timezone.utc), 0, True, 0))

        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

    return {"message": "Initialized. Change default passwords.", "device_id": device_id, "api_key": raw_api_key}

# ================================================================
# AUTH — BROWSER SESSION (HttpOnly cookies, refresh rotation, CSRF)
# ================================================================

@app.post("/api/auth/login")
@limiter.limit("5/minute")
def login(request: Request, response: Response, user: UserLogin):
    assert_trusted_origin(request)
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM users WHERE email = %s", (user.email,))
            db_user = cur.fetchone()
            if not db_user or not bcrypt.checkpw(user.password.encode(), db_user["password"].encode()):
                raise HTTPException(status_code=401, detail="Invalid credentials")
            refresh_raw = issue_refresh_token(cur, db_user["id"], str(uuid.uuid4()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    set_session_cookies(response, create_access_token(db_user), refresh_raw, secrets.token_urlsafe(32))
    return public_user(db_user)

@app.post("/api/auth/refresh")
@limiter.limit("30/minute")
def refresh_session(request: Request, response: Response):
    assert_trusted_origin(request)
    assert_csrf(request)
    raw = request.cookies.get(REFRESH_COOKIE, "")
    if not raw:
        clear_session_cookies(response)
        raise HTTPException(status_code=401, detail="No refresh session")
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM auth_refresh_tokens WHERE token_hash = %s FOR UPDATE", (hash_refresh_token(raw),))
            row = cur.fetchone()
            if not row:
                clear_session_cookies(response)
                raise HTTPException(status_code=401, detail="Invalid refresh session")
            if row["revoked_at"] is not None or row["used_at"] is not None:
                # Reuse of a rotated/revoked token: treat the whole family as compromised.
                revoke_refresh_family(cur, str(row["family_id"]))
                conn.commit()
                clear_session_cookies(response)
                raise HTTPException(status_code=401, detail="Refresh token reuse detected; session revoked")
            if row["expires_at"] <= datetime.now(timezone.utc):
                clear_session_cookies(response)
                raise HTTPException(status_code=401, detail="Refresh session expired")
            cur.execute("SELECT * FROM users WHERE id = %s", (row["user_id"],))
            db_user = cur.fetchone()
            if not db_user:
                revoke_refresh_family(cur, str(row["family_id"]))
                conn.commit()
                clear_session_cookies(response)
                raise HTTPException(status_code=401, detail="User no longer exists")
            new_raw = issue_refresh_token(cur, db_user["id"], str(row["family_id"]), replaces=str(row["id"]))
        conn.commit()
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    set_session_cookies(response, create_access_token(db_user), new_raw, secrets.token_urlsafe(32))
    return public_user(db_user)

@app.post("/api/auth/logout")
@limiter.limit("30/minute")
def logout(request: Request, response: Response):
    assert_trusted_origin(request)
    assert_csrf(request)
    raw = request.cookies.get(REFRESH_COOKIE, "")
    if raw:
        conn = get_db()
        try:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT family_id FROM auth_refresh_tokens WHERE token_hash = %s", (hash_refresh_token(raw),))
                row = cur.fetchone()
                if row:
                    revoke_refresh_family(cur, str(row["family_id"]))
            conn.commit()
        finally:
            conn.close()
    clear_session_cookies(response)
    return {"message": "Logged out"}

@app.get("/api/auth/me")
def me(user: dict = Depends(get_current_user)):
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (user["id"],))
            db_user = cur.fetchone()
    finally:
        conn.close()
    if not db_user:
        raise HTTPException(status_code=401, detail="User no longer exists")
    return public_user(db_user)

# ================================================================
# SUPER-ADMIN — SOCIETIES (Multi-Pi & 4-Slot)
# ================================================================

@app.get("/api/super-admin/societies")
def get_societies(user: dict = Depends(require_role("super_admin"))):
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM societies ORDER BY id ASC")
            societies = cur.fetchall()
            result = []
            for s in societies:
                sid = s["id"]
                cur.execute("SELECT id, name, firmware_version, last_seen, hardware_profile, feedback_hardware_installed FROM pi_devices WHERE society_id = %s ORDER BY name ASC", (sid,))
                devs = cur.fetchall()

                devices_data = []
                society_online = False

                for dev in devs:
                    cur.execute("SELECT * FROM pi_state WHERE device_id = %s", (dev["id"],))
                    pi = cur.fetchone()
                    if is_pi_online(pi): society_online = True

                    slots_data = {}
                    cur.execute("SELECT * FROM slot_configs WHERE device_id = %s", (dev["id"],))
                    configs = cur.fetchall()
                    for c in configs:
                        cur.execute("SELECT * FROM slot_state WHERE device_id = %s AND slot = %s", (dev["id"], c["slot"]))
                        st = cur.fetchone()
                        slots_data[c["slot"]] = {
                            "display_name": c["display_name"],
                            "target_days": c["target_days"],
                            "disabled": c["disabled"],
                            "feedback_enabled": bool(c["feedback_enabled"]) and bool(dev["feedback_hardware_installed"]),
                            "used_days": st["used_days"] if st else 0,
                            "physical_toggle": st["physical_toggle"] if st else "UNKNOWN",
                        }

                    devices_data.append({
                        "id": str(dev["id"]),
                        "name": dev["name"],
                        "online": is_pi_online(pi),
                        "active_slot": pi.get("active_slot") if pi else None,
                        "hardware_profile": dev["hardware_profile"],
                        "feedback_hardware_installed": dev["feedback_hardware_installed"],
                        "config_state": (pi.get("config_state") if pi else None) or "DESIRED",
                        "ota_state": pi.get("ota_state") if pi else None,
                        "storage_state": ("FAILED" if pi.get("disk_free_mb", 0) < 0 else "OK") if pi and pi.get("disk_free_mb") is not None else "UNKNOWN",
                        "slots": slots_data
                    })

                result.append({
                    "id": s["id"], "name": s["name"], "location": s["location"], "status": s["status"],
                    "pi_online": society_online,
                    "devices": devices_data
                })
            return result
    finally:
        conn.close()

@app.post("/api/super-admin/societies/save")
def save_society(data: dict, user: dict = Depends(require_role("super_admin"))):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            sid = data.get("id")
            try:
                requested_reset_day = int(data.get("reset_day", DEFAULT_RESET_DAY))
            except (TypeError, ValueError):
                raise HTTPException(400, "reset_day must be an integer")
            if not 1 <= requested_reset_day <= 28:
                raise HTTPException(400, "reset_day must be 1-28")

            society = {
                "name": data.get("name", ""), "location": data.get("location", ""),
                "plan": "Basic", "status": "active", "tailscale_ip": "", "pi_port": 5000,
                "society_code": f"SOC-{data.get('name', 'X')[:4].upper()}",
                "reset_day": requested_reset_day,
            }

            if sid:
                assert_society_name_available(cur, society["name"], exclude_id=int(sid))
                cur.execute("""UPDATE societies SET name=%s, location=%s, reset_day=%s, config_version=config_version+1 WHERE id=%s""",
                            (society["name"], society["location"], society["reset_day"], sid))
                new_sid = int(sid)
            else:
                if not society["name"].strip():
                    raise HTTPException(400, "name required")
                assert_society_name_available(cur, society["name"])
                cur.execute("""INSERT INTO societies (name, location, plan, status, tailscale_ip, pi_port, society_code, config_version, reset_day)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s) RETURNING id""",
                            (society["name"], society["location"], society["plan"], society["status"],
                             society["tailscale_ip"], society["pi_port"], society["society_code"], society["reset_day"]))
                new_sid = cur.fetchone()["id"]
                log_audit(cur, user, new_sid, "CREATE_SOCIETY", society)

            devices = data.get("devices", [])
            assigned_ids = [d["device_id"] for d in devices if d.get("device_id")]

            if assigned_ids:
                cur.execute("UPDATE pi_devices SET society_id=NULL, status='INVENTORY' WHERE society_id=%s AND id != ALL(%s)", (new_sid, assigned_ids))
            else:
                cur.execute("UPDATE pi_devices SET society_id=NULL, status='INVENTORY' WHERE society_id=%s", (new_sid,))

            for dev in devices:
                dev_id = dev["device_id"]
                if not dev_id: continue

                cur.execute("SELECT status FROM pi_devices WHERE id=%s FOR UPDATE", (dev_id,))
                device_row = cur.fetchone()
                if not device_row:
                    raise HTTPException(404, f"Device not found: {dev_id}")
                if str(device_row["status"]).upper() == "RETIRED":
                    raise HTTPException(409, f"Retired device cannot be reassigned: {dev_id}")

                cur.execute(
                    "UPDATE pi_devices SET society_id=%s, status='ASSIGNED', hardware_profile=%s, feedback_hardware_installed=%s WHERE id=%s",
                    (new_sid, dev.get("hardware_profile", "EMS-4CH-v1"), bool(dev.get("feedback_hardware_installed", False)), dev_id)
                )

                slots = dev.get("slots", {})
                for slot_code in SLOTS:
                    slot_data = slots.get(slot_code, {})
                    s_name = slot_data.get("display_name", f"Slot {slot_code}")
                    s_disabled = bool(slot_data.get("disabled", True))
                    s_target = int(slot_data.get("target_days", 0))

                    # RED 12 Fix: Enforce hardware consistency
                    dev_has_feedback_hw = bool(dev.get("feedback_hardware_installed", False))
                    s_feedback_requested = bool(slot_data.get("feedback_enabled", False))
                    s_feedback = s_feedback_requested and dev_has_feedback_hw

                    cur.execute("""INSERT INTO slot_configs (device_id, slot, display_name, target_days, disabled, feedback_enabled)
                                   VALUES (%s, %s, %s, %s, %s, %s)
                                   ON CONFLICT (device_id, slot) DO UPDATE SET
                                   display_name=EXCLUDED.display_name,
                                   target_days=EXCLUDED.target_days,
                                   disabled=EXCLUDED.disabled,
                                   feedback_enabled=EXCLUDED.feedback_enabled""",
                                (dev_id, slot_code, s_name, s_target, s_disabled, s_feedback))

        conn.commit()
    except Exception as e:
        conn.rollback()
        society_conflict_or_raise(e)
    finally:
        conn.close()
    return {"message": "Saved"}

@app.post("/api/super-admin/societies/delete")
def delete_society(data: dict, user: dict = Depends(require_role("super_admin"))):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            sid = data.get("id")
            cur.execute("SELECT id, status FROM societies WHERE id=%s FOR UPDATE", (sid,))
            society = cur.fetchone()
            if not society:
                raise HTTPException(404, "Society not found")
            cur.execute("UPDATE societies SET status='RETIRED' WHERE id = %s", (sid,))
            cur.execute("UPDATE pi_devices SET status='RETIRED', society_id=NULL WHERE society_id=%s RETURNING id", (sid,))
            for row in cur.fetchall():
                revoke_device_credentials(cur, str(row["id"]), user, "SOCIETY_RETIRED")
            log_audit(cur, user, sid, "RETIRE_SOCIETY", {})
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
    return {"message": "Deleted"}

# ================================================================
# SUPER-ADMIN — DEVICES (Inventory Lifecycle)
# ================================================================

@app.post("/api/super-admin/devices/{device_id}/credentials/rotate")
def rotate_device_credential(device_id: str, data: dict | None = None, user: dict = Depends(require_role("super_admin"))):
    """Atomically revoke the active credential and issue a new one. The plaintext
    secret is returned ONLY here and must be provisioned onto the Pi out-of-band."""
    device_id = require_uuid(device_id, "device_id")
    supplied = (data or {}).get("api_key") if isinstance(data, dict) else None
    conn = get_db()
    try:
        with conn.cursor() as cur:
            issued = issue_device_credential(cur, device_id, user, "rotate", secret=supplied)
        conn.commit()
        return {"message": "Credential rotated", **issued}
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

@app.post("/api/super-admin/devices/{device_id}/credentials/revoke")
def revoke_device_credential(device_id: str, data: dict | None = None, user: dict = Depends(require_role("super_admin"))):
    """Immediately block the device credential; the device, its society and its
    history stay intact. Idempotent."""
    device_id = require_uuid(device_id, "device_id")
    reason = str((data or {}).get("reason") or "REVOKED_BY_ADMIN")[:64] if isinstance(data, dict) else "REVOKED_BY_ADMIN"
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM pi_devices WHERE id=%s FOR UPDATE", (device_id,))
            if not cur.fetchone():
                raise HTTPException(404, "Device not found")
            revoked = revoke_device_credentials(cur, device_id, user, reason)
        conn.commit()
        return {"message": "Credential revoked" if revoked else "No active credential", "device_id": device_id, "revoked_key_ids": revoked}
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

@app.post("/api/super-admin/devices/{device_id}/provisioning-package")
def provisioning_package(device_id: str, request: Request, user: dict = Depends(require_role("super_admin"))):
    """Rotate the device credential (old key stops working) and return a ZIP installer containing the
    NEW key. The secret exists only in the ZIP body: not in JSON, not in logs, not in audit_log."""
    device_id = require_uuid(device_id, "device_id")
    api_url = os.environ.get("EMS_PUBLIC_API_URL") or (str(request.base_url).rstrip("/") + "/api")
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT name, status, society_id FROM pi_devices WHERE id = %s", (device_id,))
            dev = cur.fetchone()
            if not dev:
                raise HTTPException(404, "Device not found")
            issued = issue_device_credential(cur, device_id, user, "provisioning")
            log_audit(cur, user, dev["society_id"] or 0, "PROVISIONING_PACKAGE",
                      {"device_id": device_id, "key_id": issued["key_id"], "api_url": api_url})
            payload = build_provisioning_zip(device_id, dev["name"], issued["key_id"], issued["api_key"], api_url)
        conn.commit()
    except Exception as e:
        conn.rollback(); raise e
    finally:
        conn.close()
    return Response(content=payload, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="ems-pi-provisioning-{device_id[:8]}.zip"',
        "Cache-Control": "no-store, no-cache, must-revalidate, private", "Pragma": "no-cache",
        "X-EMS-Key-Id": issued["key_id"], "X-EMS-Device-Id": device_id,
    })

@app.post("/api/super-admin/devices/{device_id}/feedback-hardware")
def set_feedback_hardware(device_id: str, data: dict, user: dict = Depends(require_role("super_admin"))):
    """Device-level 'feedback hardware physically installed' flag. Bumps the society config_version so the
    canonical config (effective feedback = installed AND slot.feedback_enabled) re-converges (T7)."""
    device_id = require_uuid(device_id, "device_id")
    installed = (data or {}).get("installed") is True
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("UPDATE pi_devices SET feedback_hardware_installed=%s WHERE id=%s RETURNING society_id", (installed, device_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Device not found")
            if row["society_id"]:
                cur.execute("UPDATE societies SET config_version = config_version + 1 WHERE id=%s", (row["society_id"],))
            log_audit(cur, user, row["society_id"] or 0, "FEEDBACK_HARDWARE_SET", {"device_id": device_id, "installed": installed})
        conn.commit()
        return {"device_id": device_id, "feedback_hardware_installed": installed}
    except Exception as e:
        conn.rollback(); raise e
    finally:
        conn.close()

@app.get("/api/super-admin/devices/{device_id}/credentials")
def list_device_credentials(device_id: str, user: dict = Depends(require_role("super_admin"))):
    device_id = require_uuid(device_id, "device_id")
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""SELECT key_id, status, origin, hash_alg, created_at, rotated_at, revoked_at, revoke_reason
                           FROM pi_device_credentials WHERE device_id=%s ORDER BY created_at DESC""", (device_id,))
            rows = cur.fetchall()
            for r in rows:
                for k in ("created_at", "rotated_at", "revoked_at"):
                    r[k] = r[k].isoformat() if r.get(k) else None
            return {"device_id": device_id, "credentials": rows}
    finally:
        conn.close()

@app.get("/api/super-admin/metrics")
def resource_metrics(user: dict = Depends(require_role("super_admin"))):
    """In-memory per-device/day resource counters (process-local, reset on restart)."""
    today = datetime.now(timezone.utc).date().isoformat()
    with METRICS_LOCK:
        rows = [{"day": k[0], "device_id": k[1], "metric": k[2], "value": v} for k, v in METRICS.items() if k[0] >= today]
    return {"day": today, "counters": sorted(rows, key=lambda r: (r["device_id"], r["metric"]))}

@app.post("/api/super-admin/maintenance/archive-commands")
def maintenance_archive_commands(data: dict | None = None, user: dict = Depends(require_role("super_admin"))):
    """Daily maintenance (invoke from a cron/Render job). One bounded batch per call; rerun-safe."""
    batch = int((data or {}).get("batch_size", 500)) if isinstance(data, dict) else 500
    conn = get_db()
    try:
        with conn.cursor() as cur:
            result = archive_terminal_commands(cur, batch)
            if result["archived"]:
                log_audit(cur, user, 0, "COMMANDS_ARCHIVED", result)
        conn.commit()
        return result
    except Exception as e:
        conn.rollback(); raise e
    finally:
        conn.close()

# ---------------------------------------------------------------- T9 provisioning
@app.post("/api/super-admin/societies")
def create_society(data: dict, user: dict = Depends(require_role("super_admin"))):
    """Create-only (no id, no device re-assignment). Single transaction, id from RETURNING."""
    name = str((data or {}).get("name", "")).strip()[:120]
    if not name:
        raise HTTPException(400, "name required")
    location = str(data.get("location") or "").strip()[:200]
    try:
        reset_day = int(data.get("reset_day", DEFAULT_RESET_DAY))
    except (TypeError, ValueError):
        raise HTTPException(400, "reset_day must be an integer")
    if not 1 <= reset_day <= 28:
        raise HTTPException(400, "reset_day must be 1-28")
    conn = get_db()
    try:
        with conn.cursor() as cur:
            assert_society_name_available(cur, name)
            cur.execute("""INSERT INTO societies (name, location, plan, status, tailscale_ip, pi_port, society_code, config_version, reset_day)
                           VALUES (%s, %s, 'Basic', 'active', '', 5000, %s, 1, %s) RETURNING id""",
                        (name, location, f"SOC-{name[:4].upper()}", reset_day))
            sid = cur.fetchone()["id"]
            log_audit(cur, user, sid, "CREATE_SOCIETY", {"name": name, "location": location, "reset_day": reset_day, "origin": "provisioning"})
        conn.commit()
    except Exception as e:
        conn.rollback(); society_conflict_or_raise(e)
    finally:
        conn.close()
    return {"message": "Society created", "society_id": sid, "name": name, "location": location, "reset_day": reset_day}

@app.post("/api/super-admin/users")
def create_user(data: dict, user: dict = Depends(require_role("super_admin"))):
    """Create society_admin/member bound to a society. Temporary password is generated
    server-side and returned ONCE (never stored in plaintext, never logged)."""
    role = data.get("role")
    if role not in ("society_admin", "member"):
        raise HTTPException(400, "role must be society_admin or member")
    try: sid = int(data.get("society_id"))
    except (TypeError, ValueError): raise HTTPException(400, "society_id required")
    email = str(data.get("email", "")).strip().lower()[:254]; name = str(data.get("name", "")).strip()[:120]
    if "@" not in email or not name:
        raise HTTPException(400, "email and name required")
    temp_password = secrets.token_urlsafe(16)
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM societies WHERE id=%s AND status='active'", (sid,))
            if not cur.fetchone(): raise HTTPException(404, "Society not found")
            cur.execute("SELECT id FROM users WHERE email=%s", (email,))
            if cur.fetchone(): raise HTTPException(409, "Email already exists")
            cur.execute("INSERT INTO users (email, name, role, society_id, password) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                        (email, name, role, sid, bcrypt.hashpw(str(temp_password).encode(), bcrypt.gensalt()).decode()))
            uid = cur.fetchone()["id"]
            log_audit(cur, user, sid, "USER_CREATED", {"user_id": uid, "email": email, "role": role})
        conn.commit()
    except Exception as e:
        conn.rollback(); raise e
    finally:
        conn.close()
    return {"user_id": uid, "email": email, "role": role, "society_id": sid, "temporary_password": temp_password}

@app.post("/api/admin/devices/register")
def register_device(data: dict, user: dict = Depends(get_current_user)):
    """Society admin registers a Pi in THEIR society (server-side tenant); super admin may target one.
    Credential comes from the existing 0005 lifecycle; secret returned exactly once."""
    if user.get("role") not in ("super_admin", "society_admin"):
        raise HTTPException(403, "Insufficient permissions")
    if user["role"] == "super_admin":
        try: sid = int(data.get("society_id"))
        except (TypeError, ValueError): raise HTTPException(400, "society_id required")
    else:
        sid = user.get("society_id")  # never from the browser
        if not sid: raise HTTPException(403, "No society bound to this account")
    name = str(data.get("name", "")).strip()[:80] or "Pi Controller"
    profile = data.get("hardware_profile") or "EMS-4CH-v1"
    if profile not in ("EMS-4CH-v1",):
        raise HTTPException(400, "Unknown hardware_profile")
    feedback = data.get("feedback_hardware_installed") is True  # never assumed installed
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM societies WHERE id=%s AND status='active'", (sid,))
            if not cur.fetchone(): raise HTTPException(404, "Society not found")
            device_id = str(uuid.uuid4()); secret = new_device_secret()
            cur.execute("""INSERT INTO pi_devices (id, society_id, name, api_key_hash, status, hardware_profile, feedback_hardware_installed)
                           VALUES (%s, %s, %s, %s, 'ASSIGNED', %s, %s)""", (device_id, sid, name, hash_api_key(secret), profile, feedback))
            issued = issue_device_credential(cur, device_id, user, "register", secret=secret)
            log_audit(cur, user, sid, "DEVICE_REGISTERED", {"device_id": device_id, "name": name, "hardware_profile": profile,
                                                            "feedback_hardware_installed": feedback, "key_id": issued["key_id"]})
        conn.commit()
    except Exception as e:
        conn.rollback(); raise e
    finally:
        conn.close()
    return {"device_id": device_id, "society_id": sid, "name": name, "key_id": issued["key_id"], "api_key": secret}

@app.get("/api/admin/devices")
def list_society_devices(request: Request, user: dict = Depends(get_current_user)):
    """Lightweight device list for the tenant. society_admin/member: tenant from session only
    (browser-supplied society_id is ignored). super_admin: ?society_id= required. One query."""
    if user.get("role") == "super_admin":
        try: sid = int(request.query_params.get("society_id", ""))
        except (TypeError, ValueError): raise HTTPException(400, "society_id required")
    else:
        sid = user.get("society_id")
        if not sid: raise HTTPException(403, "No society bound to this account")
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""SELECT d.id, d.name, d.status, d.hardware_profile, d.feedback_hardware_installed, d.firmware_version,
                                  d.last_seen, c.key_id, p.last_sync, COALESCE(p.config_state, 'DESIRED') AS config_state,
                                  p.ota_state, p.disk_free_mb
                           FROM pi_devices d
                           LEFT JOIN pi_device_credentials c ON c.device_id = d.id AND c.status = 'active'
                           LEFT JOIN pi_state p ON p.device_id = d.id
                           WHERE d.society_id = %s ORDER BY d.name ASC, d.id ASC""", (sid,))
            rows = cur.fetchall()
            out = []
            for r in rows:
                out.append({
                    "id": str(r["id"]), "name": r["name"], "status": r["status"], "hardware_profile": r["hardware_profile"],
                    "feedback_hardware_installed": bool(r["feedback_hardware_installed"]), "firmware_version": r["firmware_version"],
                    "key_id": r["key_id"], "credential_state": "active" if r["key_id"] else "none",
                    "last_seen": r["last_seen"].isoformat() if r.get("last_seen") else None,
                    "last_sync": r["last_sync"].isoformat() if r.get("last_sync") else None,
                    "online": is_pi_online(r),
                    "config_state": r["config_state"], "ota_state": r["ota_state"],
                    "storage_state": "FAILED" if (r.get("disk_free_mb") is not None and r["disk_free_mb"] < 0) else ("UNKNOWN" if r.get("disk_free_mb") is None else "OK"),
                })
            return {"society_id": sid, "devices": out}
    finally:
        conn.close()

@app.get("/api/super-admin/devices")
def get_devices(user: dict = Depends(require_role("super_admin"))):
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT d.id, d.society_id, d.name, d.firmware_version, d.last_seen, d.status, s.name as society_name,
                       s.config_version, p.desired_config_hash, p.applied_config_version, p.applied_config_hash,
                       p.applied_config_at, COALESCE(p.config_state, 'DESIRED') AS config_state, p.config_error,
                       p.ota_state, p.ota_desired_version, p.ota_last_error, p.disk_free_mb, p.last_sync
                FROM pi_devices d
                LEFT JOIN societies s ON d.society_id = s.id
                LEFT JOIN pi_state p ON p.device_id = d.id
                ORDER BY d.name ASC
            """)
            devices = cur.fetchall()
            for d in devices:
                d["id"] = str(d["id"])
                d["society_id"] = str(d["society_id"]) if d["society_id"] else None
                d["applied_config_at"] = d["applied_config_at"].isoformat() if d.get("applied_config_at") else None
                d["last_sync"] = d["last_sync"].isoformat() if d.get("last_sync") else None
                d["storage_state"] = "FAILED" if (d.get("disk_free_mb") is not None and d["disk_free_mb"] < 0) else ("UNKNOWN" if d.get("disk_free_mb") is None else "OK")
            return devices
    finally:
        conn.close()

@app.post("/api/super-admin/devices/save")
def save_device(data: dict, user: dict = Depends(require_role("super_admin"))):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            society_id_raw = data.get("society_id")
            if society_id_raw and society_id_raw != "":
                society_id = int(society_id_raw)
                status = "ASSIGNED"
            else:
                society_id = None
                status = "INVENTORY"

            name = data.get("name", "New Pi Device")
            device_id = data.get("id")

            if device_id:
                cur.execute("SELECT status FROM pi_devices WHERE id=%s FOR UPDATE", (device_id,))
                existing = cur.fetchone()
                if not existing:
                    raise HTTPException(404, "Device not found")
                if str(existing["status"]).upper() == "RETIRED":
                    raise HTTPException(409, "Retired device cannot be edited; create a new device")
                if data.get("api_key"):
                    cur.execute("UPDATE pi_devices SET name=%s, society_id=%s, status=%s WHERE id=%s",
                                (name, society_id, status, device_id))
                    issued = issue_device_credential(cur, device_id, user, "admin_save", secret=data["api_key"])
                else:
                    cur.execute("UPDATE pi_devices SET name=%s, society_id=%s, status=%s WHERE id=%s",
                                (name, society_id, status, device_id))
                    issued = None
                log_audit(cur, user, society_id if society_id else 0, "UPDATE_DEVICE", {"device_id": device_id, "name": name})
                conn.commit()
                return {"message": "Device updated", **({"key_id": issued["key_id"]} if issued else {})}
            else:
                device_id = str(uuid.uuid4())
                raw_api_key = data.get("api_key") or new_device_secret()

                cur.execute("""INSERT INTO pi_devices (id, society_id, name, api_key_hash, status)
                               VALUES (%s, %s, %s, %s, %s)""",
                            (device_id, society_id, name, hash_api_key(raw_api_key), status))
                issued = issue_device_credential(cur, device_id, user, "admin_create", secret=raw_api_key)
                log_audit(cur, user, society_id if society_id else 0, "CREATE_DEVICE", {"device_id": device_id, "name": name})
                conn.commit()
                return {"message": "Device created", "device_id": device_id, "api_key": raw_api_key, "key_id": issued["key_id"]}
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

@app.post("/api/super-admin/devices/delete")
def delete_device(data: dict, user: dict = Depends(require_role("super_admin"))):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            device_id = require_uuid(data.get("id"), "id")
            # RED 3 Fix: Do not DELETE. Retire to preserve history & audit logs.
            cur.execute("UPDATE pi_devices SET status='RETIRED', society_id=NULL WHERE id = %s", (device_id,))
            revoke_device_credentials(cur, device_id, user, "DEVICE_RETIRED")
            log_audit(cur, user, 0, "RETIRE_DEVICE", {"device_id": device_id})
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
    return {"message": "Device retired successfully"}

# ================================================================
# SUPER-ADMIN — USERS & FIRMWARE
# ================================================================

@app.get("/api/super-admin/users")
def get_users(user: dict = Depends(require_role("super_admin"))):
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT u.id, u.email, u.name, u.role, u.society_id, s.name as society_name FROM users u LEFT JOIN societies s ON u.society_id = s.id")
            users = cur.fetchall()
            for u in users:
                u["society_id"] = str(u["society_id"]) if u["society_id"] else None
            return users
    finally:
        conn.close()

@app.post("/api/super-admin/users/save")
def save_user(data: dict, user: dict = Depends(require_role("super_admin"))):
    role = data.get("role")
    if role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role. Must be one of {VALID_ROLES}")

    society_id = data.get("society_id")
    if role != "super_admin" and not society_id:
        raise HTTPException(400, "society_id is required for non-super_admin roles")

    conn = get_db()
    try:
        with conn.cursor() as cur:
            uid = data.get("id")
            if uid:
                if data.get("password"):
                    cur.execute("UPDATE users SET email=%s, name=%s, role=%s, society_id=%s, password=%s WHERE id=%s",
                                (data["email"], data["name"], role, society_id, bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode(), uid))
                else:
                    cur.execute("UPDATE users SET email=%s, name=%s, role=%s, society_id=%s WHERE id=%s",
                                (data["email"], data["name"], role, society_id, uid))
            else:
                if not data.get("password"):
                    raise HTTPException(400, "Password required")
                cur.execute("INSERT INTO users (email, name, role, society_id, password) VALUES (%s, %s, %s, %s, %s)",
                            (data["email"], data["name"], role, society_id, bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode()))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
    return {"message": "Saved"}

@app.post("/api/super-admin/users/delete")
def delete_user(data: dict, user: dict = Depends(require_role("super_admin"))):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (data.get("id"),))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
    return {"message": "Deleted"}

@app.get("/api/super-admin/firmware/versions")
def get_firmware_versions(user: dict = Depends(require_role("super_admin"))):
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT version, changelog, forced, created_at, updated_at FROM firmware_versions ORDER BY created_at DESC")
            versions = cur.fetchall()
            return versions
    finally:
        conn.close()

@app.post("/api/super-admin/firmware/save")
def save_firmware_version(data: dict, user: dict = Depends(require_role("super_admin"))):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            version = str(data.get("version", "")).strip()
            code = data.get("code", "")
            changelog = data.get("changelog", "")
            forced = bool(data.get("forced", False))
            if not version or not isinstance(code, str) or not code:
                raise HTTPException(400, "Version and code required")
            if len(code.encode("utf-8")) > OTA_MAX_ARTIFACT_BYTES:
                raise HTTPException(413, "Firmware artifact too large")
            # Signed release metadata (signature produced OFFLINE with the private key; the
            # backend never signs). sha256 is recomputed here and must match if supplied.
            sha256 = hashlib.sha256(code.encode("utf-8")).hexdigest()
            supplied_sha = str(data.get("sha256") or "").strip().lower()
            if supplied_sha and supplied_sha != sha256:
                raise HTTPException(400, "sha256 does not match code")
            signature = data.get("signature"); key_id = data.get("key_id"); min_fw = data.get("min_firmware_version")
            for name, val in (("signature", signature), ("key_id", key_id), ("min_firmware_version", min_fw)):
                if val is not None and (not isinstance(val, str) or len(val) > 512):
                    raise HTTPException(400, f"{name} must be a string")
            if forced and not (signature and key_id):
                raise HTTPException(400, "Only signed releases (signature + key_id) can be forced to devices")
            now = datetime.now(timezone.utc)
            if forced:
                cur.execute("UPDATE firmware_versions SET forced = FALSE")
            cur.execute("""INSERT INTO firmware_versions (version, code, changelog, forced, created_at, updated_at, sha256, signature, key_id, min_firmware_version)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (version) DO UPDATE SET code=EXCLUDED.code, changelog=EXCLUDED.changelog, forced=EXCLUDED.forced,
                               updated_at=EXCLUDED.updated_at, sha256=EXCLUDED.sha256, signature=EXCLUDED.signature,
                               key_id=EXCLUDED.key_id, min_firmware_version=EXCLUDED.min_firmware_version""",
                        (version, code, changelog, forced, now, now, sha256, signature, key_id, min_fw))
            log_audit(cur, user, 0, "FIRMWARE_RELEASE_SAVED", {"version": version, "sha256": sha256, "key_id": key_id, "forced": forced, "signed": bool(signature)})
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
    return {"message": "Saved"}

@app.post("/api/super-admin/firmware/delete")
def delete_firmware_version(data: dict, user: dict = Depends(require_role("super_admin"))):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM firmware_versions WHERE version = %s", (data.get("version"),))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
    return {"message": "Deleted"}

@app.post("/api/super-admin/firmware/force")
def force_firmware(data: dict, user: dict = Depends(require_role("super_admin"))):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT signature, key_id FROM firmware_versions WHERE version = %s", (data.get("version"),))
            rel = cur.fetchone()
            if not rel:
                raise HTTPException(404, "Version not found")
            if not (rel["signature"] and rel["key_id"]):
                raise HTTPException(400, "Only signed releases can be forced to devices")
            cur.execute("UPDATE firmware_versions SET forced = FALSE")
            cur.execute("UPDATE firmware_versions SET forced = TRUE WHERE version = %s", (data.get("version"),))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
    return {"message": "Force flag updated"}

@app.get("/api/pi/firmware-download")
@limiter.limit("10/minute", key_func=pi_rate_key)
def download_firmware(
    request: Request,
    version: str,
    x_device_id: str | None = Header(None, alias="X-Device-ID"),
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
):
    device_id, _society_id = authenticate_pi({}, x_device_id, x_api_key)
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT version, code, sha256, signature, key_id, min_firmware_version FROM firmware_versions WHERE version = %s", (version,))
            fv = cur.fetchone()
            if not fv:
                raise HTTPException(404, "Version not found")
            if not (fv["signature"] and fv["key_id"]):
                raise HTTPException(409, "Release is not signed; refusing to serve to devices")
            return JSONResponse({"version": fv["version"], "code": fv["code"],
                                 "sha256": fv["sha256"] or hashlib.sha256(fv["code"].encode("utf-8")).hexdigest(),
                                 "signature": fv["signature"], "key_id": fv["key_id"],
                                 "min_firmware_version": fv["min_firmware_version"]},
                                headers={"X-EMS-Device-ID": str(device_id)})
    finally:
        conn.close()

# ================================================================
# PI SYNC (Returns canonical config for THIS specific Pi)
# ================================================================

@app.post("/api/pi/sync")
@limiter.limit("90/minute", key_func=pi_rate_key)
def pi_sync(
    request: Request,
    payload: dict,
    x_device_id: str | None = Header(None, alias="X-Device-ID"),
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
):
    device_id, society_id = authenticate_pi(payload, x_device_id, x_api_key)
    now = datetime.now(timezone.utc)
    metric_inc("sync_requests", device_id)
    metric_inc("db_writes", device_id, 2)  # pi_devices.last_seen + pi_state upsert (existing writes, measured)
    fb = payload.get("flashBytesToday")
    if isinstance(fb, (int, float)) and not isinstance(fb, bool) and fb >= 0:
        with METRICS_LOCK:  # gauge, not counter: latest Pi-reported daily flash bytes
            METRICS[(now.date().isoformat(), str(device_id), "pi_flash_bytes_today")] = int(fb)

    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("UPDATE pi_devices SET last_seen = %s, firmware_version = %s WHERE id = %s",
                        (now, payload.get("firmwareVersion", "unknown"), device_id))

            slots_payload = payload.get("slots", payload.get("wings", {}))
            for slot_code, w in slots_payload.items():
                if slot_code not in SLOTS: continue
                physical_toggle = w.get("physical_toggle", w.get("physicalToggle", "UNKNOWN"))
                if isinstance(physical_toggle, bool): physical_toggle = "ON" if physical_toggle else "OFF"
                physical_toggle = str(physical_toggle).upper()
                if physical_toggle not in ("ON", "OFF", "UNKNOWN"): physical_toggle = "UNKNOWN"

                cur.execute("""INSERT INTO slot_state (device_id, slot, physical_toggle, used_days, clicks)
                               VALUES (%s, %s, %s, %s, %s)
                               ON CONFLICT (device_id, slot) DO UPDATE SET
                               physical_toggle=EXCLUDED.physical_toggle, used_days=EXCLUDED.used_days, clicks=EXCLUDED.clicks""",
                            (device_id, slot_code, physical_toggle, int(w.get("used_days", w.get("usedDays", 0))), int(w.get("clicks", 0))))

            cur.execute("SELECT config_version, reset_day FROM societies WHERE id = %s", (society_id,))
            soc = cur.fetchone()
            cloud_config_version = soc["config_version"] if soc else 0
            canonical_reset_day = int((soc or {}).get("reset_day") or DEFAULT_RESET_DAY)

            cur.execute("SELECT hardware_profile, feedback_hardware_installed FROM pi_devices WHERE id = %s", (device_id,))
            dev_info = cur.fetchone()

            cur.execute("SELECT slot, target_days, disabled, display_name, feedback_enabled FROM slot_configs WHERE device_id = %s", (device_id,))
            configs = cur.fetchall()
            dev_feedback = bool(dev_info["feedback_hardware_installed"])
            slot_configs = {c["slot"]: {
                "target_days": c["target_days"],
                "disabled": c["disabled"],
                "display_name": c["display_name"],
                "feedback_enabled": bool(c["feedback_enabled"]) and dev_feedback
            } for c in configs}

            # --- T7: desired hash is computed from EXACTLY what this device receives below ---
            desired_hash = config_hash(canonical_device_config(
                dev_info["hardware_profile"], dev_feedback, canonical_reset_day, slot_configs))

            # Pi-reported applied identity (optional: legacy firmware omits all of it).
            raw_av = payload.get("applied_config_version")
            raw_ah = payload.get("applied_config_hash")
            raw_at = payload.get("applied_config_at")
            raw_err = payload.get("config_apply_error")
            reports_applied = raw_av is not None or raw_ah is not None
            if reports_applied:
                if isinstance(raw_av, bool) or not isinstance(raw_av, int) or raw_av < 0:
                    raise HTTPException(400, "applied_config_version must be a non-negative integer")
                if not isinstance(raw_ah, str) or len(raw_ah) != 64 or any(c not in "0123456789abcdef" for c in raw_ah):
                    raise HTTPException(400, "applied_config_hash must be a 64-char lowercase hex sha256")
                if raw_at is not None:
                    if not isinstance(raw_at, str): raise HTTPException(400, "applied_config_at must be an ISO-8601 string")
                    try: raw_at = datetime.fromisoformat(raw_at)
                    except ValueError: raise HTTPException(400, "applied_config_at must be an ISO-8601 string")
            if raw_err is not None and raw_err not in CONFIG_ERROR_CODES:
                raise HTTPException(400, "config_apply_error must be a known code")

            # --- Signed OTA: desired = the single forced SIGNED release; Pi-reported state validated.
            cur.execute("SELECT version FROM firmware_versions WHERE forced = TRUE AND signature IS NOT NULL AND key_id IS NOT NULL LIMIT 1")
            forced_row = cur.fetchone()
            ota_desired = forced_row["version"] if forced_row else None
            ota_state = payload.get("otaState")
            if ota_state is not None and ota_state not in OTA_STATES:
                raise HTTPException(400, "otaState must be a known OTA state")
            for f in ("otaVersion", "otaError", "lastGoodFirmwareVersion", "firmwareVersion"):
                if payload.get(f) is not None and (not isinstance(payload.get(f), str) or len(payload[f]) > 128):
                    raise HTTPException(400, f"{f} must be a short string")
            ota_attempts = payload.get("otaAttempts")
            if ota_attempts is not None and (isinstance(ota_attempts, bool) or not isinstance(ota_attempts, int) or ota_attempts < 0):
                raise HTTPException(400, "otaAttempts must be a non-negative integer")

            cur.execute("SELECT applied_config_version, applied_config_hash, applied_config_at, ota_state, ota_version, ota_attempts, ota_last_error, ota_updated_at, last_good_firmware_version FROM pi_state WHERE device_id=%s", (device_id,))
            prev = cur.fetchone() or {}
            ota_changed = ota_state is not None and (ota_state != prev.get("ota_state") or payload.get("otaVersion") != prev.get("ota_version")
                                                     or payload.get("otaError") != prev.get("ota_last_error"))
            eff_ota_state = ota_state if ota_state is not None else prev.get("ota_state")
            eff_ota_version = payload.get("otaVersion") if ota_state is not None else prev.get("ota_version")
            eff_ota_attempts = ota_attempts if ota_state is not None else prev.get("ota_attempts")
            eff_ota_error = payload.get("otaError") if ota_state is not None else prev.get("ota_last_error")
            eff_ota_updated = now if ota_changed else prev.get("ota_updated_at")
            eff_last_good = payload.get("lastGoodFirmwareVersion") if ota_state is not None else prev.get("last_good_firmware_version")
            # Legacy sync (no fields) must never erase stored convergence evidence.
            eff_av = raw_av if reports_applied else prev.get("applied_config_version")
            eff_ah = raw_ah if reports_applied else prev.get("applied_config_hash")
            eff_at = (raw_at if raw_at is not None else now) if reports_applied else prev.get("applied_config_at")
            config_state = derive_config_state(cloud_config_version, desired_hash, eff_av, eff_ah, raw_err)

            cur.execute("""INSERT INTO pi_state (device_id, active_slot, reset_day, emergency_stop, uptime_seconds, cpu_temp, disk_free_mb, last_sync, boot_count, last_shutdown_reason, clock_source, watchdog_enabled, last_reboot_reason, config_version,
                                                 desired_config_hash, applied_config_version, applied_config_hash, applied_config_at, config_state, config_error,
                                                 ota_desired_version, ota_state, ota_version, ota_attempts, ota_last_error, ota_updated_at, last_good_firmware_version)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (device_id) DO UPDATE SET
                           active_slot=EXCLUDED.active_slot, reset_day=EXCLUDED.reset_day, emergency_stop=EXCLUDED.emergency_stop,
                           uptime_seconds=EXCLUDED.uptime_seconds, cpu_temp=EXCLUDED.cpu_temp, disk_free_mb=EXCLUDED.disk_free_mb,
                           last_sync=EXCLUDED.last_sync, boot_count=EXCLUDED.boot_count, last_shutdown_reason=EXCLUDED.last_shutdown_reason,
                           clock_source=EXCLUDED.clock_source, watchdog_enabled=EXCLUDED.watchdog_enabled, last_reboot_reason=EXCLUDED.last_reboot_reason,
                           config_version=EXCLUDED.config_version, desired_config_hash=EXCLUDED.desired_config_hash,
                           applied_config_version=EXCLUDED.applied_config_version, applied_config_hash=EXCLUDED.applied_config_hash,
                           applied_config_at=EXCLUDED.applied_config_at, config_state=EXCLUDED.config_state, config_error=EXCLUDED.config_error,
                           ota_desired_version=EXCLUDED.ota_desired_version, ota_state=EXCLUDED.ota_state, ota_version=EXCLUDED.ota_version,
                           ota_attempts=EXCLUDED.ota_attempts, ota_last_error=EXCLUDED.ota_last_error, ota_updated_at=EXCLUDED.ota_updated_at,
                           last_good_firmware_version=EXCLUDED.last_good_firmware_version""",
                        (device_id, payload.get("active_slot", payload.get("activeWing")), int(payload.get("resetDay", DEFAULT_RESET_DAY)),
                         bool(payload.get("emergencyStop", False)), int(payload.get("uptimeSeconds", 0)),
                         float(payload.get("cpuTemp", 0)), float(payload.get("diskFreeMB", 0)), now, int(payload.get("bootCount", 0)),
                         payload.get("lastShutdownReason", ""), payload.get("clockSource", ""), bool(payload.get("watchdogEnabled", False)),
                         payload.get("lastRebootReason", ""), cloud_config_version,
                         desired_hash, eff_av, eff_ah, eff_at, config_state, raw_err,
                         ota_desired, eff_ota_state, eff_ota_version, eff_ota_attempts, eff_ota_error, eff_ota_updated, eff_last_good))

            for event in payload.get("events", []):
                ev_id = event.get("eventId")
                if not ev_id: continue
                metric_inc("events", device_id); metric_inc("db_writes", device_id)
                cur.execute("""INSERT INTO pi_events (device_id, event_id, timestamp, type, message)
                               VALUES (%s, %s, %s, %s, %s)
                               ON CONFLICT (event_id) DO NOTHING""",
                            (device_id, ev_id, event.get("timestamp", now), event.get("type", "system"), event.get("message", "")))

            # --- T6 reconciliation: what the Pi says it actually executed -----------
            # Sequence is primary evidence; the bounded id list is secondary evidence.
            raw_seq = payload.get("last_executed_sequence")
            if raw_seq is None:
                last_exec_seq = 0
            elif isinstance(raw_seq, bool) or not isinstance(raw_seq, int) or raw_seq < 0:
                raise HTTPException(400, "last_executed_sequence must be a non-negative integer")
            else:
                last_exec_seq = raw_seq
            raw_ids = payload.get("executed_command_ids") or []
            if not isinstance(raw_ids, list) or not all(isinstance(i, str) for i in raw_ids):
                raise HTTPException(400, "executed_command_ids must be a list of command id strings")
            executed_ids = list(dict.fromkeys(raw_ids[:EXECUTED_IDS_MAX]))  # bounded, de-duplicated, order kept
            reconciled = 0
            if executed_ids:
                # Delivered (or lease-reclaimed) commands the Pi has executed: promote to
                # executing so they are never re-delivered; the Pi's ACK brings the terminal state.
                cur.execute("""UPDATE pi_commands SET status='delivered', delivered_at=COALESCE(delivered_at, %s)
                               WHERE device_id=%s AND status='queued' AND id::text = ANY(%s)""", (now, device_id, executed_ids))
                cur.execute("""UPDATE pi_commands SET status='executing', executing_at=COALESCE(executing_at, %s)
                               WHERE device_id=%s AND status='delivered' AND id::text = ANY(%s)""", (now, device_id, executed_ids))
                reconciled += cur.rowcount
            if last_exec_seq > 0:
                # Older delivered commands not in the id list: outcome unknown, never re-run.
                cur.execute("""UPDATE pi_commands SET status='unknown_after_reboot', error='SEQUENCE_SUPERSEDED'
                               WHERE device_id=%s AND status='delivered' AND sequence_no IS NOT NULL AND sequence_no <= %s
                               AND NOT (id::text = ANY(%s))""", (device_id, last_exec_seq, executed_ids))
                reconciled += cur.rowcount

            # Reclaim a delivery lease, but never extend the absolute command expiry.
            cur.execute(
                "UPDATE pi_commands SET status='queued', delivered_at=NULL WHERE device_id=%s AND status='delivered' AND delivered_at < %s AND expires_at > %s",
                (device_id, now - timedelta(seconds=COMMAND_DELIVERY_LEASE_SECONDS), now),
            )
            cur.execute(
                "UPDATE pi_commands SET status='expired', error='COMMAND_EXPIRED' WHERE device_id=%s AND status IN ('queued','delivered') AND expires_at <= %s",
                (device_id, now),
            )

            cur.execute("SELECT * FROM pi_commands WHERE device_id = %s AND status = 'queued' ORDER BY sequence_no ASC NULLS FIRST, created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED", (device_id,))
            cmd = cur.fetchone()

            reply = {
                "success": True,
                "command": None,
                "command_id": None,
                "config_version": cloud_config_version,
                "config_hash": desired_hash,
                "config_state": config_state,
                "firmware_desired_version": ota_desired,
                "device_id": device_id,
                "hardware_profile": dev_info["hardware_profile"],
                "feedback_hardware_installed": dev_info["feedback_hardware_installed"],
                "slots": slot_configs,
                "resetDay": canonical_reset_day,
                "reconciled_commands": reconciled,
            }
            if cmd:
                # CAS delivery: only the worker that flips queued->delivered owns this attempt.
                cur.execute("""UPDATE pi_commands SET status='delivered', delivered_at=%s, attempt_count=attempt_count+1
                               WHERE id=%s AND status='queued' RETURNING attempt_count""", (now, cmd["id"]))
                delivered = cur.fetchone()
                if delivered:
                    reply["command"] = cmd["command"]
                    reply["command_id"] = str(cmd["id"])
                    reply["sequence_no"] = cmd.get("sequence_no")
                    reply["attempt"] = delivered["attempt_count"]
                    reply["expires_at"] = cmd["expires_at"].isoformat() if cmd.get("expires_at") else None
                    if cmd.get("slot"): reply["slot"] = cmd["slot"]
                    reply["params"] = cmd.get("params", {})

        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
    return reply

# ================================================================
# PI COMMAND ACK (Unified Contract)
# ================================================================

@app.post("/api/pi/command-ack")
@limiter.limit("120/minute", key_func=pi_rate_key)
def pi_command_ack(
    request: Request,
    payload: dict,
    x_device_id: str | None = Header(None, alias="X-Device-ID"),
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
):
    device_id, society_id = authenticate_pi(payload, x_device_id, x_api_key)
    command_id = payload.get("command_id")
    if not command_id:
        raise HTTPException(400, "command_id required")

    command_id = require_uuid(command_id, "command_id")
    attempt = payload.get("attempt")
    if attempt is not None and (isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0):
        raise HTTPException(400, "attempt must be a non-negative integer")
    status = str(payload.get("status", "")).lower()
    verification = str(payload.get("verification_state", "UNKNOWN")).upper()
    error = payload.get("error")
    allowed = set(COMMAND_TRANSITIONS)
    if status not in allowed:
        raise HTTPException(400, f"Invalid command status: {status}")

    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, command, slot, params, status, attempt_count FROM pi_commands WHERE id = %s AND device_id = %s FOR UPDATE",
                (command_id, device_id),
            )
            cmd = cur.fetchone()
            if not cmd:
                return {"success": True, "status": "unknown"}

            current = str(cmd["status"] or "queued").lower()
            if status == current:
                # Idempotent repeat of an already-applied status is harmless.
                return {"success": True, "status": current, "idempotent": True}
            path = resolve_ack_path(current, status, verification, str(cmd["command"]))
            if path is None:
                raise HTTPException(409, f"Invalid command transition {current} -> {status}")

            # Lease/attempt identity: a stale worker (older delivery attempt) may not commit.
            if attempt is not None and attempt != int(cmd["attempt_count"]):
                raise HTTPException(409, f"Stale command attempt {attempt}; current attempt is {cmd['attempt_count']}")

            now = datetime.now(timezone.utc)

            if status == "hardware_verified" and verification not in {
                "VERIFIED_ON", "VERIFIED_OFF", "GPIO_CONFIRMED"
            }:
                raise HTTPException(409, "HARDWARE_VERIFIED requires positive hardware verification")

            # Configuration commands are committed only after the Pi reports
            # terminal completion. Hardware commands never mutate configuration.
            if status == "completed":
                if cmd["command"] == "set_days":
                    slot = cmd["slot"]
                    days = int((cmd["params"] or {}).get("days", 0))
                    if slot not in SLOTS or not 1 <= days <= 31:
                        raise HTTPException(409, "Invalid set_days command data")
                    cur.execute(
                        "UPDATE slot_configs SET target_days = %s WHERE device_id = %s AND slot = %s",
                        (days, device_id, slot),
                    )
                    cur.execute(
                        "UPDATE societies SET config_version = config_version + 1 WHERE id = %s",
                        (society_id,),
                    )
                elif cmd["command"] == "set_reset_day":
                    day = int((cmd["params"] or {}).get("day", DEFAULT_RESET_DAY))
                    if not 1 <= day <= 28:
                        raise HTTPException(409, "Invalid reset day")
                    cur.execute(
                        "UPDATE societies SET reset_day=%s, config_version=config_version+1 WHERE id=%s",
                        (day, society_id),
                    )

            ts_map = {"executing": "executing_at", "hardware_verified": "hardware_verified_at",
                      "completed": "completed_at", "failed": "completed_at", "acked": "acked_at"}
            # Every hop the ACK implies gets its lifecycle timestamp (fast-forwarded hops included).
            ts_cols = [ts_map[s] for s in path if s in ts_map]
            ts_sql = "".join(f", {c}=COALESCE({c}, %s)" for c in ts_cols)
            ts_args = tuple(now for _ in ts_cols)
            # Compare-and-set on the status we observed under lock; rowcount proves the transition.
            cur.execute(
                f"UPDATE pi_commands SET status=%s, error=%s, result=%s, attempt_count=%s{ts_sql} WHERE id=%s AND device_id=%s AND status=%s",
                (status, error, verification, cmd["attempt_count"], *ts_args, command_id, device_id, current),
            )
            if cur.rowcount != 1:
                raise HTTPException(409, f"Command state changed concurrently (expected {current})")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"success": True, "status": status}

# ================================================================
# ADMIN & MEMBER ENDPOINTS (Multi-Pi Dashboard)
# ================================================================

@app.post("/api/admin/pi-command")
@limiter.limit("30/minute")
def queue_command(request: Request, data: dict, user: dict = Depends(get_current_user)):
    if user.get("role") not in {"super_admin", "society_admin"}:
        raise HTTPException(403, "Only super_admin or society_admin may issue commands")
    try: sid = int(data.get("society_id"))
    except (TypeError, ValueError): raise HTTPException(400, "Invalid society_id")

    if user.get("role") != "super_admin" and str(user.get("society_id")) != str(sid):
        raise HTTPException(403, "Cannot access other society data")

    command = str(data.get("command", ""))
    slot = str(data.get("slot", ""))
    device_id = data.get("device_id")
    params = dict(data.get("params", {}))
    device_id = require_uuid(device_id, "device_id")
    idempotency_key = data.get("idempotency_key")
    if idempotency_key is not None and not isinstance(idempotency_key, str):
        raise HTTPException(400, "idempotency_key must be a string")
    idempotency_key = idempotency_key.strip()[:128] if idempotency_key else None
    if idempotency_key is not None and not idempotency_key:
        idempotency_key = None

    validate_command(command, params, slot)

    conn = get_db()
    try:
        with conn.cursor() as cur:
            # The locked device row is the serialization point for both the
            # idempotency check and the per-device sequence allocation.
            cur.execute("SELECT id, status, next_command_sequence FROM pi_devices WHERE id=%s AND society_id=%s FOR UPDATE", (device_id, sid))
            dev = cur.fetchone()
            if not dev or str(dev["status"]).upper() != "ASSIGNED":
                raise HTTPException(404, "Active device not found in this society")

            if idempotency_key:
                cur.execute("SELECT id, command, slot, params, sequence_no FROM pi_commands WHERE device_id=%s AND idempotency_key=%s",
                            (device_id, idempotency_key))
                existing = cur.fetchone()
                if existing:
                    same = (existing["command"] == command and (existing["slot"] or "") == slot
                            and (existing["params"] or {}) == params)
                    if not same:
                        raise HTTPException(409, "idempotency_key already used for a different command")
                    conn.rollback()
                    return {"success": True, "message": "Command already queued", "command": command,
                            "command_id": str(existing["id"]), "sequence_no": existing["sequence_no"], "duplicate": True}

            sequence_no = int(dev["next_command_sequence"]) + 1
            cur.execute("UPDATE pi_devices SET next_command_sequence=%s WHERE id=%s", (sequence_no, device_id))

            command_id = str(uuid.uuid4())
            created = datetime.now(timezone.utc)
            metric_inc("commands_created", device_id); metric_inc("db_writes", device_id)
            cur.execute("""INSERT INTO pi_commands (id, device_id, command, slot, params, status, created_at, expires_at, idempotency_key, sequence_no)
                           VALUES (%s, %s, %s, %s, %s, 'queued', %s, %s, %s, %s)""",
                        (command_id, device_id, command, slot, psycopg.types.json.Json(params), created,
                         created + timedelta(seconds=COMMAND_EXPIRY_SECONDS), idempotency_key, sequence_no))

            log_audit(cur, user, sid, "QUEUE_COMMAND", {"command": command, "slot": slot, "device_id": device_id,
                                                        "command_id": command_id, "sequence_no": sequence_no,
                                                        "idempotency_key": idempotency_key})
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

    return {"success": True, "message": "Command queued", "command": command, "command_id": command_id,
            "sequence_no": sequence_no, "duplicate": False}

@app.get("/api/admin/dashboard")
def admin_dashboard(society_id: str, user: dict = Depends(require_society_access)):
    if not society_id: raise HTTPException(400, "society_id required")
    sid = int(society_id)

    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            # P0-3: society-level reset_day (single source: societies.reset_day) for the authorized tenant only.
            cur.execute("SELECT name, location, plan, society_code, status, reset_day FROM societies WHERE id = %s", (sid,))
            soc = cur.fetchone()
            if not soc:
                raise HTTPException(404, "Society not found")
            cur.execute("SELECT id, name, firmware_version FROM pi_devices WHERE society_id = %s ORDER BY name ASC", (sid,))
            devs = cur.fetchall()

            devices_data = []
            for dev in devs:
                cur.execute("SELECT * FROM pi_state WHERE device_id = %s", (dev["id"],))
                pi = cur.fetchone()

                slots_data = {}
                cur.execute("SELECT * FROM slot_configs WHERE device_id = %s", (dev["id"],))
                configs = cur.fetchall()
                for c in configs:
                    cur.execute("SELECT * FROM slot_state WHERE device_id = %s AND slot = %s", (dev["id"], c["slot"]))
                    st = cur.fetchone() or {}
                    slots_data[c["slot"]] = {
                        "used_days": st.get("used_days", 0),
                        "target_days": c["target_days"],
                        "status": "ACTIVE" if pi and pi.get("active_slot") == c["slot"] else "IDLE",
                        "display_name": c["display_name"],
                        "disabled": c["disabled"],
                        "physical_toggle": st.get("physical_toggle", "UNKNOWN"),
                        "visible": slot_is_visible(c, st),
                    }

                devices_data.append({
                    "id": str(dev["id"]),
                    "name": dev["name"],
                    "connected": is_pi_online(pi),
                    "active_slot": pi.get("active_slot") if pi else None,
                    "slots": slots_data,
                    # T7 convergence evidence (read-only exposure; no UI yet)
                    "config_version": pi.get("config_version") if pi else None,
                    "desired_config_hash": pi.get("desired_config_hash") if pi else None,
                    "applied_config_version": pi.get("applied_config_version") if pi else None,
                    "applied_config_hash": pi.get("applied_config_hash") if pi else None,
                    "applied_config_at": pi["applied_config_at"].isoformat() if pi and pi.get("applied_config_at") else None,
                    "config_state": (pi.get("config_state") if pi else None) or "DESIRED",
                    "config_error": pi.get("config_error") if pi else None,
                    "ota_state": pi.get("ota_state") if pi else None,
                    "storage_state": ("FAILED" if pi.get("disk_free_mb", 0) < 0 else "OK") if pi and pi.get("disk_free_mb") is not None else "UNKNOWN",
                    # Real Pi-reported telemetry already stored by /api/pi/sync (read-only exposure; None = never reported)
                    "firmware_version": dev.get("firmware_version"),
                    "last_sync": pi["last_sync"].isoformat() if pi and pi.get("last_sync") else None,
                    "telemetry": {
                        "cpu_temp": pi.get("cpu_temp") if pi else None,
                        "uptime_seconds": pi.get("uptime_seconds") if pi else None,
                        "boot_count": pi.get("boot_count") if pi else None,
                    },
                })

            return {
                "society_id": sid,
                "society": {"name": soc["name"], "location": soc["location"], "plan": soc["plan"], "society_code": soc["society_code"], "status": soc["status"]},
                "reset_day": soc["reset_day"] if soc["reset_day"] is not None else DEFAULT_RESET_DAY,
                "devices": devices_data,
            }
    finally:
        conn.close()

@app.get("/api/admin/pi-commands")
def get_pi_commands(society_id: str, device_id: str, limit: int = 25, user: dict = Depends(require_society_access)):
    """Read-only, tenant-scoped command history (newest first, bounded) for Last Response / logs."""
    sid = int(society_id); limit = max(1, min(int(limit), 50))
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""SELECT c.id, c.command, c.slot, c.params, c.sequence_no, c.status, c.result, c.error, c.attempt_count,
                                  c.created_at, c.delivered_at, c.executing_at, c.hardware_verified_at, c.completed_at, c.acked_at, c.expires_at
                           FROM pi_commands c JOIN pi_devices d ON d.id = c.device_id
                           WHERE d.society_id = %s AND c.device_id = %s
                           ORDER BY c.sequence_no DESC LIMIT %s""", (sid, device_id, limit))
            rows = cur.fetchall()
        iso = lambda v: v.isoformat() if v else None
        return {"commands": [{
            "id": str(r["id"]), "command": r["command"], "slot": r["slot"] or "", "params": r["params"] or {},
            "sequence_no": r["sequence_no"], "status": r["status"], "result": r["result"], "error": r["error"], "attempt_count": r["attempt_count"],
            "created_at": iso(r["created_at"]), "delivered_at": iso(r["delivered_at"]), "executing_at": iso(r["executing_at"]),
            "hardware_verified_at": iso(r["hardware_verified_at"]), "completed_at": iso(r["completed_at"]), "acked_at": iso(r["acked_at"]),
            "expires_at": iso(r["expires_at"]),
        } for r in rows]}
    finally:
        conn.close()

@app.get("/api/admin/pi-state")
def get_pi_state(society_id: str, user: dict = Depends(require_society_access)):
    return admin_dashboard(society_id, user)

def map_events(raw):
    out = []
    for e in raw:
        out.append({
            "id": e["id"],
            "ts": e["timestamp"].isoformat() if e.get("timestamp") else "",
            "level": (e.get("type","") or "").upper(),
            "msg": e.get("message","")
        })
    return out

@app.get("/api/admin/pi-events")
def get_pi_events(society_id: str, device_id: str = None, last_id: int = 0, latest: int = 0, user: dict = Depends(require_society_access)):
    """Default: incremental oldest-first after last_id (unchanged). latest=N (1..100): newest N first, bounded."""
    sid = int(society_id)
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            query = "SELECT e.* FROM pi_events e JOIN pi_devices d ON e.device_id = d.id WHERE d.society_id = %s AND e.id > %s"
            params = [sid, last_id]
            if device_id:
                query += " AND e.device_id = %s"
                params.append(device_id)
            if latest:
                query += " ORDER BY e.id DESC LIMIT %s"
                params.append(max(1, min(int(latest), 100)))
            else:
                query += " ORDER BY e.id ASC LIMIT 100"

            cur.execute(query, params)
            events = cur.fetchall()
            mapped = map_events(events)
            return {"events": mapped, "last_id": (max(e["id"] for e in mapped) if mapped else last_id)}
    finally:
        conn.close()

@app.get("/api/member/dashboard")
def member_dashboard(user: dict = Depends(get_current_user)):
    if user.get("role") != "member": raise HTTPException(403, "Members only")
    sid = user.get("society_id")
    if not sid: raise HTTPException(400, "No society assigned")

    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id, name FROM pi_devices WHERE society_id = %s ORDER BY name ASC", (sid,))
            devs = cur.fetchall()

            devices_data = []
            for dev in devs:
                cur.execute("SELECT * FROM pi_state WHERE device_id = %s", (dev["id"],))
                pi = cur.fetchone()

                slots_data = {}
                cur.execute("SELECT * FROM slot_configs WHERE device_id = %s", (dev["id"],))
                configs = cur.fetchall()
                for c in configs:
                    cur.execute("SELECT * FROM slot_state WHERE device_id = %s AND slot = %s", (dev["id"], c["slot"]))
                    st = cur.fetchone() or {}
                    if not slot_is_visible(c, st): continue
                    slots_data[c["slot"]] = {
                        "used_days": st.get("used_days", 0),
                        "target_days": c["target_days"],
                        "display_name": c["display_name"],
                        "physical_toggle": st.get("physical_toggle", "UNKNOWN"),
                    }

                devices_data.append({
                    "id": str(dev["id"]),
                    "name": dev["name"],
                    "connected": is_pi_online(pi),
                    "active_slot": pi.get("active_slot") if pi else None,
                    "slots": slots_data
                })

            return { "devices": devices_data, "reset_day": DEFAULT_RESET_DAY }
    finally:
        conn.close()

@app.get("/api/member/events")
def member_events(last_id: int = 0, user: dict = Depends(get_current_user)):
    if user.get("role") != "member": raise HTTPException(403, "Members only")
    sid = user.get("society_id")
    if not sid: raise HTTPException(400, "No society assigned")

    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT e.* FROM pi_events e JOIN pi_devices d ON e.device_id = d.id WHERE d.society_id = %s AND e.id > %s ORDER BY e.id ASC LIMIT 100", (sid, last_id))
            events = cur.fetchall()
            mapped = map_events(events)
            return {"events": mapped, "last_id": mapped[-1]["id"] if mapped else last_id}
    finally:
        conn.close()