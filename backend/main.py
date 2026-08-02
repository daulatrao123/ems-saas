import os, json, time
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import bcrypt
from jose import JWTError, jwt
from datetime import datetime, timezone, timedelta

app = FastAPI(title="EMS SaaS API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

NEON_DB_URL = os.getenv("DATABASE_URL", "postgresql://neondb_owner:npg_WRC1zyOo8IKX@ep-hidden-shadow-az9k7nq9.c-3.ap-southeast-1.aws.neon.tech/neondb?sslmode=require")

def get_db():
    try:
        conn = psycopg2.connect(NEON_DB_URL)
        conn.autocommit = True
        return conn
    except: return None

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db.json")
SECRET_KEY = os.getenv("SECRET_KEY", "ems_super_secret_2026")
_keepalive_ts = time.time()

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            try:
                db = json.load(f)
            except:
                return {"users": [], "societies": [], "pi_state": {}, "pi_events": {}, "pi_commands": [], "firmware_versions": []}
            for nk in ["pi_state", "pi_events", "pi_commands"]:
                if nk in db and db[nk]:
                    db[nk] = {int(k): v for k, v in db[nk].items()}
            return db
    return {"users": [], "societies": [], "pi_state": {}, "pi_events": {}, "pi_commands": {}, "firmware_versions": []}

def save_db(data):
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
    with open(DB_FILE, "w") as f: json.dump(data, f, indent=2)

def next_id(items):
    if not items: return "1"
    return str(max(int(x.get("id", "0")) for x in items) + 1)

def create_token(data: dict):
    to_encode = data.copy()
    to_encode.update({"exp": datetime.utcnow() + timedelta(days=30)})
    return jwt.encode(to_encode, SECRET_KEY, algorithm="HS256")

def version_gt(v1, v2):
    try:
        p1 = [int(x) for x in str(v1).split(".")]
        p2 = [int(x) for x in str(v2).split(".")]
        for a, b in zip(p1, p2):
            if a > b: return True
            if a < b: return False
        return len(p1) > len(p2)
    except: return False

class UserLogin(BaseModel):
    email: str
    password: str

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
        db["users"].extend([
            {"id": next_id(db["users"]), "email": "admin@ems.com", "name": "Super Admin", "password": bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode("utf-8"), "role": "super_admin", "society_id": None},
        ])
        save_db(db)

    for nk in ["pi_state", "pi_events", "pi_commands"]:
        if nk in db and db[nk]:
            rebuilt = {int(k): v for k, v in db[nk].items()}
            if rebuilt != db[nk]:
                db[nk] = rebuilt
                save_db(db)
    return {"message": "Seeded! admin@ems.com / sec@prestine.com / member@prestine.com"}

@app.on_event("startup")
async def auto_seed():
    seed_db()

@app.post("/api/auth/login")
def login(user: UserLogin):
    db = load_db()
    db_user = next((u for u in db["users"] if u["email"] == user.email), None)
    if not db_user or not bcrypt.checkpw(user.password.encode("utf-8"), db_user["password"].encode("utf-8")):
        raise HTTPException(status_code=400, detail="Invalid credentials")
    token = create_token({"id": db_user["id"], "role": db_user["role"], "society_id": db_user.get("society_id")})
    return {"token": token, "role": db_user["role"], "name": db_user["name"], "society_id": db_user.get("society_id")}

@app.get("/api/super-admin/societies")
def get_societies():
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
            wing_toggles[wid] = {
                "name": w.get("name", wid),
                "used_days": w.get("used_days", 0),
                "target_days": w.get("target_days", 0),
                "clicks": w.get("clicks", 0),
                "disabled": w.get("disabled", False),
                "physical_toggle": w.get("physical_toggle", "UNKNOWN"),
                "display_name": w.get("display_name", ""),
            }
        result.append({
            "id": s["id"], "name": s["name"], "location": s["location"], "plan": s["plan"],
            "status": s.get("status", "active"), "tailscale_ip": s.get("tailscale_ip", ""),
            "pi_port": s.get("pi_port", 5000), "api_key": s.get("api_key", ""),
            "society_code": s.get("society_code", ""),
            "pi_online": online,
            "last_sync": pi.get("last_sync") if pi else None,
            "active_wing": pi.get("active_wing") if pi else None,
            "emergency_stop": pi.get("emergency_stop", False) if pi else False,
            "firmware_version": pi.get("firmware_version", "?") if pi else None,
            "quota_lock_until": pi.get("quota_lock_until", "") if pi else "",
            "reset_day": pi.get("reset_day", 22) if pi else 22,
            "reset_day_lock_until": pi.get("reset_day_lock_until", "") if pi else "",
            "wings": wing_toggles,
            "watchdog_enabled": pi.get("watchdog_enabled", False) if pi else False,
            "last_reboot_reason": pi.get("last_reboot_reason", "") if pi else "",
        })
    return result

@app.post("/api/super-admin/societies/save")
def save_society(data: dict):
    db = load_db()
    sid = data.get("id")
    society = {"name": data.get("name", ""), "location": data.get("location", ""), "plan": data.get("plan", "Basic"), "status": "active", "tailscale_ip": data.get("tailscale_ip", ""), "pi_port": int(data.get("pi_port", 5000)), "api_key": data.get("api_key", ""), "society_code": data.get("society_code", "")}
    if sid:
        for s in db["societies"]:
            if s["id"] == sid: s.update(society); break
    else:
        society["id"] = next_id(db["societies"])
        db["societies"].append(society)
    save_db(db)
    return {"message": "Saved"}

@app.post("/api/super-admin/societies/delete")
def delete_society(data: dict):
    db = load_db()
    sid = data.get("id")
    db["societies"] = [s for s in db["societies"] if s["id"] != sid]
    for key in ["pi_state", "pi_events", "pi_commands"]:
        db.get(key, {}).pop(int(sid), None)
    db["users"] = [u for u in db["users"] if u.get("society_id") != sid]
    save_db(db)
    return {"message": "Deleted"}

@app.get("/api/super-admin/users")
def get_users():
    db = load_db()
    users = []
    for u in db["users"]:
        soc = next((s for s in db["societies"] if s["id"] == u.get("society_id")), None)
        users.append({"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"], "society_name": soc["name"] if soc else "None", "society_id": u.get("society_id")})
    return users

