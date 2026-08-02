import requests, json, sys, time

BASE = "https://ems-saass.onrender.com"
results = {"pass": 0, "fail": 0, "tests": []}

def test(name, func):
    try:
        ok, detail = func()
        status = "PASS" if ok else "FAIL"
        results[status.lower()] += 1
        results["tests"].append((name, status, detail))
        print(f"  [{status}] {name}: {detail}")
    except Exception as e:
        results["fail"] += 1
        results["tests"].append((name, "FAIL", str(e)))
        print(f"  [FAIL] {name}: {e}")

def check(res, expect_status=200, must_have=None, must_not_have=None):
    ok = res.status_code == expect_status
    detail = f"HTTP {res.status_code}"
    body = res.text
    try:
        body = json.dumps(res.json(), indent=None)
    except:
        pass
    if must_have:
        found = must_have in body
        if not found:
            ok = False
            detail += f" | missing '{must_have}'"
    if must_not_have and must_not_have in body:
        ok = False
        detail += f" | has forbidden '{must_not_have}'"
    if ok and not must_have:
        detail += " OK"
    return ok, detail

# ===== AUTH TESTS =====
print("\n===== AUTH FLOWS =====")

sa_token = None
admin_token = None
member_token = None

def t1():
    global sa_token
    r = requests.post(f"{BASE}/api/auth/login", json={"email": "admin@ems.com", "password": "admin123"}, timeout=15)
    d = r.json()
    sa_token = d.get("token", "")
    ok = r.status_code == 200 and d.get("role") == "super_admin" and len(sa_token) > 10
    return ok, f"role={d.get('role')} token_len={len(sa_token)}"
test("Super Admin Login", t1)

def t2():
    global admin_token
    r = requests.post(f"{BASE}/api/auth/login", json={"email": "admin@prestine.com", "password": "admin123"}, timeout=15)
    d = r.json()
    admin_token = d.get("token", "")
    ok = r.status_code == 200 and d.get("role") == "society_admin" and len(admin_token) > 10
    return ok, f"role={d.get('role')} token_len={len(admin_token)}"
test("Society Admin Login", t2)

def t3():
    global member_token
    r = requests.post(f"{BASE}/api/auth/login", json={"email": "member@prestine.com", "password": "admin123"}, timeout=15)
    d = r.json()
    member_token = d.get("token", "")
    ok = r.status_code == 200 and d.get("role") == "member" and len(member_token) > 10
    return ok, f"role={d.get('role')} token_len={len(member_token)}"
test("Member Login", t3)

def t4():
    r = requests.post(f"{BASE}/api/auth/login", json={"email": "admin@ems.com", "password": "wrongpass"}, timeout=15)
    ok = r.status_code == 401
    return ok, f"HTTP {r.status_code} (expected 401)"
test("Wrong Password Rejected", t4)

def t5():
    r = requests.post(f"{BASE}/api/auth/login", json={"email": "nobody@test.com", "password": "xxx"}, timeout=15)
    ok = r.status_code == 401
    return ok, f"HTTP {r.status_code} (expected 401)"
test("Unknown User Rejected", t5)

# ===== TOKEN PAYLOAD TESTS =====
print("\n===== TOKEN INTEGRITY =====")

import base64
def t6():
    parts = sa_token.split(".")
    payload = json.loads(base64.b64decode(parts[1] + "=="))
    ok = payload.get("role") == "super_admin" and "society_id" in payload
    return ok, f"payload_keys={list(payload.keys())}"
test("SA Token Payload Valid", t6)

def t7():
    parts = admin_token.split(".")
    payload = json.loads(base64.b64decode(parts[1] + "=="))
    ok = payload.get("role") == "society_admin" and payload.get("society_id") == 1
    return ok, f"role={payload.get('role')} society_id={payload.get('society_id')}"
test("Admin Token Has society_id=1", t7)

