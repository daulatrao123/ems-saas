import os, json, time
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import bcrypt
from jose import JWTError, jwt
from datetime import datetime, timezone, timedelta

app = FastAPI(title="EMS SaaS API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

NEON_DB_URL = os.getenv("DATABASE_URL", "postgresql://neondb_owner:npg_WRC1zyOo8IKX@ep-hidden-shadow-az9k7nq9.c-3.ap-southeast-1.aws.neon.tech/neondb?sslmode=require")

_db_cache = {"data": None, "ts": 0}
DB_CACHE_TTL = 2

def get_db():
    try:
        conn = psycopg2.connect(NEON_DB_URL)
        conn.autocommit = True
        return conn
    except:
        return None

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db.json")
SECRET_KEY = os.getenv("SECRET_KEY", "ems_super_secret_2026")
_keepalive_ts = time.time()

def load_db():
    now = time.time()
    if _db_cache["data"] and (now - _db_cache["ts"]) < DB_CACHE_TTL:
        return _db_cache["data"]
    default = {"users": [], "societies": [], "pi_state": {}, "pi_events": {}, "pi_commands": [], "firmware_versions": []}
    conn = get_db()
    if conn:
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT key, data FROM saas_data")
                rows = cur.fetchall()
                db = default.copy()
                for row in rows:
                    val = row["data"]
                    if row["key"] in ["pi_state", "pi_events", "pi_commands"] and val:
                        val = {int(k): v for k, v in val.items()}
                    db[row["key"]] = val
                _db_cache["data"] = db
                _db_cache["ts"] = now
                return db
        except Exception as e:
            print(f"NEON LOAD ERROR: {e}")
        finally:
            conn.close()
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            try:
                db = json.load(f)
            except:
                return default
            for nk in ["pi_state", "pi_events", "pi_commands"]:
                if nk in db and db[nk]:
                    db[nk] = {int(k): v for k, v in db[nk].items()}
            _db_cache["data"] = db
            _db_cache["ts"] = now
            return db
    return default

def save_db(data):
    _db_cache["data"] = data
    _db_cache["ts"] = time.time()
    conn = get_db()
    if conn:
        try:
            with conn.cursor() as cur:
                for key in ["users", "societies", "pi_state", "pi_events", "pi_commands", "firmware_versions"]:
                    val = data.get(key, [])
                    if key in ["pi_state", "pi_events", "pi_commands"] and val:
                        val = {str(k): v for k, v in val.items()}
                    cur.execute("INSERT INTO saas_data (key, data) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET data = %s, updated_at = NOW()", (key, json.dumps(val), json.dumps(val)))
            return
        except Exception as e:
            print(f"NEON SAVE ERROR: {e}")
        finally:
            conn.close()
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

def next_id(items):
    if not items:
        return "1"
    return str(max(int(x.get("id", "0")) for x in items) + 1)

def create_token(data: dict):
    to_encode = data.copy()
    to_encode.update({"exp": datetime.now(timezone.utc) + timedelta(days=30)})
    return jwt.encode(to_encode, SECRET_KEY, algorithm="HS256")

def version_gt(v1, v2):
    try:
        p1 = [int(x) for x in str(v1).split(".")]
        p2 = [int(x) for x in str(v2).split(".")]
        for a, b in zip(p1, p2):
            if a > b:
                return True
            if a < b:
                return False
        return len(p1) > len(p2)
    except:
        return False

class UserLogin(BaseModel):
    email: str
    password: str

async def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Valid token required")
    try:
        return jwt.decode(authorization[7:], SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

def require_role(*allowed_roles):
    async def checker(user: dict = Depends(get_current_user)):
        if user.get("role") not in allowed_roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return checker

# FIXED: Direct dependency instead of broken factory pattern.
# The old version was a factory that returned a function, but FastAPI
# passed that function object as `user` — the auth checks never ran.
async def require_society_access(
    request: Request,
    user: dict = Depends(get_current_user)
):
    if user.get("role") not in ["super_admin", "society_admin"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    if user["role"] != "super_admin" and str(user.get("society_id")) != str(request.query_params.get("society_id")):
        raise HTTPException(status_code=403, detail="Cannot access other society data")
    return user

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

@app.post("/api/seed")
def seed_db():
    db = load_db()
    if not any(u.get("role") == "super_admin" for u in db["users"]):
        db["users"].append({"id": next_id(db["users"]), "email": "admin@ems.com", "name": "Super Admin", "password": bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode("utf-8"), "role": "super_admin", "society_id": None})
    if not any(s.get("name") == "Prestine Pacific" for s in db["societies"]):
        db["societies"].append({"id": "1", "name": "Prestine Pacific", "location": "Mumbai", "plan": "Basic", "status": "active", "tailscale_ip": "", "pi_port": 5000, "api_key": "", "society_code": "prestine"})
    if not any(u.get("email") == "admin@prestine.com" for u in db["users"]):
        db["users"].append({"id": next_id(db["users"]), "email": "admin@prestine.com", "name": "Prestine Admin", "role": "society_admin", "society_id": "1", "password": bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode("utf-8")})
    if not any(u.get("email") == "member@prestine.com" for u in db["users"]):
        db["users"].append({"id": next_id(db["users"]), "email": "member@prestine.com", "name": "Prestine Member", "role": "member", "society_id": "1", "password": bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode("utf-8")})
    if "pi_state" not in db:
        db["pi_state"] = {}
    if not db["pi_state"].get(1):
        db["pi_state"][1] = {"active_wing": "A", "wings": {"A": {"name": "A", "display_name": "Wing A", "used_days": 3, "target_days": 30, "clicks": 15, "disabled": False, "physical_toggle": True}, "B": {"name": "B", "display_name": "Wing B", "used_days": 3, "target_days": 30, "clicks": 22, "disabled": False, "physical_toggle": True}, "C": {"name": "C", "display_name": "Wing C", "used_days": 2, "target_days": 30, "clicks": 8, "disabled": False, "physical_toggle": False}}, "reset_day": 1, "emergency_stop": False, "firmware_version": "2.1.0", "uptime_seconds": 5094, "cpu_temp": 47.2, "disk_free_mb": 1024, "last_sync": datetime.now(timezone.utc).isoformat(), "boot_count": 42, "last_shutdown_reason": None, "clock_source": "ntp", "locked": False, "pending_start": False, "quota_lock_until": "", "reset_day_lock_until": "", "watchdog_enabled": True, "last_reboot_reason": "scheduled"}
    if "pi_events" not in db:
        db["pi_events"] = {}
    if not db["pi_events"].get(1):
        now_iso = datetime.now(timezone.utc).isoformat()
        db["pi_events"][1] = [{"id": 1, "timestamp": now_iso, "type": "system", "message": "System started"}, {"id": 2, "timestamp": now_iso, "type": "sync", "message": "Data synced"}, {"id": 3, "timestamp": now_iso, "type": "click", "message": "Wing A clicked"}, {"id": 4, "timestamp": now_iso, "type": "system", "message": "Health check passed"}, {"id": 5, "timestamp": now_iso, "type": "sync", "message": "State updated"}]
    save_db(db)
    for nk in ["pi_state", "pi_events", "pi_commands"]:
        if nk in db and db[nk]:
            rebuilt = {int(k): v for k, v in db[nk].items()}
            if rebuilt != db[nk]:
                db[nk] = rebuilt
                save_db(db)
    return {"message": "Seeded!"}

@app.on_event("startup")
async def auto_seed():
    seed_db()

@app.post("/api/auth/login")
def login(user: UserLogin):
    db = load_db()
    db_user = next((u for u in db["users"] if u["email"] == user.email), None)
    if not db_user or not bcrypt.checkpw(user.password.encode("utf-8"), db_user["password"].encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token({"id": int(db_user["id"]), "role": db_user["role"], "society_id": int(db_user["society_id"]) if db_user.get("society_id") else None})
    return {"token": token, "role": db_user["role"], "name": db_user["name"], "society_id": db_user.get("society_id")}

@app.get("/api/super-admin/societies")
def get_societies(user: dict = Depends(require_role("super_admin"))):
    db = load_db()
    result = []
    for s in db["societies"]:
        pi = db.get("pi_state", {}).get(int(s["id"]))
        online = pi and (datetime.now(timezone.utc) - datetime.fromisoformat(pi.get("last_sync", "2020-01-01T00:00:00"))).total_seconds() < 360
        wings = pi.get("wings", {}) if pi else {}
        wing_toggles = {}
        for wid, w in wings.items():
            if w.get("target_days", 0) == 0:
                continue
            wing_toggles[wid] = {"name": w.get("name", wid), "used_days": w.get("used_days", 0), "target_days": w.get("target_days", 0), "clicks": w.get("clicks", 0), "disabled": w.get("disabled", False), "physical_toggle": w.get("physical_toggle", "UNKNOWN"), "display_name": w.get("display_name", "")}
        result.append({"id": s["id"], "name": s["name"], "location": s["location"], "plan": s["plan"], "status": s.get("status", "active"), "tailscale_ip": s.get("tailscale_ip", ""), "pi_port": s.get("pi_port", 5000), "api_key": s.get("api_key", ""), "society_code": s.get("society_code", ""), "pi_online": online, "last_sync": pi.get("last_sync") if pi else None, "active_wing": pi.get("active_wing") if pi else None, "emergency_stop": pi.get("emergency_stop", False) if pi else False, "firmware_version": pi.get("firmware_version", "?") if pi else None, "quota_lock_until": pi.get("quota_lock_until", "") if pi else "", "reset_day": pi.get("reset_day", 22) if pi else 22, "reset_day_lock_until": pi.get("reset_day_lock_until", "") if pi else "", "wings": wing_toggles, "watchdog_enabled": pi.get("watchdog_enabled", False) if pi else False, "last_reboot_reason": pi.get("last_reboot_reason", "") if pi else ""})
    return result

@app.post("/api/super-admin/societies/save")
def save_society(data: dict, user: dict = Depends(require_role("super_admin"))):
    db = load_db()
    sid = data.get("id")
    society = {"name": data.get("name", ""), "location": data.get("location", ""), "plan": data.get("plan", "Basic"), "status": "active", "tailscale_ip": data.get("tailscale_ip", ""), "pi_port": int(data.get("pi_port", 5000)), "api_key": data.get("api_key", ""), "society_code": data.get("society_code", "")}
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
    for key in ["pi_state", "pi_events", "pi_commands"]:
        db.get(key, {}).pop(int(sid), None)
    db["users"] = [u for u in db["users"] if u.get("society_id") != sid]
    save_db(db)
    return {"message": "Deleted"}

@app.get("/api/super-admin/users")
def get_users(user: dict = Depends(require_role("super_admin"))):
    db = load_db()
    users = []
    for u in db["users"]:
        soc = next((s for s in db["societies"] if s["id"] == u.get("society_id")), None)
        users.append({"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"], "society_name": soc["name"] if soc else "None", "society_id": u.get("society_id")})
    return users

@app.post("/api/super-admin/users/save")
def save_user(data: dict, user: dict = Depends(require_role("super_admin"))):
    db = load_db()
    uid = data.get("id")
    if uid:
        for u in db["users"]:
            if u["id"] == uid:
                if data.get("password"):
                    u["password"] = bcrypt.hashpw(data["password"].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                for k in ["email", "name", "role", "society_id"]:
                    if k in data and k != "password":
                        u[k] = data[k]
                break
    else:
        if not data.get("password"):
            raise HTTPException(400, "Password required")
        new_user = {"id": next_id(db["users"]), "email": data["email"], "name": data["name"], "role": data["role"], "society_id": data.get("society_id") or None, "password": bcrypt.hashpw(data["password"].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")}
        db["users"].append(new_user)
    save_db(db)
    return {"message": "Saved"}

@app.post("/api/super-admin/users/delete")
def delete_user(data: dict, user: dict = Depends(require_role("super_admin"))):
    db = load_db()
    db["users"] = [u for u in db["users"] if u["id"] != data.get("id")]
    save_db(db)
    return {"message": "Deleted"}

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
        db["firmware_versions"].insert(0, {"version": version, "code": code, "changelog": changelog, "forced": forced, "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()})
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

@app.post("/api/pi/sync")
def pi_sync(payload: dict):
    sid = int(payload.get("societyId", 0))
    db = load_db()
    society = next((s for s in db["societies"] if int(s["id"]) == sid), None)
    if not society:
        society = next((s for s in db["societies"] if str(s.get("society_code", "")) == str(sid)), None)
    if not society:
        new_id = next_id(db["societies"])
        society = {"id": new_id, "name": payload.get("societyName", sid), "location": "Auto-detected", "plan": "Basic", "status": "active", "tailscale_ip": "", "pi_port": 5000, "api_key": payload.get("key", ""), "society_code": sid}
        db["societies"].append(society)
        sid = new_id
    else:
        sid = int(society["id"])
    society["online"] = True
    society["last_seen"] = datetime.now(timezone.utc).isoformat()
    if payload.get("key"):
        society["api_key"] = payload["key"]
    wings = {}
    for wid, w in payload.get("wings", {}).items():
        wings[wid] = {"name": w.get("name", wid), "display_name": w.get("display_name", ""), "used_days": w.get("usedDays", 0), "target_days": w.get("targetDays", 0), "clicks": w.get("clicks", 0), "disabled": w.get("disabled", False), "physical_toggle": w.get("physicalToggle", "UNKNOWN")}
    pi_state = {"active_wing": payload.get("activeWing"), "wings": wings, "reset_day": payload.get("resetDay", 22), "emergency_stop": payload.get("emergencyStop", False), "firmware_version": payload.get("firmwareVersion", "?"), "uptime_seconds": payload.get("uptimeSeconds", 0), "cpu_temp": payload.get("cpuTemp", 0), "disk_free_mb": payload.get("diskFreeMB", 0), "last_sync": datetime.now(timezone.utc).isoformat(), "boot_count": payload.get("bootCount", 0), "last_shutdown_reason": payload.get("lastShutdownReason", ""), "clock_source": payload.get("clockSource", ""), "locked": payload.get("locked", False), "pending_start": payload.get("pendingStart", False), "quota_lock_until": payload.get("quota_lock_until", ""), "reset_day_lock_until": payload.get("reset_day_lock_until", ""), "watchdog_enabled": payload.get("watchdog_enabled", False), "last_reboot_reason": payload.get("last_reboot_reason", "")}
    if "pi_state" not in db:
        db["pi_state"] = {}
    db["pi_state"][sid] = pi_state
    if "pi_events" not in db:
        db["pi_events"] = {}
    if sid not in db["pi_events"]:
        db["pi_events"][sid] = []
    for ev in payload.get("events", []):
        db["pi_events"][sid].append(ev)
    if len(db["pi_events"][sid]) > 500:
        db["pi_events"][sid] = db["pi_events"][sid][-500:]
    save_db(db)
    reply = {"success": True, "command": None}
    cmds = db.get("pi_commands", {})
    if sid in cmds and cmds[sid].get("command"):
        reply["command"] = cmds[sid]["command"]
        if cmds[sid].get("wing"):
            reply["wing"] = cmds[sid]["wing"]
        reply["params"] = cmds[sid].get("params", {})
        cmds[sid] = {"command": None, "wing": None, "params": {}, "queued_at": None}
    fw_versions = db.get("firmware_versions", [])
    pi_ver = payload.get("firmwareVersion", "0.0.0")
    forced_fw = next((v for v in fw_versions if v.get("forced")), None)
    latest_fw = fw_versions[0] if fw_versions else None
    target_fw = forced_fw or latest_fw
    if target_fw and version_gt(target_fw["version"], pi_ver):
        reply["firmware_update"] = {"version": target_fw["version"], "forced": target_fw.get("forced", False), "changelog": target_fw.get("changelog", "")}
    save_db(db)
    return reply

@app.get("/api/pi/firmware-download")
def download_firmware(version: str, key: str = ""):
    db = load_db()
    if key:
        society = next((s for s in db["societies"] if s.get("api_key") == key), None)
        if not society:
            raise HTTPException(403, "Invalid API key")
    fv = next((v for v in db.get("firmware_versions", []) if v["version"] == version), None)
    if not fv:
        raise HTTPException(404, "Version not found")
    return PlainTextResponse(fv["code"], media_type="text/plain")

@app.post("/api/admin/pi-command")
def queue_command(data: dict, user: dict = Depends(get_current_user)):
    db = load_db()
    sid = int(data.get("society_id", 0))
    if user["role"] != "super_admin" and str(user.get("society_id")) != str(sid):
        raise HTTPException(status_code=403, detail="Cannot access other society data")
    cmd = data.get("command", "")
    params = data.get("params", {})
    wing = data.get("wing", "")
    pi = db.get("pi_state", {}).get(sid, {})
    skip_locks = params.get("skip_locks", False)
    if not skip_locks and cmd in ("set_monthly_quota", "set_days") and pi.get("quota_lock_until", ""):
        try:
            lock_until = datetime.fromisoformat(pi["quota_lock_until"])
            if datetime.now(timezone.utc) < lock_until:
                raise HTTPException(400, f"Quota locked until {pi['quota_lock_until']}")
        except HTTPException:
            raise
        except:
            pass
    if not skip_locks and cmd == "set_reset_day" and pi.get("reset_day_lock_until", ""):
        try:
            lock_until = datetime.fromisoformat(pi["reset_day_lock_until"])
            if datetime.now(timezone.utc) < lock_until:
                raise HTTPException(400, f"Reset day locked until {pi['reset_day_lock_until']}")
        except HTTPException:
            raise
        except:
            pass
    if "pi_commands" not in db:
        db["pi_commands"] = {}
    db["pi_commands"][sid] = {"command": cmd, "wing": wing, "params": params, "queued_at": datetime.now(timezone.utc).isoformat()}
    save_db(db)
    return {"success": True, "message": "Command queued", "command": cmd}

@app.get("/api/admin/pi-state")
def get_pi_state(society_id: str, user: dict = Depends(require_society_access)):
    db = load_db()
    state = db.get("pi_state", {}).get(int(society_id))
    if not state:
        return {"connected": False}
    filtered_wings = {wid: w for wid, w in state.get("wings", {}).items() if w.get("target_days", 0) > 0}
    state["wings"] = filtered_wings
    return {"connected": True, **state}

@app.get("/api/admin/pi-events")
def get_pi_events(society_id: str, since: int = 0, user: dict = Depends(require_society_access)):
    db = load_db()
    events = db.get("pi_events", {}).get(int(society_id), [])
    return {"events": events[since:], "total": len(events), "next": len(events)}

@app.get("/api/admin/dashboard")
def admin_dashboard(society_id: str = "", user: dict = Depends(require_society_access)):
    db = load_db()
    if not society_id:
        return {"error": "society_id required"}
    pi = db.get("pi_state", {}).get(int(society_id))
    if not pi:
        return {"connected": False}
    wings_data = {}
    for wid, w in pi.get("wings", {}).items():
        if w.get("target_days", 0) == 0:
            continue
        wings_data[wid] = {"used_days": w.get("used_days", 0), "target_days": w.get("target_days", 0), "status": "ACTIVE" if pi.get("active_wing") == wid else "IDLE", "name": w.get("name", wid), "display_name": w.get("display_name", ""), "disabled": w.get("disabled", False), "physical_toggle": w.get("physical_toggle", "UNKNOWN"), "clicks": w.get("clicks", 0)}
    return {"connected": True, "active_wing": pi.get("active_wing"), "reset_day": pi.get("reset_day", 22), "quota_lock_until": pi.get("quota_lock_until", ""), "reset_day_lock_until": pi.get("reset_day_lock_until", ""), "wings": wings_data, "emergency_stop": pi.get("emergency_stop", False), "watchdog_enabled": pi.get("watchdog_enabled", False), "last_reboot_reason": pi.get("last_reboot_reason", ""), "firmware_version": pi.get("firmware_version", "?")}



@app.get("/api/member/dashboard")
def member_dashboard(user: dict = Depends(get_current_user)):
    if user.get("role") != "member":
        raise HTTPException(status_code=403, detail="Members only")
    sid = user.get("society_id")
    if not sid:
        raise HTTPException(status_code=400, detail="No society assigned")
    db = load_db()
    pi = db.get("pi_state", {}).get(int(sid))
    if not pi:
        return {"connected": False}
    wings_data = {}
    for wid, w in pi.get("wings", {}).items():
        if w.get("target_days", 0) == 0:
            continue
        wings_data[wid] = {"used_days": w.get("used_days", 0), "target_days": w.get("target_days", 0), "name": w.get("name", wid), "display_name": w.get("display_name", ""), "clicks": w.get("clicks", 0)}
    return {"connected": True, "active_wing": pi.get("active_wing"), "wings": wings_data, "reset_day": pi.get("reset_day", 22), "firmware_version": pi.get("firmware_version", "?"), "cpu_temp": pi.get("cpu_temp", 0), "uptime_seconds": pi.get("uptime_seconds", 0), "last_sync": pi.get("last_sync")}



@app.get("/api/member/events")
def member_events(since: int = 0, user: dict = Depends(get_current_user)):
    if user.get("role") != "member":
        raise HTTPException(status_code=403, detail="Members only")
    sid = user.get("society_id")
    if not sid:
        raise HTTPException(status_code=400, detail="No society assigned")
    db = load_db()
    events = db.get("pi_events", {}).get(int(sid), [])
    return {"events": events[since:], "total": len(events), "next": len(events)}

@app.get("/api/member/status")
def member_status(user: dict = Depends(get_current_user)):
    raise HTTPException(status_code=404, detail="Not found")
