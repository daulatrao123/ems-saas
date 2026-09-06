"""Static release gate for EMS 6.4 targeted hardening.

This does not certify hardware, storage endurance, OTA, or production security.
It verifies the targeted source-level invariants that can be checked offline.
"""
from pathlib import Path
import ast
import hashlib
import subprocess

ROOT = Path(__file__).resolve().parents[1]

PROTECTED = [
    "pi_firmware/gpio_manager.py",
    "pi_firmware/storage_io_manager.py",
    "pi_firmware/storage_manager.py",
    "pi_firmware/logger.py",
    "pi_firmware/memory_manager.py",
    "pi_firmware/resource_guard.py",
]


def fail(msg):
    raise SystemExit(f"FAIL: {msg}")


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def main():
    backend = read("backend/main.py")
    controller = read("pi_firmware/ems_controller.py")
    queue = read("pi_firmware/offline_queue.py")
    api = read("pi_firmware/api_client.py")
    setup = read("pi_firmware/setup_pi.sh")
    service = read("pi_firmware/ems-controller.service")
    state = read("pi_firmware/state.py")

    required = [
        ("UserLogin declared before route", 'class UserLogin(BaseModel):'),
        ("backend command FSM", '"hardware_verified": {"completed", "failed"}'),
        ("backend ACKED state", '"acked": set()'),
        ("backend header auth", 'alias="X-Api-Key"'),
        ("backend delivery lease", "COMMAND_DELIVERY_LEASE_SECONDS = 120"),
        ("5-minute absolute expiry", "COMMAND_EXPIRY_SECONDS = 300"),
        ("retirement", "UPDATE pi_devices SET status='RETIRED'"),
        ("controller hardware verification", '"HARDWARE_VERIFIED"'),
        ("controller reboot ambiguity", '"UNKNOWN_AFTER_REBOOT"'),
        ("monthly reset marker", "last_reset_period"),
        ("physical ON visibility", 'physical == "ON"'),
        ("Pi API header", '"X-Device-ID"'),
        ("explicit device storage", "EMS_DATA_DEVICE"),
        ("no formatting", "NO FORMAT OPERATION WILL BE PERFORMED"),
        ("least privilege service", "User=pi"),
        ("gpio group", "SupplementaryGroups=gpio i2c"),
        ("legacy state compatibility", 'data.get("last_reset_period")'),
        ("durable pre-hardware EXECUTING", 'Cannot persist EXECUTING state; hardware command'),
        ("retired device assignment guard", 'Retired device cannot be reassigned'),
        ("firmware credential verification", 'Invalid or inactive Pi credentials'),
    ]
    for name, token in required:
        if token not in (backend + controller + queue + api + setup + service + state):
            fail(f"missing {name}: {token}")

    tests = read("frontend/test_system.py")
    if "admin123" in tests:
        fail("hardcoded test password remains")

    for path in [
        "backend/main.py", "frontend/test_system.py",
        "pi_firmware/api_client.py", "pi_firmware/ems_controller.py",
        "pi_firmware/offline_queue.py", "pi_firmware/setup_pi.sh", "pi_firmware/state.py",
    ]:
        if path.endswith(".py"):
            ast.parse(read(path), filename=path)

    if "DELETE FROM pi_devices" in backend or "DELETE FROM societies" in backend:
        fail("destructive device/society lifecycle SQL remains")

    if "payload[\"key\"]" in api or '"key": self.api_key' in api:
        fail("Pi API key is still duplicated into request JSON")

    assert backend.index("class UserLogin(BaseModel):") < backend.index('def login(request: Request, user: UserLogin):')
    assert '"delivered": {"executing", "expired", "unknown_after_reboot"}' in backend
    assert '"queued": {"delivered", "expired"}' in backend
    assert 'status = status.lower()' not in backend or 'COMMAND_TRANSITIONS' in backend

    print("PASS: EMS 6.4.1 targeted static source gate")
    print("PASS: UserLogin startup ordering, command FSM, ACK lifecycle, lease/expiry")
    print("PASS: legacy state compatibility, durable pre-hardware persistence, retirement guards")
    print("PASS: header-authenticated Pi firmware download and explicit storage/service hardening")
    print("NOTE: HIL, endurance, OTA signing/rollback, Alembic migrations, dependency audit and full frontend build remain release gates")


if __name__ == "__main__":
    main()