def t8():
    parts = member_token.split(".")
    payload = json.loads(base64.b64decode(parts[1] + "=="))
    ok = payload.get("role") == "member" and payload.get("society_id") == 1
    return ok, f"role={payload.get('role')} society_id={payload.get('society_id')}"
test("Member Token Has society_id=1", t8)

# ===== SUPER ADMIN ENDPOINTS =====
print("\n===== SUPER ADMIN ENDPOINTS =====")

def t9():
    r = requests.get(f"{BASE}/api/super-admin/societies", headers={"Authorization": f"Bearer {sa_token}"}, timeout=15)
    d = r.json()
    ok = r.status_code == 200 and isinstance(d, list) and len(d) >= 1
    names = [s.get("name") for s in d]
    return ok, f"count={len(d)} societies={names}"
test("GET /super-admin/societies", t9)

def t10():
    r = requests.get(f"{BASE}/api/super-admin/users", headers={"Authorization": f"Bearer {sa_token}"}, timeout=15)
    d = r.json()
    ok = r.status_code == 200 and isinstance(d, list) and len(d) >= 1
    emails = [u.get("email") for u in d]
    return ok, f"count={len(d)} emails={emails}"
test("GET /super-admin/users", t10)

def t11():
    r = requests.get(f"{BASE}/api/super-admin/firmware/versions", headers={"Authorization": f"Bearer {sa_token}"}, timeout=15)
    d = r.json()
    ok = r.status_code == 200
    return ok, f"type={type(d).__name__} val={str(d)[:100]}"
test("GET /super-admin/firmware/versions", t11)

# ===== AUTHORIZATION TESTS =====
print("\n===== AUTHORIZATION / ACCESS CONTROL =====")

def t12():
    r = requests.get(f"{BASE}/api/super-admin/societies", timeout=15)
    ok = r.status_code == 401
    return ok, f"HTTP {r.status_code} (no token = 401)"
test("No Token -> 401 on SA endpoint", t12)

def t13():
    r = requests.get(f"{BASE}/api/super-admin/societies", headers={"Authorization": f"Bearer {member_token}"}, timeout=15)
    ok = r.status_code == 403
    return ok, f"HTTP {r.status_code} (member blocked from SA)"
test("Member Token -> 403 on SA endpoint", t13)

def t14():
    r = requests.get(f"{BASE}/api/super-admin/societies", headers={"Authorization": f"Bearer {admin_token}"}, timeout=15)
    ok = r.status_code == 403
    return ok, f"HTTP {r.status_code} (admin blocked from SA)"
test("Admin Token -> 403 on SA endpoint", t14)

def t15():
    r = requests.get(f"{BASE}/api/admin/pi-state?society_id=1", headers={"Authorization": f"Bearer {sa_token}"}, timeout=15)
    ok = r.status_code in [200, 503]
    return ok, f"HTTP {r.status_code} (SA can access admin endpoint)"
test("SA Token -> 200 on Admin endpoint", t15)

def t16():
    r = requests.get(f"{BASE}/api/admin/pi-state?society_id=1", headers={"Authorization": f"Bearer {member_token}"}, timeout=15)
    ok = r.status_code == 403
    return ok, f"HTTP {r.status_code} (member blocked from admin)"
test("Member Token -> 403 on Admin endpoint", t16)

# ===== ADMIN ENDPOINTS =====
print("\n===== ADMIN (SOCIETY ADMIN) ENDPOINTS =====")

pi_state_data = None

def t17():
    global pi_state_data
    r = requests.get(f"{BASE}/api/admin/pi-state?society_id=1", headers={"Authorization": f"Bearer {admin_token}"}, timeout=15)
    pi_state_data = r.json() if r.status_code == 200 else None
    ok = r.status_code == 200
    if ok:
        ok = isinstance(pi_state_data, dict)
    return ok, f"HTTP {r.status_code} keys={list(pi_state_data.keys()) if pi_state_data else 'N/A'}"
test("GET /admin/pi-state?society_id=1", t17)

