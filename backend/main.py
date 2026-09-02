"""
EMS SaaS Backend v4.0.0 — Industrial Production (Relational DB)
====================================
- Migrated from JSONB to proper PostgreSQL relational tables.
- Eliminated global Python locks; uses DB-level atomic operations.
- API keys stored as SHA-256 hashes.
- Native Postgres UNIQUE constraint for event idempotency.
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

app = FastAPI(title="EMS SaaS API", version="4.0.0-prod")

DEFAULT_RESET_DAY = 15
WING_ORDER = ["A", "B", "G"]
PI_ONLINE_THRESHOLD_SECONDS = 120
VALID_ROLES = {"super_admin", "society_admin", "member"}

VALID_COMMANDS = {
    "set_active_wing", "set_days", "set_reset_day", "restart",
    "reboot", "reset_days", "off_wing", "off_all", "lcd_display",
}

# ================================================================
# DATABASE & SCHEMA INITIALIZATION
# ================================================================

def get_db():
    conn = psycopg.connect(DATABASE_URL, connect_timeout=10)
    conn.autocommit = True
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
                    tailscale_ip TEXT, pi_port INT, api_key TEXT, society_code TEXT
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email TEXT UNIQUE, name TEXT, password TEXT, role TEXT, society_id INT
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pi_state (
                    society_id INT PRIMARY KEY,
                    active_wing TEXT, wings JSONB, reset_day INT, emergency_stop BOOL,
                    firmware_version TEXT, uptime_seconds INT, cpu_temp FLOAT, disk_free_mb FLOAT,
                    last_sync TIMESTAMPTZ, boot_count INT, last_shutdown_reason TEXT, clock_source TEXT,
                    watchdog_enabled BOOL, last_reboot_reason TEXT
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pi_events (
                    id SERIAL PRIMARY KEY,
                    society_id INT, event_id TEXT UNIQUE, timestamp TIMESTAMPTZ, type TEXT, message TEXT
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pi_commands (
                    id UUID PRIMARY KEY,
                    society_id INT, command TEXT, wing TEXT, params JSONB,
                    status TEXT, queued_at TIMESTAMPTZ, sent_at TIMESTAMPTZ, acked_at TIMESTAMPTZ, error TEXT, result TEXT
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS firmware_versions (
                    version TEXT PRIMARY KEY,
                    code TEXT, changelog TEXT, forced BOOL, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
                );
            """)
        print("DB schema verified OK (Relational v4.0)")
    except Exception as e:
        print(f"DB SCHEMA CHECK ERROR: {e}")
    finally:
        conn.close()

def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()

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

