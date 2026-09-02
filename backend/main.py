"""
EMS SaaS Backend v5.2.0 — Industrial Production (Hardened)
====================================
- Explicit Database Transactions for multi-step operations.
- Audit logging no longer silently fails.
- Added Rate Limiting (slowapi) for login and Pi sync.
- Real Database readiness check in /api/health.
- Wing contract strictly enforced: A, B, G.
- PRODUCTION FIX: Cloud is authority for target_days (updated on ACK, not sync).
"""

import os
import time
import uuid
import hashlib
import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException, Depends, Header, Request
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

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="EMS SaaS API", version="5.2.0-prod")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

DEFAULT_RESET_DAY = 15
WING_ORDER = ["A", "B", "G"]
PI_ONLINE_THRESHOLD_SECONDS = 120
COMMAND_EXPIRY_SECONDS = 300
VALID_ROLES = {"super_admin", "society_admin", "member"}

VALID_COMMANDS = {
    "set_active_wing", "set_days", "set_reset_day", "restart",
    "reboot", "reset_days", "off_wing", "off_all", "lcd_display",
}

# ================================================================
# DATABASE & SCHEMA INITIALIZATION
# ================================================================

def get_db():
    conn = psycopg.connect(DATABASE_URL, connect_timeout=10, row_factory=dict_row)
    conn.autocommit = False # PRODUCTION HARDENING: Use explicit transactions
    return conn