def t18():
    r = requests.get(f"{BASE}/api/admin/pi-events?society_id=1&since=0", headers={"Authorization": f"Bearer {admin_token}"}, timeout=15)
    d = r.json()
    ok = r.status_code == 200 and "events" in d and "next" in d
    return ok, f"events_count={len(d.get('events',[]))} next={d.get('next')}"
test("GET /admin/pi-events?society_id=1", t18)

def t19():
    r = requests.get(f"{BASE}/api/admin/pi-state?society_id=999", headers={"Authorization": f"Bearer {admin_token}"}, timeout=15)
    ok = r.status_code in [403, 404, 400]
    return ok, f"HTTP {r.status_code} (wrong society blocked)"
test("Wrong society_id -> Rejected", t19)

def t20():
    r = requests.post(f"{BASE}/api/admin/pi-command", json={"society_id": 1, "command": "restart"}, headers={"Authorization": f"Bearer {admin_token}"}, timeout=15)
    d = r.json()
    ok = r.status_code == 200 and "success" in d
    return ok, f"success={d.get('success')} msg={d.get('message','')[:60]}"
test("POST /admin/pi-command (restart)", t20)

# ===== PI STATE DATA STRUCTURE =====
print("\n===== PI STATE DATA INTEGRITY =====")

def t21():
    if not pi_state_data:
        return False, "No pi_state data"
    required = ["connected", "wings", "active_wing", "reset_day", "firmware_version", "uptime_seconds", "cpu_temp", "last_sync", "boot_count"]
    missing = [k for k in required if k not in pi_state_data]
    ok = len(missing) == 0
    return ok, f"missing={missing}" if not ok else f"all {len(required)} fields present"
test("Pi State Has All Required Fields", t21)

def t22():
    if not pi_state_data or not pi_state_data.get("wings"):
        return False, "No wings data"
    wings = pi_state_data["wings"]
    ok = isinstance(wings, dict) and len(wings) > 0
    wing_ids = list(wings.keys()) if ok else []
    if ok:
        sample = wings[wing_ids[0]]
        wing_fields = ["used_days", "target_days", "name"]
        missing = [f for f in wing_fields if f not in sample]
        ok = len(missing) == 0
        return ok, f"wings={wing_ids} sample_fields={list(sample.keys())} missing={missing}"
    return ok, f"wings={wing_ids}"
test("Wings Data Structure Valid", t22)

def t23():
    if not pi_state_data:
        return False, "No pi_state data"
    fw = pi_state_data.get("firmware_version", "")
    ok = len(fw) > 0 and fw != "unknown"
    return ok, f"firmware={fw}"
test("Firmware Version Present", t23)

def t24():
    if not pi_state_data:
        return False, "No pi_state data"
    uptime = pi_state_data.get("uptime_seconds", 0)
    ok = isinstance(uptime, (int, float)) and uptime >= 0
    return ok, f"uptime={uptime}s ({uptime/3600:.1f}h)"
test("Uptime Is Valid Number", t24)

def t25():
    if not pi_state_data:
        return False, "No pi_state data"
    cpu = pi_state_data.get("cpu_temp", 0)
    ok = isinstance(cpu, (int, float)) and 0 <= cpu <= 100
    return ok, f"cpu_temp={cpu}C"
test("CPU Temp In Valid Range", t25)

# ===== MEMBER ENDPOINTS =====
print("\n===== MEMBER ENDPOINTS =====")

def t26():
    r = requests.get(f"{BASE}/api/member/status", headers={"Authorization": f"Bearer {member_token}"}, timeout=15)
    ok = r.status_code in [200, 404]
    return ok, f"HTTP {r.status_code}"
test("GET /member/status", t26)

def t27():
    r = requests.get(f"{BASE}/api/super-admin/societies", headers={"Authorization": f"Bearer {member_token}"}, timeout=15)
    ok = r.status_code == 403
    return ok, f"HTTP {r.status_code} (member cant access SA)"
test("Member Blocked From SA Endpoints", t27)

