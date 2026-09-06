"""Offline release gate for EMS 6.4.1 strict hardening."""
from pathlib import Path
import ast
import re

ROOT = Path(__file__).resolve().parents[1]
PROTECTED = [
    "pi_firmware/gpio_manager.py", "pi_firmware/storage_io_manager.py",
    "pi_firmware/storage_manager.py", "pi_firmware/logger.py",
    "pi_firmware/memory_manager.py", "pi_firmware/resource_guard.py",
]

def fail(msg): raise SystemExit(f"FAIL: {msg}")
def read(path): return (ROOT / path).read_text(encoding="utf-8")

def main():
    backend=read("backend/main.py"); controller=read("pi_firmware/ems_controller.py")
    api=read("pi_firmware/api_client.py"); setup=read("pi_firmware/setup_pi.sh")
    service=read("pi_firmware/ems-controller.service"); state=read("pi_firmware/state.py")
    migration=read("backend/alembic/versions/20260906_0001_industrial_baseline.py")
    render=read("backend/render.yaml"); req=read("backend/requirements.txt")
    for path in PROTECTED:
        if not (ROOT/path).is_file(): fail(f"missing protected file: {path}")
    if re.search(r"CREATE TABLE|ALTER TABLE|DROP INDEX|DO \$\$", backend, re.I): fail("application backend still contains schema DDL")
    if "alembic upgrade head" not in render: fail("Render does not run Alembic before the app")
    if "alembic==1.13.3" not in req: fail("Alembic dependency missing")
    if "ALTER COLUMN reset_day SET DEFAULT {DEFAULT_RESET_DAY}" not in migration: fail("safe reset-day DDL missing from migration")
    checks=[
        ('"queued": {"delivered", "expired"}', "queued -> executing must be forbidden"),
        ('"hardware_verified": {"completed", "failed"}', "strict hardware_verified transition missing"),
        ('"acked": set()', "terminal ACKED state missing"),
        ("COMMAND_DELIVERY_LEASE_SECONDS = 120", "delivery lease missing"),
        ("COMMAND_EXPIRY_SECONDS = 300", "absolute expiry missing"),
        ("HARDWARE_VERIFIED requires explicit hardware verification", "verification gate missing"),
        ('alias="X-Api-Key"', "Pi header auth missing"),
        ("EMS_DATA_DEVICE", "explicit storage device missing"),
        ("NO FORMAT OPERATION WILL BE PERFORMED", "non-destructive setup guard missing"),
        ("User=pi", "least-privilege service user missing"),
        ("Group=pi", "least-privilege service group missing"),
        ("SupplementaryGroups=gpio i2c", "GPIO/I2C access groups missing"),
        ('"UNKNOWN_AFTER_REBOOT"', "reboot ambiguity state missing"),
        ("last_reset_period", "reset marker missing"),
        ('physical == "ON"', "physical ON visibility gate missing"),
        ('"X-Device-ID"', "Pi device header missing"),
        ('"X-Api-Key"', "Pi API key header missing"),
        ('UPDATE pi_devices SET status=\'RETIRED\'', "retirement lifecycle missing"),
        ('data.get("last_reset_period")', "legacy state compatibility missing"),
    ]
    blob=backend+controller+api+setup+service+state
    for token,msg in checks:
        if token not in (migration+blob if token.startswith("ALTER") else blob): fail(msg)
    if '"key": self.api_key' in api or 'payload["key"]' in api: fail("Pi API key still sent in JSON")
    if "DELETE FROM pi_devices" in backend or "DELETE FROM societies" in backend: fail("destructive lifecycle SQL remains")
    for path in list((ROOT/"backend").rglob("*.py"))+list((ROOT/"pi_firmware").rglob("*.py"))+[ROOT/"frontend/test_system.py"]:
        try: ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except Exception as exc: fail(f"syntax error in {path}: {exc}")
    print("PASS: EMS 6.4.1 strict source gate")
    print("PASS: startup DDL removed; Alembic migration/deploy chain present")
    print("PASS: strict command FSM, 120s lease, 300s expiry, verification gate")
    print("PASS: Pi recovery/state compatibility, storage provisioning, least privilege")
    print("NOTE: HIL, 7-30 day endurance, live migration rehearsal, OTA/A-B and cookie-auth qualification remain release gates")

if __name__ == "__main__": main()
