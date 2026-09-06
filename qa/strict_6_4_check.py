"""Static release gate for EMS SaaS 6.4.1 targeted hardening.

This gate checks source-level invariants that can be verified offline. It does
not certify target hardware, storage endurance, OTA, or production security.
"""
from pathlib import Path
import ast
import re

ROOT = Path(__file__).resolve().parents[1]


def fail(msg: str):
    raise SystemExit(f"FAIL: {msg}")


def read(path: str) -> str:
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
        ("fixed DDL default", 'ALTER COLUMN reset_day SET DEFAULT {DEFAULT_RESET_DAY}'),
        ("strict queued transition", '"queued": {"delivered", "expired"}'),
        ("delivery lease", "COMMAND_DELIVERY_LEASE_SECONDS = 120"),
        ("absolute expiry", "COMMAND_EXPIRY_SECONDS = 300"),
        ("header auth", 'alias="X-Api-Key"'),
        ("retirement", "UPDATE pi_devices SET status='RETIRED'"),
        ("reboot ambiguity", '"UNKNOWN_AFTER_REBOOT"'),
        ("hardware verification", '"HARDWARE_VERIFIED"'),
        ("monthly reset marker", "last_reset_period"),
        ("legacy state defaults", 'data.get("used_days", 0)'),
        ("physical visibility", 'physical == "ON"'),
        ("explicit storage", "EMS_DATA_DEVICE"),
        ("no formatting", "NO FORMAT OPERATION WILL BE PERFORMED"),
        ("least privilege", "User=pi"),
        ("gpio group", "SupplementaryGroups=gpio i2c"),
        ("persist before hardware", "Cannot persist EXECUTING state"),
    ]
    combined = backend + controller + queue + api + setup + service + state
    for name, token in required:
        if token not in combined:
            fail(f"missing {name}: {token}")

    if '"queued": {"delivered", "executing", "expired"}' in backend:
        fail("direct queued->executing transition remains")

    if "ALTER TABLE societies ALTER COLUMN reset_day SET DEFAULT %s" in backend:
        fail("parameterized PostgreSQL DDL remains")

    if '"key": self.api_key' in api or 'payload["key"]' in api:
        fail("Pi API key still duplicated into request JSON")

    if "DELETE FROM pi_devices" in backend or "DELETE FROM societies" in backend:
        fail("destructive lifecycle SQL remains")

    tests = read("frontend/test_system.py")
    if re.search(r"admin123|password\s*=\s*['\"]", tests, flags=re.I):
        fail("hardcoded test credential pattern remains")

    for path in [
        "backend/main.py", "frontend/test_system.py", "pi_firmware/api_client.py",
        "pi_firmware/ems_controller.py", "pi_firmware/offline_queue.py", "pi_firmware/state.py",
    ]:
        ast.parse(read(path), filename=path)

    print("PASS: EMS 6.4.1 strict source gate")
    print("PASS: PostgreSQL startup DDL parameter bug fixed")
    print("PASS: strict command FSM and ACK lifecycle")
    print("PASS: state/queue compatibility invariants")
    print("PASS: explicit storage + least-privilege service invariants")
    print("NOTE: HIL, endurance, Alembic, OTA, web-session security, dependency and deployment gates remain mandatory")


if __name__ == "__main__":
    main()
