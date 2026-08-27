"""
EMS SaaS Backend v3.0 — Production
====================================
Wings: A, B, G
Reset Day: 15 (default, configurable per society)
Commands: set_active_wing, set_days, set_reset_day, restart,
          reboot, reset_days, off_wing, off_all, lcd_display
Pi auth: strict key verification, never overwrites stored key
Wing visibility: target_days > 0 AND physical_toggle == ON AND not disabled
"""

import os
import json
import time
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
# CORS
# ================================================================

ALLOWED_ORIGINS = [
    "https://ems-saas-three.vercel.app",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5500",
    "http://localhost:5500",
]

app = FastAPI(title="EMS SaaS API", version="3.0.0")

# ================================================================
# AUTO-CREATE TABLE ON STARTUP
# ================================================================

@app.on_event("startup")
def ensure_db_schema():
    if not DATABASE_URL:
        return
    try:
        conn = psycopg.connect(DATABASE_URL, connect_timeout=10)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_data (
                    key   TEXT PRIMARY KEY,
                    data  JSONB NOT NULL DEFAULT '{}',
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
        conn.close()
        print("DB schema verified OK")
    except Exception as e:
        print(f"DB SCHEMA CHECK ERROR: {e}")

# ================================================================
# EXCEPTION HANDLERS — CORS on every error
# ================================================================

def _cors_headers(origin: str) -> dict:
    valid = origin if origin in ALLOWED_ORIGINS else ALLOWED_ORIGINS[0]
    return {
        "Access-Control-Allow-Origin": valid,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, PATCH, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With",
    }

@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=_cors_headers(request.headers.get("origin", "")),
    )

@app.exception_handler(Exception)
async def unhandled_exc_handler(request: Request, exc: Exception):
    print(f"UNHANDLED {request.method} {request.url}: {type(exc).__name__}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers=_cors_headers(request.headers.get("origin", "")),
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)

# ================================================================
# CONFIG — secrets from env only
# ================================================================

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("WARNING: DATABASE_URL not configured — local JSON only")

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY environment variable is required.")

DEFAULT_RESET_DAY = 15
WING_ORDER = ["A", "B", "G"]

VALID_COMMANDS = {
    "set_active_wing",
    "set_days",
    "set_reset_day",
    "restart",
    "reboot",
    "reset_days",
    "off_wing",
    "off_all",
    "lcd_display",
}

# ================================================================
# DATABASE
# ================================================================

_db_cache = {"data": None, "ts": 0}
DB_CACHE_TTL = 30
INT_KEYS = ("pi_state", "pi_events", "pi_commands")

def get_db():
    if not DATABASE_URL:
        return None
    try:
        conn = psycopg.connect(DATABASE_URL, connect_timeout=10)
        conn.autocommit = True
        return conn
    except Exception as e:
        print(f"DB CONNECT ERROR: {e}")
        return None

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db.json")

def load_db() -> dict:
    now = time.time()
    if _db_cache["data"] and (now - _db_cache["ts"]) < DB_CACHE_TTL:
        return _db_cache["data"]
    default = {"users": [], "societies": [], "pi_state": {}, "pi_events": {}, "pi_commands": {}, "firmware_versions": []}
    conn = get_db()
    if conn:
        try:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT key, data FROM saas_data")
                rows = cur.fetchall()
                db = default.copy()
                for row in rows:
                    db[row["key"]] = row["data"]
                for k in INT_KEYS:
                    if k in db and db[k]:
                        db[k] = {int(ik): iv for ik, iv in db[k].items()}
                _db_cache["data"] = db
                _db_cache["ts"] = now
                return db
        except Exception as e:
            print(f"NEON LOAD ERROR: {e}")
        finally:
            conn.close()
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                db = json.load(f)
            for k in INT_KEYS:
                if k in db and db[k]:
                    db[k] = {int(ik): iv for ik, iv in db[k].items()}
            _db_cache["data"] = db
            _db_cache["ts"] = now
            return db
        except Exception:
            pass
    return default