@app.on_event("startup")
def ensure_db_schema():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS societies (
                    id SERIAL PRIMARY KEY,
                    name TEXT, location TEXT, plan TEXT, status TEXT,
                    tailscale_ip TEXT, pi_port INT, society_code TEXT
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email TEXT UNIQUE, name TEXT, password TEXT, role TEXT, society_id INT
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pi_devices (
                    id UUID PRIMARY KEY,
                    society_id INT,
                    name TEXT,
                    api_key_hash TEXT UNIQUE,
                    firmware_version TEXT,
                    last_seen TIMESTAMPTZ,
                    status TEXT DEFAULT 'active'
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS wing_configs (
                    society_id INT,
                    wing_id TEXT,
                    name TEXT,
                    display_name TEXT,
                    target_days INT,
                    disabled BOOL DEFAULT FALSE,
                    PRIMARY KEY (society_id, wing_id)
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS wing_state (
                    device_id UUID,
                    wing_id TEXT,
                    physical_toggle TEXT DEFAULT 'UNKNOWN',
                    used_days INT DEFAULT 0,
                    clicks INT DEFAULT 0,
                    PRIMARY KEY (device_id, wing_id)
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pi_state (
                    device_id UUID PRIMARY KEY,
                    active_wing TEXT, reset_day INT, emergency_stop BOOL,
                    uptime_seconds INT, cpu_temp FLOAT, disk_free_mb FLOAT,
                    last_sync TIMESTAMPTZ, boot_count INT, last_shutdown_reason TEXT, clock_source TEXT,
                    watchdog_enabled BOOL, last_reboot_reason TEXT
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pi_events (
                    id SERIAL PRIMARY KEY,
                    device_id UUID, event_id TEXT UNIQUE, timestamp TIMESTAMPTZ, type TEXT, message TEXT
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pi_commands (
                    id UUID PRIMARY KEY,
                    device_id UUID, command TEXT, wing TEXT, params JSONB,
                    status TEXT DEFAULT 'queued',
                    created_at TIMESTAMPTZ, delivered_at TIMESTAMPTZ, acked_at TIMESTAMPTZ, expires_at TIMESTAMPTZ,
                    error TEXT, result TEXT
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS firmware_versions (
                    version TEXT PRIMARY KEY,
                    code TEXT, changelog TEXT, forced BOOL, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id SERIAL PRIMARY KEY,
                    society_id INT, user_id INT, device_id UUID,
                    action TEXT, details JSONB, created_at TIMESTAMPTZ
                );
            """)
            
            # PRODUCTION HARDENING: Add Foreign Keys & Cascades safely
            cur.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_users_society') THEN
                        ALTER TABLE users ADD CONSTRAINT fk_users_society FOREIGN KEY (society_id) REFERENCES societies(id) ON DELETE SET NULL;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_devices_society') THEN
                        ALTER TABLE pi_devices ADD CONSTRAINT fk_devices_society FOREIGN KEY (society_id) REFERENCES societies(id) ON DELETE CASCADE;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_wing_configs_society') THEN
                        ALTER TABLE wing_configs ADD CONSTRAINT fk_wing_configs_society FOREIGN KEY (society_id) REFERENCES societies(id) ON DELETE CASCADE;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_wing_state_device') THEN
                        ALTER TABLE wing_state ADD CONSTRAINT fk_wing_state_device FOREIGN KEY (device_id) REFERENCES pi_devices(id) ON DELETE CASCADE;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_pi_state_device') THEN
                        ALTER TABLE pi_state ADD CONSTRAINT fk_pi_state_device FOREIGN KEY (device_id) REFERENCES pi_devices(id) ON DELETE CASCADE;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_pi_events_device') THEN
                        ALTER TABLE pi_events ADD CONSTRAINT fk_pi_events_device FOREIGN KEY (device_id) REFERENCES pi_devices(id) ON DELETE CASCADE;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_pi_commands_device') THEN
                        ALTER TABLE pi_commands ADD CONSTRAINT fk_pi_commands_device FOREIGN KEY (device_id) REFERENCES pi_devices(id) ON DELETE CASCADE;
                    END IF;
                END $$;
            """)

        conn.commit() # Commit schema changes
        print("DB schema verified OK (Relational v5.2.0 Hardened)")
    except Exception as e:
        conn.rollback()
        print(f"DB SCHEMA CHECK ERROR: {e}")
        raise RuntimeError(f"Database schema initialization failed: {e}")
    finally:
        conn.close()

def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()

def log_audit(cur, user: dict, society_id: int, action: str, details: dict):
    # PRODUCTION HARDENING: No more silent except: pass. Pass cursor in to make it transactional.
    cur.execute("""INSERT INTO audit_log (society_id, user_id, action, details, created_at) 
                   VALUES (%s, %s, %s, %s, %s)""",
                (society_id, user.get("id"), action, psycopg.types.json.Json(details), datetime.now(timezone.utc)))

# ================================================================
# EXCEPTION HANDLERS & CORS
# ================================================================

def _cors_headers(origin: str) -> dict:
    valid = origin if origin in ALLOWED_ORIGINS else ALLOWED_ORIGINS[0]
    return {
        "Access-Control-Allow-Origin": valid,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, PATCH, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With, X-Api-Key",
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
    allow_headers=["Content-Type", "Authorization", "X-Requested-With", "X-Api-Key"],
)

# ================================================================
# UTILITIES
# ================================================================

def create_token(data: dict) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + timedelta(days=30)
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def is_pi_online(pi_state: dict) -> bool:
    if not pi_state: return False
    last_sync = pi_state.get("last_sync")
    if not last_sync: return False
    if isinstance(last_sync, str):
        try:
            if last_sync.endswith("Z"): last_sync = last_sync[:-1] + "+00:00"
            last_sync = datetime.fromisoformat(last_sync)
        except: return False
    if last_sync.tzinfo is None: last_sync = last_sync.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last_sync).total_seconds() <= PI_ONLINE_THRESHOLD_SECONDS

def wing_is_visible(config: dict, state: dict) -> bool:
    physical = str(state.get("physical_toggle", "UNKNOWN")).upper()
    return (
        int(config.get("target_days", 0)) > 0
        and physical == "ON"
        and not bool(config.get("disabled", False))
    )

def validate_command(command: str, params: dict, wing: str = "") -> None:
    if command not in VALID_COMMANDS:
        raise HTTPException(400, f"Unsupported command: {command}")
    if command in ("set_active_wing", "set_days", "off_wing"):
        if wing not in WING_ORDER:
            raise HTTPException(400, "Valid wing A, B or G is required")
    if command == "set_days":
        try: days = int(params.get("days"))
        except: raise HTTPException(400, "days must be an integer")
        if not 1 <= days <= 31: raise HTTPException(400, "days must be 1-31")
    if command == "set_reset_day":
        try: day = int(params.get("day"))
        except: raise HTTPException(400, "day must be an integer")
        if not 1 <= day <= 28: raise HTTPException(400, "reset day must be 1-28")
    if command == "lcd_display":
        line1 = str(params.get("line1", ""))[:16]
        line2 = str(params.get("line2", ""))[:16]
        if not line1 and not line2: raise HTTPException(400, "LCD message cannot be empty")
        params["line1"] = line1
        params["line2"] = line2
        try: dur = float(params.get("duration", 10))
        except: raise HTTPException(400, "duration must be a number")
        if not 1 <= dur <= 120: raise HTTPException(400, "duration must be 1-120 seconds")
        params["duration"] = dur

# ================================================================
# AUTH
# ================================================================

class UserLogin(BaseModel):
    email: str
    password: str

async def get_current_user(authorization: str = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Valid token required")
    try:
        return jwt.decode(authorization[7:], SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

def require_role(*roles):
    async def checker(user: dict = Depends(get_current_user)):
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return checker

async def require_society_access(request: Request, user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") not in ("super_admin", "society_admin"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    if user["role"] != "super_admin":
        requested = str(request.query_params.get("society_id", ""))
        owned = str(user.get("society_id", ""))
        if requested and requested != owned:
            raise HTTPException(status_code=403, detail="Cannot access other society data")
    return user

def authenticate_pi(payload: dict) -> tuple:
    try: device_id = str(payload.get("deviceId"))
    except: raise HTTPException(400, "Invalid deviceId")
    
    supplied_key = str(payload.get("key", ""))
    if not supplied_key: raise HTTPException(401, "Pi API key required")
    
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id, society_id FROM pi_devices WHERE id = %s AND api_key_hash = %s", (device_id, hash_api_key(supplied_key)))
            dev = cur.fetchone()
            if not dev: raise HTTPException(403, "Invalid Pi API key or Device ID.")
            return dev["id"], dev["society_id"]
    finally:
        conn.close()

# ================================================================
# HEALTH & KEEPALIVE
# ================================================================

_keepalive_ts = time.time()

@app.get("/keepalive")
def keepalive():
    global _keepalive_ts
    _keepalive_ts = time.time()
    return PlainTextResponse("alive")

@app.head("/ping")
@app.get("/ping")
def ping():
    global _keepalive_ts
    _keepalive_ts = time.time()
    return PlainTextResponse("pong")

@app.get("/api/health")
def health():
    # PRODUCTION HARDENING: Real readiness check
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.commit()
        conn.close()
        return {"status": "ok", "database": "ok", "uptime_keepalive": round(time.time() - _keepalive_ts)}
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
                
            cur.execute("INSERT INTO societies (name, location, plan, status, society_code) VALUES (%s, %s, %s, %s, %s) RETURNING id", 
                        ("Prestine Pacific", "Mumbai", "Basic", "active", "prestine"))
            sid = cur.fetchone()["id"]
            
            device_id = str(uuid.uuid4())
            raw_api_key = str(uuid.uuid4())
            cur.execute("INSERT INTO pi_devices (id, society_id, name, api_key_hash, status) VALUES (%s, %s, %s, %s, %s)",
                        (device_id, sid, "Main Controller", hash_api_key(raw_api_key), "active"))
                        
            cur.execute("INSERT INTO users (email, name, password, role, society_id) VALUES (%s, %s, %s, %s, %s)",
                        ("admin@ems.com", "Super Admin", bcrypt.hashpw(bootstrap_pass.encode(), bcrypt.gensalt()).decode(), "super_admin", None))
            cur.execute("INSERT INTO users (email, name, password, role, society_id) VALUES (%s, %s, %s, %s, %s)",
                        ("admin@prestine.com", "Prestine Admin", bcrypt.hashpw(bootstrap_pass.encode(), bcrypt.gensalt()).decode(), "society_admin", sid))
            cur.execute("INSERT INTO users (email, name, password, role, society_id) VALUES (%s, %s, %s, %s, %s)",
                        ("member@prestine.com", "Prestine Member", bcrypt.hashpw(bootstrap_pass.encode(), bcrypt.gensalt()).decode(), "member", sid))
            
            for wid in WING_ORDER:
                cur.execute("INSERT INTO wing_configs (society_id, wing_id, name, display_name, target_days, disabled) VALUES (%s, %s, %s, %s, %s, %s)",
                           (sid, wid, f"Tower {wid}", f"Tower {wid}", 10 if wid == "A" else 12 if wid == "B" else 10, False))
                cur.execute("INSERT INTO wing_state (device_id, wing_id) VALUES (%s, %s)", (device_id, wid))
                
            cur.execute("""INSERT INTO pi_state (device_id, active_wing, reset_day, emergency_stop, uptime_seconds, cpu_temp, disk_free_mb, last_sync, boot_count, watchdog_enabled) 
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (device_id, "A", DEFAULT_RESET_DAY, False, 0, 0.0, 0.0, datetime.now(timezone.utc), 0, True))
                        
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
        
    return {"message": "Initialized. Change default passwords.", "device_id": device_id, "api_key": raw_api_key}

# ================================================================
# AUTH LOGIN
# ================================================================

@app.post("/api/auth/login")
@limiter.limit("5/minute")
def login(request: Request, user: UserLogin):
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM users WHERE email = %s", (user.email,))
            db_user = cur.fetchone()
            if not db_user or not bcrypt.checkpw(user.password.encode(), db_user["password"].encode()):
                raise HTTPException(status_code=401, detail="Invalid credentials")
                
            token = create_token({
                "id": db_user["id"], "role": db_user["role"],
                "society_id": db_user["society_id"]
            })
            return {
                "token": token, "role": db_user["role"],
                "name": db_user["name"], "society_id": db_user["society_id"],
            }
    finally:
        conn.close()

# ================================================================
# SUPER-ADMIN — SOCIETIES
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
                cur.execute("SELECT * FROM pi_devices WHERE society_id = %s", (sid,))
                dev = cur.fetchone()
                
                cur.execute("SELECT * FROM pi_state WHERE device_id = %s", (dev["id"],))
                pi = cur.fetchone()
                online = is_pi_online(pi)
                
                wings_data = {}
                cur.execute("SELECT * FROM wing_configs WHERE society_id = %s", (sid,))
                configs = cur.fetchall()
                for c in configs:
                    cur.execute("SELECT * FROM wing_state WHERE device_id = %s AND wing_id = %s", (dev["id"], c["wing_id"]))
                    st = cur.fetchone()
                    wings_data[c["wing_id"]] = {
                        "name": c["name"], "display_name": c["display_name"],
                        "target_days": c["target_days"], "disabled": c["disabled"],
                        "used_days": st["used_days"] if st else 0,
                        "clicks": st["clicks"] if st else 0,
                        "physical_toggle": st["physical_toggle"] if st else "UNKNOWN",
                        "visible": wing_is_visible(c, st or {})
                    }
                    
                result.append({
                    "id": s["id"], "name": s["name"], "location": s["location"],
                    "plan": s["plan"], "status": s.get("status", "active"),
                    "society_code": s.get("society_code", ""),
                    "pi_online": online, "last_sync": pi.get("last_sync") if pi else None,
                    "active_wing": pi.get("active_wing") if pi else None,
                    "firmware_version": dev.get("firmware_version", "?") if dev else None,
                    "reset_day": pi.get("reset_day", DEFAULT_RESET_DAY) if pi else DEFAULT_RESET_DAY,
                    "wings": wings_data,
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
            society = {
                "name": data.get("name", ""), "location": data.get("location", ""),
                "plan": data.get("plan", "Basic"), "status": "active",
                "tailscale_ip": data.get("tailscale_ip", ""),
                "pi_port": int(data.get("pi_port", 5000)),
                "society_code": data.get("society_code", ""),
            }
            
            if sid:
                cur.execute("""UPDATE societies SET name=%s, location=%s, plan=%s, status=%s, 
                               tailscale_ip=%s, pi_port=%s, society_code=%s WHERE id=%s""",
                            (society["name"], society["location"], society["plan"], society["status"],
                             society["tailscale_ip"], society["pi_port"], society["society_code"], sid))
            else:
                cur.execute("""INSERT INTO societies (name, location, plan, status, tailscale_ip, pi_port, society_code) 
                               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                            (society["name"], society["location"], society["plan"], society["status"],
                             society["tailscale_ip"], society["pi_port"], society["society_code"]))
                new_sid = cur.fetchone()[0]
                log_audit(cur, user, new_sid, "CREATE_SOCIETY", society)
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
            cur.execute("DELETE FROM societies WHERE id = %s", (sid,))
            log_audit(cur, user, sid, "DELETE_SOCIETY", {})
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
    return {"message": "Deleted"}

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
def download_firmware(version: str, x_api_key: str = Header(None, alias="X-Api-Key")):
    if not x_api_key:
        raise HTTPException(403, "API key required in X-Api-Key header")
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT code FROM firmware_versions WHERE version = %s", (version,))
            fv = cur.fetchone()
            if not fv:
                raise HTTPException(404, "Version not found")
            return PlainTextResponse(fv["code"], media_type="text/plain")
    finally:
        conn.close()

# ================================================================
# PI SYNC & COMMAND ACK
# ================================================================

@app.post("/api/pi/sync")
@limiter.limit("30/minute")
def pi_sync(request: Request, payload: dict):
    device_id, society_id = authenticate_pi(payload)
    now = datetime.now(timezone.utc)
    
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("UPDATE pi_devices SET last_seen = %s, firmware_version = %s WHERE id = %s",
                        (now, payload.get("firmwareVersion", "unknown"), device_id))
                        
            for wid, w in payload.get("wings", {}).items():
                if wid not in WING_ORDER: continue
                physical_toggle = w.get("physicalToggle", "UNKNOWN")
                if isinstance(physical_toggle, bool): physical_toggle = "ON" if physical_toggle else "OFF"
                physical_toggle = str(physical_toggle).upper()
                if physical_toggle not in ("ON", "OFF", "UNKNOWN"): physical_toggle = "UNKNOWN"
                
                # PRODUCTION FIX: Pi only reports runtime state, NOT target_days config
                cur.execute("""INSERT INTO wing_state (device_id, wing_id, physical_toggle, used_days, clicks) 
                               VALUES (%s, %s, %s, %s, %s) 
                               ON CONFLICT (device_id, wing_id) DO UPDATE SET 
                               physical_toggle=EXCLUDED.physical_toggle, used_days=EXCLUDED.used_days, clicks=EXCLUDED.clicks""",
                            (device_id, wid, physical_toggle, int(w.get("usedDays", 0)), int(w.get("clicks", 0))))

            cur.execute("""INSERT INTO pi_state (device_id, active_wing, reset_day, emergency_stop, uptime_seconds, cpu_temp, disk_free_mb, last_sync, boot_count, last_shutdown_reason, clock_source, watchdog_enabled, last_reboot_reason) 
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (device_id) DO UPDATE SET 
                           active_wing=EXCLUDED.active_wing, reset_day=EXCLUDED.reset_day, emergency_stop=EXCLUDED.emergency_stop, 
                           uptime_seconds=EXCLUDED.uptime_seconds, cpu_temp=EXCLUDED.cpu_temp, disk_free_mb=EXCLUDED.disk_free_mb, 
                           last_sync=EXCLUDED.last_sync, boot_count=EXCLUDED.boot_count, last_shutdown_reason=EXCLUDED.last_shutdown_reason, 
                           clock_source=EXCLUDED.clock_source, watchdog_enabled=EXCLUDED.watchdog_enabled, last_reboot_reason=EXCLUDED.last_reboot_reason""",
                        (device_id, payload.get("activeWing"), int(payload.get("resetDay", DEFAULT_RESET_DAY)),
                         bool(payload.get("emergencyStop", False)), int(payload.get("uptimeSeconds", 0)),
                         float(payload.get("cpuTemp", 0)), float(payload.get("diskFreeMB", 0)), now, int(payload.get("bootCount", 0)),
                         payload.get("lastShutdownReason", ""), payload.get("clockSource", ""), bool(payload.get("watchdogEnabled", False)),
                         payload.get("lastRebootReason", "")))

            for event in payload.get("events", []):
                ev_id = event.get("eventId")
                if not ev_id: continue
                cur.execute("""INSERT INTO pi_events (device_id, event_id, timestamp, type, message) 
                               VALUES (%s, %s, %s, %s, %s) 
                               ON CONFLICT (event_id) DO NOTHING""",
                            (device_id, ev_id, event.get("timestamp", now), event.get("type", "system"), event.get("message", "")))

            cur.execute("UPDATE pi_commands SET status = 'expired' WHERE device_id = %s AND status = 'delivered' AND delivered_at < %s",
                        (device_id, now - timedelta(seconds=COMMAND_EXPIRY_SECONDS)))

            cur.execute("SELECT * FROM pi_commands WHERE device_id = %s AND status = 'queued' ORDER BY created_at ASC LIMIT 1", (device_id,))
            cmd = cur.fetchone()
            
            reply = {"success": True, "command": None, "command_id": None}
            if cmd:
                cur.execute("UPDATE pi_commands SET status = 'delivered', delivered_at = %s WHERE id = %s", (now, cmd["id"]))
                reply["command"] = cmd["command"]
                reply["command_id"] = str(cmd["id"])
                if cmd.get("wing"): reply["wing"] = cmd["wing"]
                reply["params"] = cmd.get("params", {})

        conn.commit() # Commit all sync changes atomically
    except Exception as e:
        conn.rollback() # Rollback if any query fails
        raise e
    finally:
        conn.close()
    return reply

@app.post("/api/pi/command-ack")
def pi_command_ack(payload: dict):
    device_id, society_id = authenticate_pi(payload)
    command_id = payload.get("command_id")
    if not command_id:
        raise HTTPException(400, "command_id required")

    success = bool(payload.get("success", True))
    error = payload.get("error")
    result = payload.get("result")

    conn = get_db()
    try:
        with conn.cursor() as cur:
            # Fetch the command to see what it was
            cur.execute("SELECT command, wing, params FROM pi_commands WHERE id = %s AND device_id = %s", (command_id, device_id))
            cmd = cur.fetchone()
            if not cmd:
                return {"success": True, "status": "unknown"}

            # PRODUCTION FIX: If ACK is successful, apply configuration changes to DB
            if success:
                if cmd["command"] == "set_days":
                    wing = cmd["wing"]
                    days = int(cmd["params"].get("days", 0))
                    cur.execute("UPDATE wing_configs SET target_days = %s WHERE society_id = %s AND wing_id = %s", (days, society_id, wing))
                elif cmd["command"] == "set_reset_day":
                    day = int(cmd["params"].get("day", 15))
                    cur.execute("UPDATE pi_state SET reset_day = %s WHERE device_id = %s", (day, device_id))

            # Update command status
            cur.execute("""UPDATE pi_commands SET status = %s, acked_at = %s, error = %s, result = %s 
                           WHERE id = %s AND device_id = %s AND status = 'delivered'""",
                        ("succeeded" if success else "failed", datetime.now(timezone.utc), None if success else error, result, command_id, device_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
    return {"success": True}

# ================================================================
# ADMIN & MEMBER ENDPOINTS
# ================================================================

def get_device_for_society(cur, sid: int) -> str:
    cur.execute("SELECT id FROM pi_devices WHERE society_id = %s ORDER BY last_seen DESC LIMIT 1", (sid,))
    dev = cur.fetchone()
    if not dev: raise HTTPException(404, "No device configured for this society")
    return dev["id"]

@app.post("/api/admin/pi-command")
@limiter.limit("30/minute")
def queue_command(request: Request, data: dict, user: dict = Depends(get_current_user)):
    try: sid = int(data.get("society_id"))
    except: raise HTTPException(400, "Invalid society_id")

    if user.get("role") != "super_admin" and str(user.get("society_id")) != str(sid):
        raise HTTPException(403, "Cannot access other society data")

    command = str(data.get("command", ""))
    wing = str(data.get("wing", ""))
    params = dict(data.get("params", {}))

    validate_command(command, params, wing)

    conn = get_db()
    try:
        with conn.cursor() as cur:
            dev_id = get_device_for_society(cur, sid)
            command_id = str(uuid.uuid4())
            cur.execute("""INSERT INTO pi_commands (id, device_id, command, wing, params, status, created_at, expires_at) 
                           VALUES (%s, %s, %s, %s, %s, 'queued', %s, %s)""",
                        (command_id, dev_id, command, wing, psycopg.types.json.Json(params), datetime.now(timezone.utc), datetime.now(timezone.utc) + timedelta(hours=24)))
            
            log_audit(cur, user, sid, "QUEUE_COMMAND", {"command": command, "wing": wing, "id": command_id})
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
            dev_id = get_device_for_society(cur, sid)
            
            cur.execute("SELECT * FROM pi_state WHERE device_id = %s", (dev_id,))
            pi = cur.fetchone()
            if not pi: return {"connected": False}

            cur.execute("SELECT * FROM wing_configs WHERE society_id = %s", (sid,))
            configs = cur.fetchall()
            
            wings_data = {}
            for c in configs:
                cur.execute("SELECT * FROM wing_state WHERE device_id = %s AND wing_id = %s", (dev_id, c["wing_id"]))
                st = cur.fetchone() or {}
                wings_data[c["wing_id"]] = {
                    "used_days": st.get("used_days", 0),
                    "target_days": c["target_days"],
                    "status": "ACTIVE" if pi.get("active_wing") == c["wing_id"] else "IDLE",
                    "name": c["name"],
                    "display_name": c["display_name"],
                    "disabled": c["disabled"],
                    "physical_toggle": st.get("physical_toggle", "UNKNOWN"),
                    "clicks": st.get("clicks", 0),
                    "visible": wing_is_visible(c, st),
                }

            cur.execute("SELECT * FROM pi_commands WHERE device_id = %s AND status IN ('queued','delivered') ORDER BY created_at ASC LIMIT 1", (dev_id,))
            pc = cur.fetchone()
            
            return {
                "connected": is_pi_online(pi),
                "active_wing": pi.get("active_wing"),
                "reset_day": pi.get("reset_day", DEFAULT_RESET_DAY),
                "wings": wings_data,
                "emergency_stop": pi.get("emergency_stop", False),
                "watchdog_enabled": pi.get("watchdog_enabled", False),
                "last_reboot_reason": pi.get("last_reboot_reason", ""),
                "firmware_version": pi.get("firmware_version", "?"),
                "cpu_temp": pi.get("cpu_temp", 0),
                "uptime_seconds": pi.get("uptime_seconds", 0),
                "boot_count": pi.get("boot_count", 0),
                "disk_free_mb": pi.get("disk_free_mb", 0),
                "clock_source": pi.get("clock_source", "NTP"),
                "last_sync": pi.get("last_sync").isoformat() if pi.get("last_sync") else None,
                "pending_command": {
                    "id": str(pc["id"]), "command": pc["command"],
                    "status": pc["status"], "queued_at": pc["created_at"].isoformat() if pc["created_at"] else None,
                    "sent_at": pc["delivered_at"].isoformat() if pc["delivered_at"] else None, "acked_at": pc["acked_at"].isoformat() if pc["acked_at"] else None,
                    "error": pc["error"],
                } if pc else None,
            }
    finally:
        conn.close()

@app.get("/api/admin/pi-state")
def get_pi_state(society_id: str, user: dict = Depends(require_society_access)):
    return admin_dashboard(society_id, user)

def map_events(raw):
    out = []
    for e in raw:
        out.append({
            "id": e["id"], # Return integer ID for cursor
            "ts": e["timestamp"].isoformat() if e.get("timestamp") else "", 
            "level": (e.get("type","") or "").upper(), 
            "msg": e.get("message","")
        })
    return out

@app.get("/api/admin/pi-events")
def get_pi_events(society_id: str, last_id: int = 0, user: dict = Depends(require_society_access)):
    sid = int(society_id)
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            dev_id = get_device_for_society(cur, sid)
            cur.execute("SELECT * FROM pi_events WHERE device_id = %s AND id > %s ORDER BY id ASC LIMIT 100", (dev_id, last_id))
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
            dev_id = get_device_for_society(cur, sid)
            cur.execute("SELECT * FROM pi_state WHERE device_id = %s", (dev_id,))
            pi = cur.fetchone()
            if not pi: return {"connected": False}

            cur.execute("SELECT * FROM wing_configs WHERE society_id = %s", (sid,))
            configs = cur.fetchall()
            wings_data = {}
            for c in configs:
                cur.execute("SELECT * FROM wing_state WHERE device_id = %s AND wing_id = %s", (dev_id, c["wing_id"]))
                st = cur.fetchone() or {}
                if not wing_is_visible(c, st): continue
                wings_data[c["wing_id"]] = {
                    "used_days": st.get("used_days", 0),
                    "target_days": c["target_days"],
                    "name": c["name"],
                    "display_name": c["display_name"],
                    "clicks": st.get("clicks", 0),
                    "physical_toggle": st.get("physical_toggle", "UNKNOWN"),
                }

            return {
                "connected": is_pi_online(pi),
                "active_wing": pi.get("active_wing"),
                "wings": wings_data, "reset_day": pi.get("reset_day", DEFAULT_RESET_DAY),
                "firmware_version": pi.get("firmware_version", "?"),
                "cpu_temp": pi.get("cpu_temp", 0),
                "uptime_seconds": pi.get("uptime_seconds", 0),
                "last_sync": pi.get("last_sync").isoformat() if pi.get("last_sync") else None,
            }
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
            dev_id = get_device_for_society(cur, sid)
            cur.execute("SELECT * FROM pi_events WHERE device_id = %s AND id > %s ORDER BY id ASC LIMIT 100", (dev_id, last_id))
            events = cur.fetchall()
            mapped = map_events(events)
            return {"events": mapped, "last_id": mapped[-1]["id"] if mapped else last_id}
    finally:
        conn.close()