def wing_is_visible(w: dict) -> bool:
    physical = str(w.get("physical_toggle", "UNKNOWN")).upper()
    return (
        int(w.get("target_days", 0)) > 0
        and physical == "ON"
        and not bool(w.get("disabled", False))
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

def authenticate_pi(payload: dict) -> int:
    try: sid = int(payload.get("societyId"))
    except: raise HTTPException(400, "Invalid societyId")
    
    supplied_key = str(payload.get("key", ""))
    if not supplied_key: raise HTTPException(401, "Pi API key required")
    
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id FROM societies WHERE id = %s AND api_key = %s", (sid, hash_api_key(supplied_key)))
            soc = cur.fetchone()
            if not soc: raise HTTPException(403, "Invalid Pi API key or Society ID.")
            return sid
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
    return {"status": "ok", "uptime_keepalive": round(time.time() - _keepalive_ts)}

# ================================================================
# BOOTSTRAP
# ================================================================

@app.post("/api/bootstrap")
def bootstrap():
    bootstrap_pass = os.getenv("EMS_BOOTSTRAP_PASSWORD")
    if not bootstrap_pass:
        raise HTTPException(500, "EMS_BOOTSTRAP_PASSWORD env variable is not configured.")
        
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id FROM users LIMIT 1")
            if cur.fetchone():
                raise HTTPException(400, "System already initialized")
                
            cur.execute("INSERT INTO societies (name, location, plan, status, pi_port, society_code) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id", 
                        ("Prestine Pacific", "Mumbai", "Basic", "active", 5000, "prestine"))
            sid = cur.fetchone()["id"]
            
            # Generate a random API key for the society
            raw_api_key = str(uuid.uuid4())
            cur.execute("UPDATE societies SET api_key = %s WHERE id = %s", (hash_api_key(raw_api_key), sid))
            
            cur.execute("INSERT INTO users (email, name, password, role, society_id) VALUES (%s, %s, %s, %s, %s)",
                        ("admin@ems.com", "Super Admin", bcrypt.hashpw(bootstrap_pass.encode(), bcrypt.gensalt()).decode(), "super_admin", None))
            cur.execute("INSERT INTO users (email, name, password, role, society_id) VALUES (%s, %s, %s, %s, %s)",
                        ("admin@prestine.com", "Prestine Admin", bcrypt.hashpw(bootstrap_pass.encode(), bcrypt.gensalt()).decode(), "society_admin", sid))
            cur.execute("INSERT INTO users (email, name, password, role, society_id) VALUES (%s, %s, %s, %s, %s)",
                        ("member@prestine.com", "Prestine Member", bcrypt.hashpw(bootstrap_pass.encode(), bcrypt.gensalt()).decode(), "member", sid))
                        
            wings = {
                "A": {"name": "Tower A", "display_name": "Tower A", "used_days": 0, "target_days": 9, "clicks": 0, "disabled": False, "physical_toggle": "UNKNOWN"},
                "B": {"name": "Tower B", "display_name": "Tower B", "used_days": 0, "target_days": 12, "clicks": 0, "disabled": False, "physical_toggle": "UNKNOWN"},
                "G": {"name": "Tower G", "display_name": "Tower G", "used_days": 0, "target_days": 10, "clicks": 0, "disabled": False, "physical_toggle": "UNKNOWN"}
            }
            cur.execute("""INSERT INTO pi_state (society_id, active_wing, wings, reset_day, emergency_stop, firmware_version, 
                           uptime_seconds, cpu_temp, disk_free_mb, last_sync, boot_count, watchdog_enabled) 
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (sid, "A", psycopg.types.json.Json(wings), DEFAULT_RESET_DAY, False, "0.0.0", 0, 0.0, 0.0, datetime.now(timezone.utc), 0, True))
                        
    finally:
        conn.close()
        
    return {"message": "Initialized. Change default passwords.", "society_api_key": raw_api_key}

# ================================================================
# AUTH LOGIN
# ================================================================

@app.post("/api/auth/login")
def login(user: UserLogin):
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
                cur.execute("SELECT * FROM pi_state WHERE society_id = %s", (sid,))
                pi = cur.fetchone()
                
                online = is_pi_online(pi)
                wings = pi.get("wings", {}) if pi else {}
                wing_data = {}
                for wid, w in wings.items():
                    wing_data[wid] = {
                        "name": w.get("name", wid),
                        "display_name": w.get("display_name", ""),
                        "used_days": w.get("used_days", 0),
                        "target_days": w.get("target_days", 0),
                        "clicks": w.get("clicks", 0),
                        "disabled": w.get("disabled", False),
                        "physical_toggle": w.get("physical_toggle", "UNKNOWN"),
                        "visible": wing_is_visible(w),
                    }
                    
                result.append({
                    "id": s["id"], "name": s["name"], "location": s["location"],
                    "plan": s["plan"], "status": s.get("status", "active"),
                    "tailscale_ip": s.get("tailscale_ip", ""), "pi_port": s.get("pi_port", 5000),
                    "api_key": "configured" if s.get("api_key") else "",
                    "society_code": s.get("society_code", ""),
                    "pi_online": online, "last_sync": pi.get("last_sync") if pi else None,
                    "active_wing": pi.get("active_wing") if pi else None,
                    "emergency_stop": pi.get("emergency_stop", False) if pi else False,
                    "firmware_version": pi.get("firmware_version", "?") if pi else None,
                    "reset_day": pi.get("reset_day", DEFAULT_RESET_DAY) if pi else DEFAULT_RESET_DAY,
                    "wings": wing_data,
                    "watchdog_enabled": pi.get("watchdog_enabled", False) if pi else False,
                    "last_reboot_reason": pi.get("last_reboot_reason", "") if pi else "",
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
                               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                            (society["name"], society["location"], society["plan"], society["status"],
                             society["tailscale_ip"], society["pi_port"], society["society_code"]))
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
            cur.execute("DELETE FROM pi_state WHERE society_id = %s", (sid,))
            cur.execute("DELETE FROM pi_events WHERE society_id = %s", (sid,))
            cur.execute("DELETE FROM pi_commands WHERE society_id = %s", (sid,))
            cur.execute("DELETE FROM users WHERE society_id = %s", (sid,))
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
    finally:
        conn.close()
    return {"message": "Saved"}

@app.post("/api/super-admin/users/delete")
def delete_user(data: dict, user: dict = Depends(require_role("super_admin"))):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (data.get("id"),))
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
    finally:
        conn.close()
    return {"message": "Saved"}

@app.post("/api/super-admin/firmware/delete")
def delete_firmware_version(data: dict, user: dict = Depends(require_role("super_admin"))):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM firmware_versions WHERE version = %s", (data.get("version"),))
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
def pi_sync(payload: dict):
    sid = authenticate_pi(payload)
    now = datetime.now(timezone.utc).isoformat()
    
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            # 1. Fetch Existing DB State
            cur.execute("SELECT * FROM pi_state WHERE society_id = %s FOR UPDATE", (sid,))
            db_state = cur.fetchone()
            
            wings = {}
            for wid in WING_ORDER:
                w = payload.get("wings", {}).get(wid, {})
                
                physical_toggle = w.get("physicalToggle", "UNKNOWN")
                if isinstance(physical_toggle, bool):
                    physical_toggle = "ON" if physical_toggle else "OFF"
                physical_toggle = str(physical_toggle).upper()
                if physical_toggle not in ("ON", "OFF", "UNKNOWN"):
                    physical_toggle = "UNKNOWN"

                existing_wing = db_state.get("wings", {}).get(wid, {}) if db_state else {}
                
                wings[wid] = {
                    "name": existing_wing.get("name", wid),
                    "display_name": existing_wing.get("display_name", f"Wing {wid}"),
                    "target_days": existing_wing.get("target_days", 0),
                    "disabled": existing_wing.get("disabled", False),
                    "used_days": int(w.get("usedDays", existing_wing.get("used_days", 0))),
                    "clicks": int(w.get("clicks", existing_wing.get("clicks", 0))),
                    "physical_toggle": physical_toggle,
                }

            # 2. Upsert Pi State
            cur.execute("""INSERT INTO pi_state (society_id, active_wing, wings, reset_day, emergency_stop, firmware_version, 
                           uptime_seconds, cpu_temp, disk_free_mb, last_sync, boot_count, last_shutdown_reason, clock_source, 
                           watchdog_enabled, last_reboot_reason) 
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (society_id) DO UPDATE SET 
                           active_wing=EXCLUDED.active_wing, wings=EXCLUDED.wings, reset_day=EXCLUDED.reset_day, 
                           emergency_stop=EXCLUDED.emergency_stop, firmware_version=EXCLUDED.firmware_version, 
                           uptime_seconds=EXCLUDED.uptime_seconds, cpu_temp=EXCLUDED.cpu_temp, disk_free_mb=EXCLUDED.disk_free_mb, 
                           last_sync=EXCLUDED.last_sync, boot_count=EXCLUDED.boot_count, last_shutdown_reason=EXCLUDED.last_shutdown_reason, 
                           clock_source=EXCLUDED.clock_source, watchdog_enabled=EXCLUDED.watchdog_enabled, 
                           last_reboot_reason=EXCLUDED.last_reboot_reason""",
                        (sid, payload.get("activeWing"), psycopg.types.json.Json(wings), int(payload.get("resetDay", DEFAULT_RESET_DAY)),
                         bool(payload.get("emergencyStop", False)), payload.get("firmwareVersion", "?"), int(payload.get("uptimeSeconds", 0)),
                         float(payload.get("cpuTemp", 0)), float(payload.get("diskFreeMB", 0)), now, int(payload.get("bootCount", 0)),
                         payload.get("lastShutdownReason", ""), payload.get("clockSource", ""), bool(payload.get("watchdogEnabled", False)),
                         payload.get("lastRebootReason", "")))

            # 3. Insert Events (DB Unique Constraint handles deduplication)
            for event in payload.get("events", []):
                ev_id = event.get("eventId")
                if not ev_id: continue
                try:
                    cur.execute("INSERT INTO pi_events (society_id, event_id, timestamp, type, message) VALUES (%s, %s, %s, %s, %s)",
                                (sid, ev_id, event.get("timestamp", now), event.get("type", "system"), event.get("message", "")))
                except psycopg.errors.UniqueViolation:
                    pass # Ignore duplicate events

            # 4. Fetch and return next pending command
            cur.execute("SELECT * FROM pi_commands WHERE society_id = %s AND status = 'pending' ORDER BY queued_at ASC LIMIT 1", (sid,))
            cmd = cur.fetchone()
            
            reply = {"success": True, "command": None, "command_id": None}
            if cmd:
                cur.execute("UPDATE pi_commands SET status = 'sent', sent_at = %s WHERE id = %s", (now, cmd["id"]))
                reply["command"] = cmd["command"]
                reply["command_id"] = str(cmd["id"])
                if cmd.get("wing"): reply["wing"] = cmd["wing"]
                reply["params"] = cmd.get("params", {})

    finally:
        conn.close()
    return reply

@app.post("/api/pi/command-ack")
def pi_command_ack(payload: dict):
    sid = authenticate_pi(payload)
    command_id = payload.get("command_id")
    if not command_id:
        raise HTTPException(400, "command_id required")

    success = bool(payload.get("success", True))
    error = payload.get("error")
    result = payload.get("result")

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""UPDATE pi_commands SET status = %s, acked_at = %s, error = %s, result = %s 
                           WHERE id = %s AND society_id = %s""",
                        ("acknowledged" if success else "failed", datetime.now(timezone.utc), None if success else error, result, command_id, sid))
    finally:
        conn.close()
    return {"success": True}

# ================================================================
# ADMIN & MEMBER ENDPOINTS
# ================================================================

@app.post("/api/admin/pi-command")
def queue_command(data: dict, user: dict = Depends(get_current_user)):
    try: sid = int(data.get("society_id"))
    except: raise HTTPException(400, "Invalid society_id")

    if user.get("role") != "super_admin" and str(user.get("society_id")) != str(sid):
        raise HTTPException(403, "Cannot access other society data")

    command = str(data.get("command", ""))
    wing = str(data.get("wing", ""))
    params = dict(data.get("params", {}))

    validate_command(command, params, wing)

    command_id = str(uuid.uuid4())
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO pi_commands (id, society_id, command, wing, params, status, queued_at) 
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (command_id, sid, command, wing, psycopg.types.json.Json(params), "pending", datetime.now(timezone.utc)))
    finally:
        conn.close()
        
    return {"success": True, "message": "Command queued", "command": command, "command_id": command_id}