def save_db(data: dict) -> None:
    _db_cache["data"] = data
    _db_cache["ts"] = time.time()
    conn = get_db()
    if conn:
        try:
            with conn.cursor() as cur:
                for key in list(data.keys()):
                    val = data.get(key, [])
                    if key in INT_KEYS and val:
                        val = {str(ik): iv for ik, iv in val.items()}
                    cur.execute(
                        "INSERT INTO saas_data (key, data) VALUES (%s, %s) "
                        "ON CONFLICT (key) DO UPDATE SET data = %s, updated_at = NOW()",
                        (key, json.dumps(val), json.dumps(val)),
                    )
            return
        except Exception as e:
            print(f"NEON SAVE ERROR: {e}")
        finally:
            conn.close()
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ================================================================
# UTILITIES
# ================================================================

def next_id(items: list) -> str:
    if not items:
        return "1"
    return str(max(int(x.get("id", "0")) for x in items) + 1)

def create_token(data: dict) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + timedelta(days=30)
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def version_gt(v1: str, v2: str) -> bool:
    try:
        p1 = [int(x) for x in str(v1).split(".")]
        p2 = [int(x) for x in str(v2).split(".")]
        for a, b in zip(p1, p2):
            if a > b: return True
            if a < b: return False
        return len(p1) > len(p2)
    except Exception:
        return False

def wing_is_visible(w: dict) -> bool:
    physical = str(w.get("physical_toggle", "UNKNOWN")).upper()
    return (
        int(w.get("target_days", 0)) > 0
        and physical == "ON"
        and not bool(w.get("disabled", False))
    )

# ================================================================
# COMMAND VALIDATION
# ================================================================

def validate_command(command: str, params: dict, wing: str = "") -> None:
    if command not in VALID_COMMANDS:
        raise HTTPException(400, f"Unsupported command: {command}")

    if command in ("set_active_wing", "set_days", "off_wing"):
        if wing not in WING_ORDER:
            raise HTTPException(400, "Valid wing A, B or G is required")

    if command == "set_days":
        try:
            days = int(params.get("days"))
        except Exception:
            raise HTTPException(400, "days must be an integer")
        if not 1 <= days <= 31:
            raise HTTPException(400, "days must be between 1 and 31")

    if command == "set_reset_day":
        try:
            day = int(params.get("day"))
        except Exception:
            raise HTTPException(400, "day must be an integer")
        if not 1 <= day <= 28:
            raise HTTPException(400, "reset day must be 1-28")

    if command == "lcd_display":
        line1 = str(params.get("line1", ""))[:16]
        line2 = str(params.get("line2", ""))[:16]
        if not line1 and not line2:
            raise HTTPException(400, "LCD message cannot be empty")
        params["line1"] = line1
        params["line2"] = line2
        duration = params.get("duration", 10)
        try:
            dur = float(duration)
        except Exception:
            raise HTTPException(400, "duration must be a number")
        if not 1 <= dur <= 120:
            raise HTTPException(400, "duration must be 1-120 seconds")
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

