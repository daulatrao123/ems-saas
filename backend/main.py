"""
EMS SaaS Backend v6.2.3 — Industrial Production (Strict Multi-Pi & 4-Slot Contract)
"""

import os
import time
import uuid
import hmac
import hashlib
import secrets
import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException, Depends, Header, Request, Response
from fastapi.responses import PlainTextResponse, JSONResponse
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
app = FastAPI(title="EMS SaaS API", version="6.4.0-targeted")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

DEFAULT_RESET_DAY = 15
SLOTS = ["A", "B", "C", "D"] # Strict 4-slot architecture
PI_ONLINE_THRESHOLD_SECONDS = 120
COMMAND_EXPIRY_SECONDS = 300
COMMAND_DELIVERY_LEASE_SECONDS = 120

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

def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()

def log_audit(cur, user: dict, society_id: int, action: str, details: dict):
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
    if not device_id:
        raise HTTPException(400, "Invalid deviceId")
    if not supplied_key:
        raise HTTPException(401, "Pi API key required")

    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, society_id, status FROM pi_devices WHERE id = %s AND api_key_hash = %s",
                (device_id, hash_api_key(supplied_key)),
            )
            dev = cur.fetchone()
            if not dev:
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
                        "slots": slots_data
                    })

                result.append({
                    "id": s["id"], "name": s["name"], "location": s["location"],
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
                cur.execute("""UPDATE societies SET name=%s, location=%s, reset_day=%s, config_version=config_version+1 WHERE id=%s""",
                            (society["name"], society["location"], society["reset_day"], sid))
                new_sid = int(sid)
            else:
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
        raise e
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
            cur.execute("UPDATE pi_devices SET status='RETIRED', society_id=NULL WHERE society_id=%s", (sid,))
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

@app.get("/api/super-admin/devices")
def get_devices(user: dict = Depends(require_role("super_admin"))):
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT d.id, d.society_id, d.name, d.firmware_version, d.last_seen, d.status, s.name as society_name
                FROM pi_devices d
                LEFT JOIN societies s ON d.society_id = s.id
                ORDER BY d.name ASC
            """)
            devices = cur.fetchall()
            for d in devices:
                d["id"] = str(d["id"])
                d["society_id"] = str(d["society_id"]) if d["society_id"] else None
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
                    api_key_hash = hash_api_key(data["api_key"])
                    cur.execute("UPDATE pi_devices SET name=%s, society_id=%s, status=%s, api_key_hash=%s WHERE id=%s",
                                (name, society_id, status, api_key_hash, device_id))
                else:
                    cur.execute("UPDATE pi_devices SET name=%s, society_id=%s, status=%s WHERE id=%s",
                                (name, society_id, status, device_id))
                log_audit(cur, user, society_id if society_id else 0, "UPDATE_DEVICE", {"device_id": device_id, "name": name})
                conn.commit()
                return {"message": "Device updated"}
            else:
                device_id = str(uuid.uuid4())
                raw_api_key = data.get("api_key") or str(uuid.uuid4())
                api_key_hash = hash_api_key(raw_api_key)

                cur.execute("""INSERT INTO pi_devices (id, society_id, name, api_key_hash, status)
                               VALUES (%s, %s, %s, %s, %s)""",
                            (device_id, society_id, name, api_key_hash, status))
                log_audit(cur, user, society_id if society_id else 0, "CREATE_DEVICE", {"device_id": device_id, "name": name})
                conn.commit()
                return {"message": "Device created", "device_id": device_id, "api_key": raw_api_key}
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
            device_id = data.get("id")
            # RED 3 Fix: Do not DELETE. Retire to preserve history & audit logs.
            cur.execute("UPDATE pi_devices SET status='RETIRED', society_id=NULL WHERE id = %s", (device_id,))
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
            version = data.get("version", "").strip()
            code = data.get("code", "")
            changelog = data.get("changelog", "")
            forced = data.get("forced", False)
            if not version or not code:
                raise HTTPException(400, "Version and code required")

            if forced:
                cur.execute("UPDATE firmware_versions SET forced = FALSE")

            cur.execute("""INSERT INTO firmware_versions (version, code, changelog, forced, created_at, updated_at)
                           VALUES (%s, %s, %s, %s, %s, %s)
                           ON CONFLICT (version) DO UPDATE SET code=%s, changelog=%s, forced=%s, updated_at=%s""",
                        (version, code, changelog, forced, datetime.now(timezone.utc), datetime.now(timezone.utc), code, changelog, forced, datetime.now(timezone.utc)))
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
def download_firmware(
    version: str,
    x_device_id: str | None = Header(None, alias="X-Device-ID"),
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
):
    device_id, _society_id = authenticate_pi({}, x_device_id, x_api_key)
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT code FROM firmware_versions WHERE version = %s", (version,))
            fv = cur.fetchone()
            if not fv:
                raise HTTPException(404, "Version not found")
            return PlainTextResponse(fv["code"], media_type="text/plain", headers={"X-EMS-Device-ID": str(device_id)})
    finally:
        conn.close()

# ================================================================
# PI SYNC (Returns canonical config for THIS specific Pi)
# ================================================================

@app.post("/api/pi/sync")
@limiter.limit("30/minute")
def pi_sync(
    request: Request,
    payload: dict,
    x_device_id: str | None = Header(None, alias="X-Device-ID"),
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
):
    device_id, society_id = authenticate_pi(payload, x_device_id, x_api_key)
    now = datetime.now(timezone.utc)

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

            cur.execute("SELECT config_version FROM societies WHERE id = %s", (society_id,))
            soc = cur.fetchone()
            cloud_config_version = soc["config_version"] if soc else 0

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

            cur.execute("""INSERT INTO pi_state (device_id, active_slot, reset_day, emergency_stop, uptime_seconds, cpu_temp, disk_free_mb, last_sync, boot_count, last_shutdown_reason, clock_source, watchdog_enabled, last_reboot_reason, config_version)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (device_id) DO UPDATE SET
                           active_slot=EXCLUDED.active_slot, reset_day=EXCLUDED.reset_day, emergency_stop=EXCLUDED.emergency_stop,
                           uptime_seconds=EXCLUDED.uptime_seconds, cpu_temp=EXCLUDED.cpu_temp, disk_free_mb=EXCLUDED.disk_free_mb,
                           last_sync=EXCLUDED.last_sync, boot_count=EXCLUDED.boot_count, last_shutdown_reason=EXCLUDED.last_shutdown_reason,
                           clock_source=EXCLUDED.clock_source, watchdog_enabled=EXCLUDED.watchdog_enabled, last_reboot_reason=EXCLUDED.last_reboot_reason""",
                        (device_id, payload.get("active_slot", payload.get("activeWing")), int(payload.get("resetDay", DEFAULT_RESET_DAY)),
                         bool(payload.get("emergencyStop", False)), int(payload.get("uptimeSeconds", 0)),
                         float(payload.get("cpuTemp", 0)), float(payload.get("diskFreeMB", 0)), now, int(payload.get("bootCount", 0)),
                         payload.get("lastShutdownReason", ""), payload.get("clockSource", ""), bool(payload.get("watchdogEnabled", False)),
                         payload.get("lastRebootReason", ""), cloud_config_version))

            for event in payload.get("events", []):
                ev_id = event.get("eventId")
                if not ev_id: continue
                cur.execute("""INSERT INTO pi_events (device_id, event_id, timestamp, type, message)
                               VALUES (%s, %s, %s, %s, %s)
                               ON CONFLICT (event_id) DO NOTHING""",
                            (device_id, ev_id, event.get("timestamp", now), event.get("type", "system"), event.get("message", "")))

            # Reclaim a delivery lease, but never extend the absolute command expiry.
            cur.execute(
                "UPDATE pi_commands SET status='queued', delivered_at=NULL WHERE device_id=%s AND status='delivered' AND delivered_at < %s AND expires_at > %s",
                (device_id, now - timedelta(seconds=COMMAND_DELIVERY_LEASE_SECONDS), now),
            )
            cur.execute(
                "UPDATE pi_commands SET status='expired', error='COMMAND_EXPIRED' WHERE device_id=%s AND status IN ('queued','delivered') AND expires_at <= %s",
                (device_id, now),
            )

            cur.execute("SELECT * FROM pi_commands WHERE device_id = %s AND status = 'queued' ORDER BY created_at ASC LIMIT 1", (device_id,))
            cmd = cur.fetchone()

            cur.execute("SELECT reset_day FROM societies WHERE id=%s", (society_id,))
            society_row = cur.fetchone()
            canonical_reset_day = int((society_row or {}).get("reset_day") or DEFAULT_RESET_DAY)

            reply = {
                "success": True,
                "command": None,
                "command_id": None,
                "config_version": cloud_config_version,
                "device_id": device_id,
                "hardware_profile": dev_info["hardware_profile"],
                "feedback_hardware_installed": dev_info["feedback_hardware_installed"],
                "slots": slot_configs,
                "resetDay": canonical_reset_day
            }
            if cmd:
                cur.execute("UPDATE pi_commands SET status = 'delivered', delivered_at = %s WHERE id = %s", (now, cmd["id"]))
                reply["command"] = cmd["command"]
                reply["command_id"] = str(cmd["id"])
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
def pi_command_ack(
    payload: dict,
    x_device_id: str | None = Header(None, alias="X-Device-ID"),
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
):
    device_id, society_id = authenticate_pi(payload, x_device_id, x_api_key)
    command_id = payload.get("command_id")
    if not command_id:
        raise HTTPException(400, "command_id required")

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
                "SELECT id, command, slot, params, status FROM pi_commands WHERE id = %s AND device_id = %s FOR UPDATE",
                (command_id, device_id),
            )
            cmd = cur.fetchone()
            if not cmd:
                return {"success": True, "status": "unknown"}

            current = str(cmd["status"] or "queued").lower()
            if status != current and status not in COMMAND_TRANSITIONS.get(current, set()):
                # Idempotent repeat of an already-applied status is harmless.
                if status == current:
                    return {"success": True, "status": current}
                raise HTTPException(409, f"Invalid command transition {current} -> {status}")

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

            if status == "acked":
                cur.execute(
                    "UPDATE pi_commands SET status='acked', acked_at=%s, result=%s, error=%s WHERE id=%s AND device_id=%s AND status IN ('completed','failed','expired')",
                    (now, verification, error, command_id, device_id),
                )
            else:
                cur.execute(
                    "UPDATE pi_commands SET status=%s, acked_at=NULL, error=%s, result=%s WHERE id=%s AND device_id=%s AND status=%s",
                    (status, error, verification, command_id, device_id, current),
                )

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

    validate_command(command, params, slot)

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, status FROM pi_devices WHERE id=%s AND society_id=%s", (device_id, sid))
            dev = cur.fetchone()
            if not dev or str(dev["status"]).upper() != "ASSIGNED":
                raise HTTPException(404, "Active device not found in this society")

            command_id = str(uuid.uuid4())
            created = datetime.now(timezone.utc)
            cur.execute("""INSERT INTO pi_commands (id, device_id, command, slot, params, status, created_at, expires_at)
                           VALUES (%s, %s, %s, %s, %s, 'queued', %s, %s)""",
                        (command_id, device_id, command, slot, psycopg.types.json.Json(params), created, created + timedelta(seconds=COMMAND_EXPIRY_SECONDS)))

            log_audit(cur, user, sid, "QUEUE_COMMAND", {"command": command, "slot": slot, "device_id": device_id})
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

    return {"success": True, "message": "Command queued", "command": command, "command_id": command_id}

@app.get("/api/admin/dashboard")
def admin_dashboard(society_id: str, user: dict = Depends(require_society_access)):
    if not society_id: raise HTTPException(400, "society_id required")
    sid = int(society_id)

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
                    "slots": slots_data
                })

            return { "society_id": sid, "devices": devices_data }
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
def get_pi_events(society_id: str, device_id: str = None, last_id: int = 0, user: dict = Depends(require_society_access)):
    sid = int(society_id)
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            query = "SELECT e.* FROM pi_events e JOIN pi_devices d ON e.device_id = d.id WHERE d.society_id = %s AND e.id > %s"
            params = [sid, last_id]
            if device_id:
                query += " AND e.device_id = %s"
                params.append(device_id)
            query += " ORDER BY e.id ASC LIMIT 100"

            cur.execute(query, params)
            events = cur.fetchall()
            mapped = map_events(events)
            return {"events": mapped, "last_id": mapped[-1]["id"] if mapped else last_id}
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