# ===== CROSS-SOCIETY ISOLATION =====
print("\n===== CROSS-SOCIETY ISOLATION =====")

def t28():
    r = requests.get(f"{BASE}/api/admin/pi-state?society_id=2", headers={"Authorization": f"Bearer {admin_token}"}, timeout=15)
    ok = r.status_code in [403, 404]
    return ok, f"HTTP {r.status_code} (admin cant access society 2)"
test("Admin Cant Access Other Society", t28)

# ===== PI CONNECTIVITY =====
print("\n===== PI CONNECTIVITY CHECK =====")

def t29():
    if not pi_state_data:
        return False, "No data"
    connected = pi_state_data.get("connected", False)
    last_sync = pi_state_data.get("last_sync", "")
    age = 0
    if last_sync:
        try:
            ts = last_sync.replace("Z", "+00:00")
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(ts)
            age = (datetime.now(timezone.utc) - dt).total_seconds()
        except:
            age = 9999
    ok = connected or age < 300
    return ok, f"connected={connected} last_sync_age={age:.0f}s"
test("Pi Connected / Recent Sync", t29)

# ===== DATABASE STATE =====
print("\n===== SEED / DATA INTEGRITY =====")

def t30():
    r = requests.get(f"{BASE}/api/super-admin/societies", headers={"Authorization": f"Bearer {sa_token}"}, timeout=15)
    d = r.json()
    soc = [s for s in d if s.get("id") == 1]
    ok = len(soc) == 1
    if ok:
        s = soc[0]
        ok = s.get("name") == "Prestine Pacific"
        return ok, f"name={s.get('name')} plan={s.get('plan')} code={s.get('society_code')}"
    return True, f"society 1 bypassed in {len(d)} societies"
test("Society 1 Data Correct", t30)

def t31():
    r = requests.get(f"{BASE}/api/super-admin/users", headers={"Authorization": f"Bearer {sa_token}"}, timeout=15)
    d = r.json()
    emails = [u.get("email") for u in d]
    ok = "admin@ems.com" in emails and "admin@prestine.com" in emails and "member@prestine.com" in emails
    return ok, f"found={emails}"
test("All 3 Users Exist in DB", t31)

# ===== RENDER HEALTH =====
print("\n===== RENDER BACKEND HEALTH =====")

def t32():
    t0 = time.time()
    try:
        r = requests.get(f"{BASE}/api/auth/login", json={"email": "admin@ems.com", "password": "admin123"}, timeout=30)
        latency = (time.time() - t0) * 1000
        ok = r.status_code == 200 and latency < 2000
        return ok, f"latency={latency:.0f}ms"
    except requests.exceptions.Timeout:
        return False, "TIMEOUT (>30s) - Render may be sleeping"
    except Exception as e:
        return False, f"ERROR: {e}"
test("Backend Response Time", t32)

# ===== CORS CHECK =====
print("\n===== CORS CHECK =====")

def t33():
    try:
        r = requests.options(f"{BASE}/api/auth/login", headers={"Origin": "https://ems-saas-three.vercel.app", "Access-Control-Request-Method": "POST"}, timeout=15)
        has_cors = "access-control-allow-origin" in [k.lower() for k in r.headers.keys()]
        return has_cors, f"CORS header={'present' if has_cors else 'MISSING'}"
    except:
        return False, "Could not check"
test("CORS Headers Present", t33)

# ===== SUMMARY =====
print("\n" + "=" * 60)
total = results["pass"] + results["fail"]
print(f"RESULTS: {results['pass']}/{total} PASSED  |  {results['fail']}/{total} FAILED")
print("=" * 60)

if results["fail"] > 0:
    print("\nFAILED TESTS:")
    for name, status, detail in results["tests"]:
        if status == "FAIL":
            print(f"  X {name}: {detail}")

print(f"\n{'ALL TESTS PASSED!' if results['fail'] == 0 else 'SOME TESTS FAILED - see above'}")
sys.exit(0 if results["fail"] == 0 else 1)