async def require_society_access(
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    if user.get("role") not in ("super_admin", "society_admin"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    if user["role"] != "super_admin":
        requested = str(request.query_params.get("society_id", ""))
        owned = str(user.get("society_id", ""))
        if requested and requested != owned:
            raise HTTPException(status_code=403, detail="Cannot access other society data")
    return user

# ================================================================
# PI AUTHENTICATION — strict, never overwrites stored key
# ================================================================

def authenticate_pi(payload: dict) -> tuple:
    try:
        sid = int(payload.get("societyId"))
    except Exception:
        raise HTTPException(400, "Invalid societyId")

    supplied_key = str(payload.get("key", ""))
    if not supplied_key:
        raise HTTPException(401, "Pi API key required")

    db = load_db()
    society = next(
        (s for s in db.get("societies", []) if int(s.get("id", 0)) == sid),
        None,
    )
    if not society:
        raise HTTPException(403, "Society not registered. Create it in admin dashboard first.")

    stored_key = str(society.get("api_key", ""))
    if not stored_key:
        raise HTTPException(403, "No Pi API key configured for this society.")
    if supplied_key != stored_key:
        raise HTTPException(403, "Invalid Pi API key.")

    return sid, society

# ================================================================
# HEALTH
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
# BOOTSTRAP — one-time, locked after first user
# ================================================================

@app.post("/api/bootstrap")
def bootstrap():
    db = load_db()
    if db["users"]:
        raise HTTPException(400, "System already initialized")

    now_iso = datetime.now(timezone.utc).isoformat()

    db["societies"].append({
        "id": "1",
        "name": "Prestine Pacific",
        "location": "Mumbai",
        "plan": "Basic",
        "status": "active",
        "tailscale_ip": "",
        "pi_port": 5000,
        "api_key": "",
        "society_code": "prestine",
    })
    db["users"].append({
        "id": "1", "email": "admin@ems.com", "name": "Super Admin",
        "password": bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode(),
        "role": "super_admin", "society_id": None,
    })
    db["users"].append({
        "id": "2", "email": "admin@prestine.com", "name": "Prestine Admin",
        "password": bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode(),
        "role": "society_admin", "society_id": "1",
    })
    db["users"].append({
        "id": "3", "email": "member@prestine.com", "name": "Prestine Member",
        "password": bcrypt.hashpw(b"member123", bcrypt.gensalt()).decode(),
        "role": "member", "society_id": "1",
    })
    db["pi_state"][1] = {
        "active_wing": "A",
        "wings": {
            "A": {"name": "A", "display_name": "Wing A", "used_days": 0, "target_days": 9, "clicks": 0, "disabled": False, "physical_toggle": "UNKNOWN"},
            "B": {"name": "B", "display_name": "Wing B", "used_days": 0, "target_days": 12, "clicks": 0, "disabled": False, "physical_toggle": "UNKNOWN"},
            "G": {"name": "G", "display_name": "Wing G", "used_days": 0, "target_days": 10, "clicks": 0, "disabled": False, "physical_toggle": "UNKNOWN"},
        },
        "reset_day": DEFAULT_RESET_DAY,
        "emergency_stop": False,
        "firmware_version": "0.0.0",
        "uptime_seconds": 0, "cpu_temp": 0, "disk_free_mb": 0,
        "last_sync": now_iso, "boot_count": 0, "last_shutdown_reason": None,
        "clock_source": "ntp", "watchdog_enabled": True, "last_reboot_reason": None,
    }
    db["pi_events"][1] = [{"id": 1, "timestamp": now_iso, "type": "system", "message": "System bootstrapped"}]
    db["pi_commands"][1] = None
    save_db(db)
    return {"message": "Initialized. Change default passwords and set society API key."}

# ================================================================
# AUTH LOGIN
# ================================================================

@app.post("/api/auth/login")
def login(user: UserLogin):
    db = load_db()
    db_user = next((u for u in db["users"] if u["email"] == user.email), None)
    if not db_user or not bcrypt.checkpw(user.password.encode(), db_user["password"].encode()):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token({
        "id": int(db_user["id"]),
        "role": db_user["role"],
        "society_id": int(db_user["society_id"]) if db_user.get("society_id") else None,
    })
    return {
        "token": token, "role": db_user["role"],
        "name": db_user["name"], "society_id": db_user.get("society_id"),
    }

# ================================================================
# SUPER-ADMIN — SOCIETIES
# ================================================================

@app.get("/api/super-admin/societies")
def get_societies(user: dict = Depends(require_role("super_admin"))):
    db = load_db()
    result = []
    for s in db["societies"]:
        pi = db.get("pi_state", {}).get(int(s["id"]))
        online = False
        if pi and pi.get("last_sync"):
            try:
                online = (datetime.now(timezone.utc) - datetime.fromisoformat(pi["last_sync"])).total_seconds() < 600
            except Exception:
                pass
        wings = pi.get("wings", {}) if pi else {}
        wing_data = {}
        for wid, w in wings.items():
            if not wing_is_visible(w):
                continue
            wing_data[wid] = {
                "name": w.get("name", wid),
                "display_name": w.get("display_name", ""),
                "used_days": w.get("used_days", 0),
                "target_days": w.get("target_days", 0),
                "clicks": w.get("clicks", 0),
                "disabled": w.get("disabled", False),
                "physical_toggle": w.get("physical_toggle", "UNKNOWN"),
            }
        result.append({
            "id": s["id"], "name": s["name"], "location": s["location"],
            "plan": s["plan"], "status": s.get("status", "active"),
            "tailscale_ip": s.get("tailscale_ip", ""), "pi_port": s.get("pi_port", 5000),
            "api_key": s.get("api_key", ""), "society_code": s.get("society_code", ""),
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

@app.post("/api/super-admin/societies/save")
def save_society(data: dict, user: dict = Depends(require_role("super_admin"))):
    db = load_db()
    sid = data.get("id")
    society = {
        "name": data.get("name", ""), "location": data.get("location", ""),
        "plan": data.get("plan", "Basic"), "status": "active",
        "tailscale_ip": data.get("tailscale_ip", ""),
        "pi_port": int(data.get("pi_port", 5000)),
        "api_key": data.get("api_key", ""), "society_code": data.get("society_code", ""),
    }
    if sid:
        for s in db["societies"]:
            if s["id"] == sid:
                s.update(society)
                break
    else:
        society["id"] = next_id(db["societies"])
        db["societies"].append(society)
    save_db(db)
    return {"message": "Saved"}

@app.post("/api/super-admin/societies/delete")
def delete_society(data: dict, user: dict = Depends(require_role("super_admin"))):
    db = load_db()
    sid = data.get("id")
    db["societies"] = [s for s in db["societies"] if s["id"] != sid]
    for key in INT_KEYS:
        db.get(key, {}).pop(int(sid), None)
    db["users"] = [u for u in db["users"] if u.get("society_id") != sid]
    save_db(db)
    return {"message": "Deleted"}

# ================================================================
# SUPER-ADMIN — USERS
# ================================================================

@app.get("/api/super-admin/users")
def get_users(user: dict = Depends(require_role("super_admin"))):
    db = load_db()
    users = []
    for u in db["users"]:
        soc = next((s for s in db["societies"] if s["id"] == u.get("society_id")), None)
        users.append({
            "id": u["id"], "email": u["email"], "name": u["name"],
            "role": u["role"], "society_name": soc["name"] if soc else "None",
            "society_id": u.get("society_id"),
        })
    return users

@app.post("/api/super-admin/users/save")
def save_user(data: dict, user: dict = Depends(require_role("super_admin"))):
    db = load_db()
    uid = data.get("id")
    if uid:
        for u in db["users"]:
            if u["id"] == uid:
                if data.get("password"):
                    u["password"] = bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode()
                for k in ("email", "name", "role", "society_id"):
                    if k in data:
                        u[k] = data[k]
                break
    else:
        if not data.get("password"):
            raise HTTPException(400, "Password required")
        db["users"].append({
            "id": next_id(db["users"]), "email": data["email"], "name": data["name"],
            "role": data["role"], "society_id": data.get("society_id") or None,
            "password": bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode(),
        })
    save_db(db)
    return {"message": "Saved"}

@app.post("/api/super-admin/users/delete")
def delete_user(data: dict, user: dict = Depends(require_role("super_admin"))):
    db = load_db()
    db["users"] = [u for u in db["users"] if u["id"] != data.get("id")]
    save_db(db)
    return {"message": "Deleted"}

# ================================================================
# SUPER-ADMIN — FIRMWARE
# ================================================================

@app.get("/api/super-admin/firmware/versions")
def get_firmware_versions(user: dict = Depends(require_role("super_admin"))):
    db = load_db()
    versions = db.get("firmware_versions", [])
    for v in versions:
        v.pop("code", None)
    return versions

@app.post("/api/super-admin/firmware/save")
def save_firmware_version(data: dict, user: dict = Depends(require_role("super_admin"))):
    db = load_db()
    version = data.get("version", "").strip()
    code = data.get("code", "")
    changelog = data.get("changelog", "")
    forced = data.get("forced", False)
    if not version or not code:
        raise HTTPException(400, "Version and code required")
    if "firmware_versions" not in db:
        db["firmware_versions"] = []
    existing = next((v for v in db["firmware_versions"] if v["version"] == version), None)
    if existing:
        existing["code"] = code
        existing["changelog"] = changelog
        existing["forced"] = forced
        existing["updated_at"] = datetime.now(timezone.utc).isoformat()
    else:
        db["firmware_versions"].insert(0, {
            "version": version, "code": code, "changelog": changelog,
            "forced": forced, "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    if forced:
        for v in db["firmware_versions"]:
            if v["version"] != version:
                v["forced"] = False
    save_db(db)
    return {"message": "Saved"}

@app.post("/api/super-admin/firmware/delete")
def delete_firmware_version(data: dict, user: dict = Depends(require_role("super_admin"))):
    db = load_db()
    version = data.get("version")
    db["firmware_versions"] = [v for v in db.get("firmware_versions", []) if v["version"] != version]
    save_db(db)
    return {"message": "Deleted"}

@app.post("/api/super-admin/firmware/force")
def force_firmware(data: dict, user: dict = Depends(require_role("super_admin"))):
    db = load_db()
    version = data.get("version")
    if "firmware_versions" not in db:
        db["firmware_versions"] = []
    for v in db["firmware_versions"]:
        v["forced"] = (v["version"] == version)
    save_db(db)
    return {"message": "Force flag updated"}

@app.get("/api/pi/firmware-download")
def download_firmware(version: str, key: str = ""):
    if not key:
        raise HTTPException(403, "API key required")
    db = load_db()
    society = next((s for s in db["societies"] if s.get("api_key") == key), None)
    if not society:
        raise HTTPException(403, "Invalid API key")
    fv = next((v for v in db.get("firmware_versions", []) if v["version"] == version), None)
    if not fv:
        raise HTTPException(404, "Version not found")
    return PlainTextResponse(fv["code"], media_type="text/plain")

# ================================================================
# PI SYNC — camelCase → snake_case, strict auth
# ================================================================

@app.post("/api/pi/sync")
def pi_sync(payload: dict):
    sid, society = authenticate_pi(payload)
    db = load_db()
    now = datetime.now(timezone.utc).isoformat()

    wings = {}
    for wid, w in payload.get("wings", {}).items():
        physical_toggle = w.get("physicalToggle", "UNKNOWN")
        if isinstance(physical_toggle, bool):
            physical_toggle = "ON" if physical_toggle else "OFF"
        physical_toggle = str(physical_toggle).upper()
        if physical_toggle not in ("ON", "OFF", "UNKNOWN"):
            physical_toggle = "UNKNOWN"

        wings[wid] = {
            "name": w.get("name", wid),
            "display_name": w.get("display_name", w.get("name", wid)),
            "used_days": int(w.get("usedDays", 0)),
            "target_days": int(w.get("targetDays", 0)),
            "clicks": int(w.get("clicks", 0)),
            "disabled": bool(w.get("disabled", False)),
            "physical_toggle": physical_toggle,
        }

    pi_state = {
        "active_wing": payload.get("activeWing"),
        "wings": wings,
        "reset_day": int(payload.get("resetDay", DEFAULT_RESET_DAY)),
        "emergency_stop": bool(payload.get("emergencyStop", False)),
        "firmware_version": payload.get("firmwareVersion", "?"),
        "uptime_seconds": int(payload.get("uptimeSeconds", 0)),
        "cpu_temp": float(payload.get("cpuTemp", 0)),
        "disk_free_mb": float(payload.get("diskFreeMB", 0)),
        "last_sync": now,
        "boot_count": int(payload.get("bootCount", 0)),
        "last_shutdown_reason": payload.get("lastShutdownReason", ""),
        "clock_source": payload.get("clockSource", ""),
        "watchdog_enabled": bool(payload.get("watchdogEnabled", False)),
        "last_reboot_reason": payload.get("lastRebootReason", ""),
    }

    db.setdefault("pi_state", {})
    pi_state["last_sync"] = datetime.now(timezone.utc).isoformat()
    db["pi_state"][sid] = pi_state

    db.setdefault("pi_events", {})
    db["pi_events"].setdefault(sid, [])
    for event in payload.get("events", []):
        db["pi_events"][sid].append(event)
    if len(db["pi_events"][sid]) > 500:
        db["pi_events"][sid] = db["pi_events"][sid][-500:]

    save_db(db)

    reply = {"success": True, "command": None}
    cmds = db.get("pi_commands", {}).get(sid, [])
    if isinstance(cmds, dict): cmds = [cmds]; db.setdefault("pi_commands", {}); db["pi_commands"][sid] = cmds
    for cmd in cmds:
        if cmd.get("status") == "pending":
            reply["command"] = cmd["command"]
            reply["command_id"] = cmd["id"]
            if cmd.get("wing"): reply["wing"] = cmd["wing"]
            reply["params"] = cmd.get("params", {})
            cmd["status"] = "sent"
            cmd["sent_at"] = datetime.now(timezone.utc).isoformat()
            break
    save_db(db)
    return reply

# ================================================================
# PI COMMAND ACK
# ================================================================

@app.post("/api/pi/command-ack")
def pi_command_ack(payload: dict):
    sid, _ = authenticate_pi(payload)
    command_id = payload.get("command_id")
    if not command_id:
        raise HTTPException(400, "command_id required")

    success = bool(payload.get("success", True))
    error = payload.get("error")
    result = payload.get("result")

    db = load_db()
    cmds = db.get("pi_commands", {}).get(sid, [])
    if isinstance(cmds, dict): cmds = [cmds]
    found = next((cmd for cmd in cmds if cmd.get("id") == str(command_id)), None)
    if not found:
        raise HTTPException(404, "Command not found")

    found["acked_at"] = datetime.now(timezone.utc).isoformat()
    found["status"] = "acknowledged" if success else "failed"
    found["error"] = None if success else error
    found["result"] = result
    save_db(db)
    return {"success": True, "status": found["status"]}

# ================================================================
# ADMIN — COMMAND QUEUE
# ================================================================

@app.post("/api/admin/pi-command")
def queue_command(data: dict, user: dict = Depends(get_current_user)):
    db = load_db()
    try:
        sid = int(data.get("society_id"))
    except Exception:
        raise HTTPException(400, "Invalid society_id")

    if user.get("role") != "super_admin" and str(user.get("society_id")) != str(sid):
        raise HTTPException(403, "Cannot access other society data")

    command = str(data.get("command", ""))
    wing = str(data.get("wing", ""))
    params = dict(data.get("params", {}))

    validate_command(command, params, wing)

    command_id = str(int(time.time() * 1000))
    new_cmd = {
        "id": command_id, "command": command, "wing": wing, "params": params,
        "queued_at": datetime.now(timezone.utc).isoformat(), "status": "pending",
        "sent_at": None, "acked_at": None, "error": None, "result": None,
    }
    db.setdefault("pi_commands", {})
    if sid not in db["pi_commands"]: db["pi_commands"][sid] = []
    if isinstance(db["pi_commands"][sid], dict): db["pi_commands"][sid] = [db["pi_commands"][sid]]
    db["pi_commands"][sid].append(new_cmd)
    save_db(db)
    return {"success": True, "message": "Command queued", "command": command, "command_id": command_id}

# ================================================================
# ADMIN — DASHBOARD / STATE / EVENTS
# ================================================================

@app.get("/api/admin/dashboard")
def admin_dashboard(society_id: str = "", user: dict = Depends(require_society_access)):
    if not society_id:
        raise HTTPException(400, "society_id required")
    db = load_db()
    pi = db.get("pi_state", {}).get(int(society_id))
    if not pi:
        return {"connected": False}

    wings_data = {}
    for wid, w in pi.get("wings", {}).items():
        if not wing_is_visible(w):
            continue
        wings_data[wid] = {
            "used_days": w.get("used_days", 0),
            "target_days": w.get("target_days", 0),
            "status": "ACTIVE" if pi.get("active_wing") == wid else "IDLE",
            "name": w.get("name", wid),
            "display_name": w.get("display_name", ""),
            "disabled": w.get("disabled", False),
            "physical_toggle": w.get("physical_toggle", "UNKNOWN"),
            "clicks": w.get("clicks", 0),
        }

    cmds = db.get("pi_commands", {}).get(int(society_id), [])
    if isinstance(cmds, dict): cmds = [cmds]
    pc = next((c for c in cmds if c.get("status") in ("pending","sent")), None)
    return {
        "connected": True,
        "active_wing": pi.get("active_wing"),
        "reset_day": pi.get("reset_day", DEFAULT_RESET_DAY),
        "wings": wings_data,
        "emergency_stop": pi.get("emergency_stop", False),
        "watchdog_enabled": pi.get("watchdog_enabled", False),
        "last_reboot_reason": pi.get("last_reboot_reason", ""),
        "firmware_version": pi.get("firmware_version", "?"),
        "cpu_temp": pi.get("cpu_temp", 0),
        "uptime_seconds": pi.get("uptime_seconds", 0),
        "last_sync": pi.get("last_sync"),
        "pending_command": {
            "id": pc.get("id"), "command": pc.get("command"),
            "status": pc.get("status"), "queued_at": pc.get("queued_at"),
            "sent_at": pc.get("sent_at"), "acked_at": pc.get("acked_at"),
            "error": pc.get("error"),
        } if pc else None,
    }

@app.get("/api/admin/pi-state")
def get_pi_state(society_id: str, user: dict = Depends(require_society_access)):
    db = load_db()
    state = db.get("pi_state", {}).get(int(society_id))
    if not state:
        return {"connected": False}
    filtered = {wid: w for wid, w in state.get("wings", {}).items() if wing_is_visible(w)}
    return {"connected": True, **{k: v for k, v in state.items() if k != "wings"}, "wings": filtered}

def map_events(raw):
    out = []
    for e in raw:
        if isinstance(e, dict) and e.get("message"):
            out.append({"id": e.get("id",0), "ts": e.get("timestamp",""), "level": (e.get("type","") or "").upper(), "msg": e.get("message","")})
    return out

@app.get("/api/admin/pi-events")
def get_pi_events(society_id: str, since: int = 0, user: dict = Depends(require_society_access)):
    db = load_db()
    events = map_events(db.get("pi_events", {}).get(int(society_id), []))
    return {"events": events[since:], "total": len(events), "next": len(events)}

# ================================================================
# MEMBER
# ================================================================

@app.get("/api/member/dashboard")
def member_dashboard(user: dict = Depends(get_current_user)):
    if user.get("role") != "member":
        raise HTTPException(403, "Members only")
    sid = user.get("society_id")
    if not sid:
        raise HTTPException(400, "No society assigned")
    db = load_db()
    pi = db.get("pi_state", {}).get(int(sid))
    if not pi:
        return {"connected": False}

    wings_data = {}
    for wid, w in pi.get("wings", {}).items():
        if not wing_is_visible(w):
            continue
        wings_data[wid] = {
            "used_days": w.get("used_days", 0),
            "target_days": w.get("target_days", 0),
            "name": w.get("name", wid),
            "display_name": w.get("display_name", ""),
            "clicks": w.get("clicks", 0),
            "physical_toggle": w.get("physical_toggle", "UNKNOWN"),
        }

    return {
        "connected": True, "active_wing": pi.get("active_wing"),
        "wings": wings_data, "reset_day": pi.get("reset_day", DEFAULT_RESET_DAY),
        "firmware_version": pi.get("firmware_version", "?"),
        "cpu_temp": pi.get("cpu_temp", 0),
        "uptime_seconds": pi.get("uptime_seconds", 0),
        "last_sync": pi.get("last_sync"),
    }

@app.get("/api/member/events")
def member_events(since: int = 0, user: dict = Depends(get_current_user)):
    if user.get("role") != "member":
        raise HTTPException(403, "Members only")
    sid = user.get("society_id")
    if not sid:
        raise HTTPException(400, "No society assigned")
    db = load_db()
    events = map_events(db.get("pi_events", {}).get(int(sid), []))
    return {"events": events[since:], "total": len(events), "next": len(events)}