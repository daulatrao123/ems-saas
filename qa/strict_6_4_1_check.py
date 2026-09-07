"""Standalone offline release gate for EMS 6.4.1.

This is a source/invariant gate only. It does not certify real GPIO,
contactor feedback, power-fail behavior, flash endurance, OTA security,
or production deployment.
"""
from pathlib import Path
import hashlib

ROOT = Path(__file__).resolve().parents[1]
PROTECTED = {
    "pi_firmware/gpio_manager.py": "b396b0d074b20f7fc7363bbbca0329c0471d9a54afbd598351f70431893c2dc8",
    "pi_firmware/storage_io_manager.py": "f41617296de4fda888fedb952f953d7db066ae57a9ee3bd75db9119782644fc5",
    # Phase 0.5 re-baseline (June 2026): storage bands 70/80/90/95 + STORAGE_FAILED,
    # monitor_once(), transition events, free_mb/storage_state status, cleanup at >=80%.
    "pi_firmware/storage_manager.py": "73319f7ddca2fdf4aa10df52279f134566873ba047df4c57a2b5f4339abe9b91",
    "pi_firmware/logger.py": "8e1ccc99f4944d87749ee99cc4e09bf41b8be454fa0e2b3d0ff037534e2bdec6",
    "pi_firmware/memory_manager.py": "c353672f37ac706cb3f961a87d19c48895892fafefead5e933183d29719ce5eb",
    # Phase 0.5 re-baseline (June 2026): NORMAL/WARNING/CLEANUP_ELIGIBLE/CRITICAL/
    # STORAGE_PROTECTION/STORAGE_FAILED classification; write policy per band;
    # critical_log/state/queue_db still always allowed.
    "pi_firmware/resource_guard.py": "bd5efcdd44a5a12e5afaa256505402217d2101fa1bb68bfaad763abf08920352",
}

def text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")

def check(name, condition):
    if not condition:
        raise SystemExit(f"FAIL: {name}")
    print(f"PASS: {name}")

backend=text("backend/main.py")
state=text("pi_firmware/state.py")
queue=text("pi_firmware/offline_queue.py")
ctrl=text("pi_firmware/ems_controller.py")
api=text("pi_firmware/api_client.py")
setup=text("pi_firmware/setup_pi.sh")
unit=text("pi_firmware/ems-controller.service")
login=text("frontend/src/app/login/page.tsx")
admin=text("frontend/src/app/admin/page.tsx")
member=text("frontend/src/app/member/page.tsx")
superadmin=text("frontend/src/app/super-admin/page.tsx")
tests=text("frontend/test_system.py")

check("state format remains version 4", "STATE_VERSION = 4" in text("pi_firmware/config.py"))
check("legacy usage fields default safely", 'data.get("used_days", 0)' in state and 'data.get("clicks", 0)' in state)
check("legacy reset fields default safely", 'data.get("last_reset_period")' in state)
claim=queue[queue.index("def claim_next"):queue.index("def get_interrupted")]
check("UNKNOWN_AFTER_REBOOT is excluded from normal claim", "UNKNOWN_AFTER_REBOOT" not in claim and "status='DELIVERED'" in claim)
check("reboot recovery tracks all required statuses", all(x in queue for x in ("EXECUTING", "HARDWARE_VERIFIED", "UNKNOWN_AFTER_REBOOT")))
check("backend queued transition is strict", '"queued": {"delivered", "expired"}' in backend)
check("120-second delivery lease", "COMMAND_DELIVERY_LEASE_SECONDS = 120" in backend)
check("300-second absolute expiry", "COMMAND_EXPIRY_SECONDS = 300" in backend)
check("positive HARDWARE_VERIFIED verification required", "HARDWARE_VERIFIED requires positive hardware verification" in backend)
check("Pi credentials use headers", '"X-Device-ID"' in api and '"X-API-Key"' in api)
check("Pi API key is not duplicated into JSON", 'payload["key"]' not in api)
check("device retirement is non-destructive", "DELETE FROM pi_devices" not in backend and "status='RETIRED'" in backend)
check("retired devices cannot be reassigned", "Retired device cannot be reassigned" in backend)
check("firmware download validates device credentials", "authenticate_pi({}, x_device_id, x_api_key)" in backend)
check("feedback capability is enforced in dashboard", 'bool(c["feedback_enabled"]) and bool(dev["feedback_hardware_installed"])' in backend)
check("storage device is explicit", "EMS_DATA_DEVICE" in setup)
check("setup never formats the data device", "NO FORMAT OPERATION WILL BE PERFORMED" in setup)
check("data mount permissions are root:pi 0770", "chown root:pi" in setup and "chmod 0770" in setup)
check("systemd service runs as pi", "User=pi" in unit and "Group=pi" in unit)
check("systemd requires EMS data mount", "RequiresMountsFor=/mnt/ems-data" in unit)
check("member UI is read-only", "Read-only access" in member and "/api/admin/pi-command" not in member)
check("login routes roles separately", 'router.push("/member")' in login and 'router.push("/admin")' in login)
check("admin page enforces society_admin", 'role !== "society_admin"' in admin)
check("super-admin page enforces super_admin", 'role !== "super_admin"' in superadmin)
check("command API rejects member role", 'Only super_admin or society_admin may issue commands' in backend)
check("integration test uses environment credentials", "TEST_USERS" in tests and "admin123" not in tests)
check("obsolete storage_health is absent", not (ROOT/"pi_firmware/storage_health.py").exists())
for rel, expected in PROTECTED.items():
    actual=hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()
    check(f"protected subsystem unchanged: {rel}", actual == expected)
print("\nSTRICT 6.4.1 SOURCE GATE: PASS")
print("NOTE: HIL, endurance, OTA, migrations, dependency and production-auth qualification remain mandatory.")