@app.post("/api/super-admin/users/save")
def save_user(data: dict):
    db = load_db()
    uid = data.get("id")
    if uid:
        for u in db["users"]:
            if u["id"] == uid:
                if data.get("password"): u["password"] = bcrypt.hashpw(data["password"].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                for k in ["email", "name", "role", "society_id"]:
                    if k in data and k != "password": u[k] = data[k]
                break
    else:
        if not data.get("password"): raise HTTPException(400, "Password required")
        user = {"id": next_id(db["users"]), "email": data["email"], "name": data["name"], "role": data["role"], "society_id": data.get("society_id") or None, "password": bcrypt.hashpw(data["password"].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")}
        db["users"].append(user)
    save_db(db)
    return {"message": "Saved"}

@app.post("/api/super-admin/users/delete")
def delete_user(data: dict):
    db = load_db()
    db["users"] = [u for u in db["users"] if u["id"] != data.get("id")]
    save_db(db)
    return {"message": "Deleted"}

@app.get("/api/super-admin/firmware/versions")
def get_firmware_versions():
    db = load_db()
    versions = db.get("firmware_versions", [])
    for v in versions: v.pop("code", None)
    return versions

@app.post("/api/super-admin/firmware/save")
def save_firmware_version(data: dict):
    db = load_db()
    version = data.get("version", "").strip()
    code = data.get("code", "")
    changelog = data.get("changelog", "")
    forced = data.get("forced", False)
    if not version or not code: raise HTTPException(400, "Version and code required")
    if "firmware_versions" not in db: db["firmware_versions"] = []
    existing = next((v for v in db["firmware_versions"] if v["version"] == version), None)
    if existing:
        existing["code"] = code; existing["changelog"] = changelog; existing["forced"] = forced; existing["updated_at"] = datetime.now(timezone.utc).isoformat()
    else:
        db["firmware_versions"].insert(0, {"version": version, "code": code, "changelog": changelog, "forced": forced, "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()})
    if forced:
        for v in db["firmware_versions"]:
            if v["version"] != version: v["forced"] = False
    save_db(db)
    return {"message": "Saved"}

@app.post("/api/super-admin/firmware/delete")
def delete_firmware_version(data: dict):
    db = load_db()
    version = data.get("version")
    db["firmware_versions"] = [v for v in db.get("firmware_versions", []) if v["version"] != version]
    save_db(db)
    return {"message": "Deleted"}

@app.post("/api/super-admin/firmware/force")
def force_firmware(data: dict):
    db = load_db()
    version = data.get("version")
    if "firmware_versions" not in db: db["firmware_versions"] = []
    for v in db["firmware_versions"]:
        v["forced"] = (v["version"] == version)
    save_db(db)
    return {"message": "Force flag updated"}

@app.post("/api/pi/sync")
def pi_sync(payload: dict):
    print("=== PI RAW PAYLOAD ===", payload)
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
    if payload.get("key"): society["api_key"] = payload["key"]
    wings = {}
    for wid, w in payload.get("wings", {}).items():
        wings[wid] = {
            "name": w.get("name", wid),
            "display_name": w.get("display_name", ""),
            "used_days": w.get("usedDays", 0),
            "target_days": w.get("targetDays", 0),
            "clicks": w.get("clicks", 0),
            "disabled": w.get("disabled", False),
            "physical_toggle": w.get("physicalToggle", "UNKNOWN"),
        }
    pi_state = {
        "active_wing": payload.get("activeWing"),
        "wings": wings,
        "reset_day": payload.get("resetDay", 22),
        "emergency_stop": payload.get("emergencyStop", False),
        "firmware_version": payload.get("firmwareVersion", "?"),
        "uptime_seconds": payload.get("uptimeSeconds", 0),
        "cpu_temp": payload.get("cpuTemp", 0),
        "disk_free_mb": payload.get("diskFreeMB", 0),
        "last_sync": datetime.now(timezone.utc).isoformat(),
        "boot_count": payload.get("bootCount", 0),
        "last_shutdown_reason": payload.get("lastShutdownReason", ""),
        "clock_source": payload.get("clockSource", ""),
        "locked": payload.get("locked", False),
        "pending_start": payload.get("pendingStart", False),
        "quota_lock_until": payload.get("quota_lock_until", ""),
        "reset_day_lock_until": payload.get("reset_day_lock_until", ""),
        "watchdog_enabled": payload.get("watchdog_enabled", False),
        "last_reboot_reason": payload.get("last_reboot_reason", ""),
    }
    if "pi_state" not in db: db["pi_state"] = {}
    db["pi_state"][sid] = pi_state
    if "pi_events" not in db: db["pi_events"] = {}
    if sid not in db["pi_events"]: db["pi_events"][sid] = []
    for ev in payload.get("events", []): db["pi_events"][sid].append(ev)
    if len(db["pi_events"][sid]) > 500: db["pi_events"][sid] = db["pi_events"][sid][-500:]
    save_db(db)
    reply = {"success": True, "command": None}
    cmds = db.get("pi_commands", {})
    if sid in cmds and cmds[sid].get("command"):
        reply["command"] = cmds[sid]["command"]
        if cmds[sid].get("wing"): reply["wing"] = cmds[sid]["wing"]
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
        if not society: raise HTTPException(403, "Invalid API key")
    fv = next((v for v in db.get("firmware_versions", []) if v["version"] == version), None)
    if not fv: raise HTTPException(404, "Version not found")
    return PlainTextResponse(fv["code"], media_type="text/plain")

@app.post("/api/admin/pi-command")
def queue_command(data: dict):
    db = load_db()
    sid = int(data.get("society_id", 0))
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
        except: pass
    if not skip_locks and cmd == "set_reset_day" and pi.get("reset_day_lock_until", ""):
        try:
            lock_until = datetime.fromisoformat(pi["reset_day_lock_until"])
            if datetime.now(timezone.utc) < lock_until:
                raise HTTPException(400, f"Reset day locked until {pi['reset_day_lock_until']}")
        except: pass
    if "pi_commands" not in db: db["pi_commands"] = {}
    db["pi_commands"][sid] = {"command": cmd, "wing": wing, "params": params, "queued_at": datetime.now(timezone.utc).isoformat()}
    save_db(db)
    return {"success": True, "message": "Command queued", "command": cmd}

@app.get("/api/admin/pi-state")
def get_pi_state(society_id: str):
    db = load_db()
    state = db.get("pi_state", {}).get(int(society_id))
    if not state: return {"connected": False}
    filtered_wings = {wid: w for wid, w in state.get("wings", {}).items() if w.get("target_days", 0) > 0}
    state["wings"] = filtered_wings
    return {"connected": True, **state}

@app.get("/api/admin/pi-events")
def get_pi_events(society_id: str, since: int = 0):
    db = load_db()
    events = db.get("pi_events", {}).get(int(society_id), [])
    return {"events": events[since:], "total": len(events), "next": len(events)}

@app.get("/api/admin/dashboard")
def admin_dashboard(society_id: str = ""):
    db = load_db()
    if not society_id: return {"error": "society_id required"}
    pi = db.get("pi_state", {}).get(int(society_id))
    if not pi: return {"connected": False}
    wings_data = {}
    for wid, w in pi.get("wings", {}).items():
        if w.get("target_days", 0) == 0:
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
    return {
        "connected": True,
        "active_wing": pi.get("active_wing"),
        "reset_day": pi.get("reset_day", 22),
        "quota_lock_until": pi.get("quota_lock_until", ""),
        "reset_day_lock_until": pi.get("reset_day_lock_until", ""),
        "wings": wings_data,
        "emergency_stop": pi.get("emergency_stop", False),
        "watchdog_enabled": pi.get("watchdog_enabled", False),
        "last_reboot_reason": pi.get("last_reboot_reason", ""),
        "firmware_version": pi.get("firmware_version", "?"),
    }