@app.get("/api/admin/dashboard")
def admin_dashboard(society_id: str, user: dict = Depends(require_society_access)):
    if not society_id:
        raise HTTPException(400, "society_id required")
    sid = int(society_id)
    
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM pi_state WHERE society_id = %s", (sid,))
            pi = cur.fetchone()
            if not pi:
                return {"connected": False}

            wings_data = {}
            for wid, w in pi.get("wings", {}).items():
                wings_data[wid] = {
                    "used_days": w.get("used_days", 0),
                    "target_days": w.get("target_days", 0),
                    "status": "ACTIVE" if pi.get("active_wing") == wid else "IDLE",
                    "name": w.get("name", wid),
                    "display_name": w.get("display_name", ""),
                    "disabled": w.get("disabled", False),
                    "physical_toggle": w.get("physical_toggle", "UNKNOWN"),
                    "clicks": w.get("clicks", 0),
                    "visible": wing_is_visible(w),
                }

            cur.execute("SELECT * FROM pi_commands WHERE society_id = %s AND status IN ('pending','sent') ORDER BY queued_at ASC LIMIT 1", (sid,))
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
                "last_sync": pi.get("last_sync").isoformat() if pi.get("last_sync") else None,
                "pending_command": {
                    "id": str(pc["id"]), "command": pc["command"],
                    "status": pc["status"], "queued_at": pc["queued_at"].isoformat() if pc["queued_at"] else None,
                    "sent_at": pc["sent_at"].isoformat() if pc["sent_at"] else None, "acked_at": pc["acked_at"].isoformat() if pc["acked_at"] else None,
                    "error": pc["error"],
                } if pc else None,
            }
    finally:
        conn.close()

