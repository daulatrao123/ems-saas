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
    changed = subprocess.check_output(
        ["git", "diff", "--name-only"], cwd=ROOT, text=True
    ).splitlines()
    allowed = {
        "backend/main.py",
        "frontend/test_system.py",
        "pi_firmware/api_client.py",
        "pi_firmware/ems_controller.py",
        "pi_firmware/offline_queue.py",
        "pi_firmware/setup_pi.sh",
        "pi_firmware/state.py",
        "pi_firmware/ems-controller.service",
    }
    if any(p not in allowed for p in changed):
        fail(f"unexpected modified files: {[p for p in changed if p not in allowed]}")

    for path in PROTECTED:
        if subprocess.call(["git", "diff", "--quiet", "--", path], cwd=ROOT) != 0:
            fail(f"protected subsystem changed: {path}")

    backend = read("backend/main.py")
    controller = read("pi_firmware/ems_controller.py")
    queue = read("pi_firmware/offline_queue.py")
    api = read("pi_firmware/api_client.py")
    setup = read("pi_firmware/setup_pi.sh")
    service = read("pi_firmware/ems-controller.service")
    state = read("pi_firmware/state.py")

    required = [
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
    ]
    for name, token in required:
        if token not in (backend + controller + queue + api + setup + service + state):
            fail(f"missing {name}: {token}")

    # No plaintext test passwords may be shipped.
    tests = read("frontend/test_system.py")
    if "admin123" in tests:
        fail("hardcoded test password remains")

    for path in [
        "backend/main.py", "frontend/test_system.py",
        "pi_firmware/api_client.py", "pi_firmware/ems_controller.py",
        "pi_firmware/offline_queue.py", "pi_firmware/setup_pi.sh", "pi_firmware/state.py",
    ]:
        ast.parse(read(path), filename=path) if path.endswith(".py") else None

    if "DELETE FROM pi_devices" in backend or "DELETE FROM societies" in backend:
        fail("destructive lifecycle SQL remains")

    if "payload[\"key\"]" in api or '"key": self.api_key' in api:
        fail("Pi API key is still duplicated into request JSON")

    print("PASS: strict 6.4 targeted source gate")
    print("PASS: protected GPIO/storage/resource subsystems unchanged")
    print("PASS: command lifecycle, reboot recovery, reset persistence, header auth")
    print("PASS: explicit storage device and least-privilege service")
    print("NOTE: hardware HIL, endurance, OTA, dependency and deployment tests remain mandatory")


if __name__ == "__main__":
    main()
