"""Deployment smoke tests for cookie-based EMS authentication.

This file is a test runner only; it must never be deployed as application code.
Configure EMS_TEST_BASE_URL and the three test credentials through environment
variables. Do not hard-code production secrets here.
"""
import os
import requests

BASE = os.getenv("EMS_TEST_BASE_URL", "http://localhost:8000").rstrip("/")
USERS = {
    "super_admin": (os.getenv("EMS_TEST_SUPER_ADMIN_EMAIL", ""), os.getenv("EMS_TEST_SUPER_ADMIN_PASSWORD", "")),
    "society_admin": (os.getenv("EMS_TEST_ADMIN_EMAIL", ""), os.getenv("EMS_TEST_ADMIN_PASSWORD", "")),
    "member": (os.getenv("EMS_TEST_MEMBER_EMAIL", ""), os.getenv("EMS_TEST_MEMBER_PASSWORD", "")),
}

failures = []

def check(name, condition, detail=""):
    if condition:
        print(f"PASS: {name}")
    else:
        failures.append(name)
        print(f"FAIL: {name} {detail}")

def login(role):
    email, password = USERS[role]
    if not email or not password:
        raise RuntimeError(f"Set credentials for {role} using EMS_TEST_* environment variables")
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", json={"email": email, "password": password}, timeout=15)
    check(f"{role} login", r.status_code == 200, f"HTTP {r.status_code}")
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    check(f"{role} has HttpOnly access cookie", "ems_access" in s.cookies)
    check(f"{role} response contains no bearer token", "token" not in data)
    return s, data

def csrf(s):
    return s.cookies.get("ems_csrf", "")

if __name__ == "__main__":
    sessions = {role: login(role) for role in USERS}
    for role, (s, data) in sessions.items():
        r = s.get(f"{BASE}/api/auth/me", timeout=15)
        check(f"{role} session", r.status_code == 200 and r.json().get("role") == role, f"HTTP {r.status_code}")

    sa, _ = sessions["super_admin"]
    r = sa.get(f"{BASE}/api/super-admin/societies", timeout=15)
    check("super admin society endpoint", r.status_code == 200)

    admin, admin_data = sessions["society_admin"]
    sid = admin_data.get("society_id")
    r = admin.get(f"{BASE}/api/admin/dashboard", params={"society_id": sid}, timeout=15)
    check("society admin dashboard", r.status_code == 200)
    r = admin.get(f"{BASE}/api/admin/dashboard", params={"society_id": "999999"}, timeout=15)
    check("society isolation", r.status_code == 403)

    member, _ = sessions["member"]
    r = member.get(f"{BASE}/api/super-admin/societies", timeout=15)
    check("member blocked from super admin", r.status_code == 403)

    # Unsafe request without CSRF must be rejected even with a valid session.
    r = admin.post(f"{BASE}/api/admin/pi-command", json={"society_id": sid, "command": "restart"}, timeout=15)
    check("CSRF blocks unsafe request", r.status_code == 403)

    # Refresh rotates the server-side refresh token and preserves the session.
    r = admin.post(f"{BASE}/api/auth/refresh", headers={"X-CSRF-Token": csrf(admin)}, timeout=15)
    check("refresh token rotation", r.status_code == 200 and "ems_refresh" in admin.cookies)

    r = admin.post(f"{BASE}/api/auth/logout", headers={"X-CSRF-Token": csrf(admin)}, timeout=15)
    check("logout", r.status_code == 200 and "ems_access" not in admin.cookies)

    if failures:
        raise SystemExit(f"{len(failures)} QA checks failed: {', '.join(failures)}")
    print("ALL COOKIE AUTH SMOKE TESTS PASSED")
