"""Offline static release gate for EMS 6.4.1 strict hardening.

This gate deliberately does not certify physical HIL, flash endurance, OTA,
or browser security. Those require target-system qualification.
"""
from pathlib import Path
import ast
import hashlib
import re

ROOT = Path(__file__).resolve().parents[1]
PROTECTED_HASHES = {
    "pi_firmware/gpio_manager.py": "b396b0d074b20f7fc7363bbbca0329c0471d9a54afbd598351f70431893c2dc8",
    "pi_firmware/storage_io_manager.py": "f41617296de4fda888fedb952f953d7db066ae57a9ee3bd75db9119782644fc5",
    "pi_firmware/storage_manager.py": "ce6be1a3442abcff93322d78c03e63c00f640c17004de7638718d710296999a1",
    "pi_firmware/logger.py": "8e1ccc99f4944d87749ee99cc4e09bf41b8be454fa0e2b3d0ff037534e2bdec6",
    "pi_firmware/memory_manager.py": "c353672f37ac706cb3f961a87d19c48895892fafefead5e933183d29719ce5eb",
    "pi_firmware/resource_guard.py": "c4d6f6278b9ccf58abf658e491651ab7c2cd8734a837d8656cfcc76d45a50449",
}


def fail(msg):
    raise SystemExit(f"FAIL: {msg}")


def text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def main():
    backend = text("backend/main.py")
    controller = text("pi_firmware/ems_controller.py")
    queue = text("pi_firmware/offline_queue.py")
    api = text("pi_firmware/api_client.py")
    setup = text("pi_firmware/setup_pi.sh")
    service = text("pi_firmware/ems-controller.service")
    state = text("pi_firmware/state.py")
    alembic_env = text("backend/alembic/env.py")
    migration = text("backend/alembic/versions/0001_industrial_schema.py")
    render = text("backend/render.yaml")

    # The runtime must contain no schema DDL.
    if re.search(r"\b(CREATE TABLE|ALTER TABLE|DROP INDEX|DROP TABLE|CREATE INDEX)\b|DO \$\$", backend, re.I):
        fail("runtime backend/main.py still contains DDL")
    if "@app.on_event(\"startup\")" in backend or "ensure_db_schema" in backend:
        fail("database schema mutation is still tied to FastAPI startup")

    required = [
        ("strict queued FSM", '"queued": {"delivered", "expired"}'),
        ("hardware verification transition", '"hardware_verified": {"completed", "failed"}'),
        ("ACKED terminal state", '"acked": set()'),
        ("delivery lease", "COMMAND_DELIVERY_LEASE_SECONDS = 120"),
        ("absolute expiry", "COMMAND_EXPIRY_SECONDS = 300"),
        ("header Pi auth", 'alias="X-Api-Key"'),
        ("retirement", "UPDATE pi_devices SET status='RETIRED'"),
        ("reboot ambiguity", '"UNKNOWN_AFTER_REBOOT"'),
        ("durable usage marker", "last_reset_period"),
        ("physical ON visibility", 'physical == "ON"'),
        ("Pi API header", '"X-Device-ID"'),
        ("explicit storage device", "EMS_DATA_DEVICE"),
        ("no format", "NO FORMAT OPERATION WILL BE PERFORMED"),
        ("least privilege", "User=pi"),
        ("GPIO/I2C groups", "SupplementaryGroups=gpio i2c"),
        ("legacy state compatibility", 'data.get("last_reset_period")'),
        ("pre-hardware durable state check", "hardware execution blocked"),
    ]
    combined = backend + controller + queue + api + setup + service + state
    for name, token in required:
        if token not in combined:
            fail(f"missing {name}: {token}")

    if '"key": self.api_key' in api or 'payload["key"]' in api:
        fail("Pi API key remains in request JSON")
    if "DELETE FROM pi_devices" in backend or "DELETE FROM societies" in backend:
        fail("destructive lifecycle SQL remains")

    # Alembic must be present, configured from DATABASE_URL, and invoked before app start.
    if "alembic" not in text("backend/requirements.txt"):
        fail("Alembic dependency missing")
    if "DATABASE_URL" not in alembic_env or "create_engine" not in alembic_env:
        fail("Alembic environment is not configured for DATABASE_URL")
    if "revision = \"0001_industrial_schema\"" not in migration:
        fail("industrial schema revision missing")
    if "alembic upgrade head && uvicorn" not in render:
        fail("deployment does not run migrations before FastAPI")

    # Protected low-level subsystems are hash-locked in this candidate.
    for rel, expected in PROTECTED_HASHES.items():
        actual = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
        if actual != expected:
            fail(f"protected subsystem changed: {rel}")

    # Python syntax gate.
    for rel in [
        "backend/main.py", "backend/alembic/env.py",
        "backend/alembic/versions/0001_industrial_schema.py",
        "frontend/test_system.py", "pi_firmware/api_client.py",
        "pi_firmware/ems_controller.py", "pi_firmware/offline_queue.py",
        "pi_firmware/state.py",
    ]:
        ast.parse(text(rel), filename=rel)

    tests = text("frontend/test_system.py")
    if "admin123" in tests or "password123" in tests:
        fail("hardcoded test password remains")

    print("PASS: no runtime DDL / Alembic migration architecture")
    print("PASS: strict command FSM, lease, expiry and ACK contract")
    print("PASS: reboot recovery, state compatibility and pre-hardware persistence gate")
    print("PASS: storage device safety and least-privilege service")
    print("PASS: protected GPIO/storage/resource subsystem hashes")
    print("PASS: Python syntax and credential hygiene")
    print("NOTE: HIL, endurance, OTA and browser-cookie security remain qualification gates")


if __name__ == "__main__":
    main()