@app.get("/api/admin/pi-state")
def get_pi_state(society_id: str, user: dict = Depends(require_society_access)):
    # Reuse dashboard logic to fetch state
    state = admin_dashboard(society_id, user)
    if not state.get("connected") and not state.get("wings"):
        return state
    return state

def map_events(raw):
    out = []
    for e in raw:
        out.append({
            "id": str(e["id"]), 
            "ts": e["timestamp"].isoformat() if e.get("timestamp") else "", 
            "level": (e.get("type","") or "").upper(), 
            "msg": e.get("message","")
        })
    return out

@app.get("/api/admin/pi-events")
def get_pi_events(society_id: str, since: int = 0, user: dict = Depends(require_society_access)):
    sid = int(society_id)
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM pi_events WHERE society_id = %s ORDER BY timestamp DESC LIMIT 100", (sid,))
            events = cur.fetchall()
            mapped = map_events(events)
            return {"events": mapped[since:], "total": len(mapped), "next": len(mapped)}
    finally:
        conn.close()

@app.get("/api/member/dashboard")
def member_dashboard(user: dict = Depends(get_current_user)):
    if user.get("role") != "member":
        raise HTTPException(403, "Members only")
    sid = user.get("society_id")
    if not sid:
        raise HTTPException(400, "No society assigned")
        
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM pi_state WHERE society_id = %s", (sid,))
            pi = cur.fetchone()
            if not pi:
                return {"connected": False}

            wings_data = {}
            for wid, w in pi.get("wings", {}).items():
                if not wing_is_visible(w): continue
                wings_data[wid] = {
                    "used_days": w.get("used_days", 0),
                    "target_days": w.get("target_days", 0),
                    "name": w.get("name", wid),
                    "display_name": w.get("display_name", ""),
                    "clicks": w.get("clicks", 0),
                    "physical_toggle": w.get("physical_toggle", "UNKNOWN"),
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
def member_events(since: int = 0, user: dict = Depends(get_current_user)):
    if user.get("role") != "member":
        raise HTTPException(403, "Members only")
    sid = user.get("society_id")
    if not sid:
        raise HTTPException(400, "No society assigned")
        
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM pi_events WHERE society_id = %s ORDER BY timestamp DESC LIMIT 100", (sid,))
            events = cur.fetchall()
            mapped = map_events(events)
            return {"events": mapped[since:], "total": len(mapped), "next": len(mapped)}
    finally:
        conn.close()