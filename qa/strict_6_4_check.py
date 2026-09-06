"""Self-contained offline release gate for EMS 6.4.1."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]

def text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")

def fail(msg):
    raise SystemExit("FAIL: " + msg)

def main():
    backend=text("backend/main.py")
    controller=text("pi_firmware/ems_controller.py")
    queue=text("pi_firmware/offline_queue.py")
    state=text("pi_firmware/state.py")
    api=text("pi_firmware/api_client.py")
    setup=text("pi_firmware/setup_pi.sh")
    service=text("pi_firmware/ems-controller.service")
    tests=text("frontend/test_system.py")

    required=[
      ("strict queued FSM", '"queued": {"delivered", "expired"}'),
      ("delivery lease", "COMMAND_DELIVERY_LEASE_SECONDS = 120"),
      ("absolute expiry", "COMMAND_EXPIRY_SECONDS = 300"),
      ("HARDWARE_VERIFIED FSM", '"hardware_verified": {"completed", "failed"}'),
      ("ACKED terminal state", '"acked": set()'),
      ("verification evidence", '"VERIFIED_ON", "VERIFIED_OFF", "GPIO_CONFIRMED"'),
      ("reboot ambiguity", '"UNKNOWN_AFTER_REBOOT"'),
      ("legacy state fields", 'data.get("used_days", 0)'),
      ("reset period", 'last_reset_period'),
      ("physical ON visibility", 'physical == "ON"'),
      ("Pi device header", '"X-Device-ID"'),
      ("Pi API header", '"X-API-Key"'),
      ("storage device", "EMS_DATA_DEVICE"),
      ("no format", "NO FORMAT OPERATION WILL BE PERFORMED"),
      ("least privilege", "User=pi"),
      ("GPIO/I2C groups", "SupplementaryGroups=gpio i2c"),
      ("queue migration", "PRAGMA table_info(commands)"),
    ]
    allsrc=backend+controller+queue+state+api+setup+service
    for name, token in required:
        if token not in allsrc: fail(name)
    if "DELETE FROM pi_devices" in backend or "DELETE FROM societies" in backend: fail("destructive lifecycle SQL")
    if '"key": self.api_key' in api or 'payload["key"]' in api: fail("API key in JSON")
    if "admin123" in tests: fail("hardcoded test password")
    for rel in ["backend/main.py","frontend/test_system.py","pi_firmware/api_client.py","pi_firmware/ems_controller.py","pi_firmware/offline_queue.py","pi_firmware/state.py"]:
        ast.parse(text(rel), filename=rel)
    if "storage_health.py" in {p.name for p in (ROOT/"pi_firmware").iterdir()}: fail("obsolete storage_health.py remains")
    print("PASS: EMS 6.4.1 strict offline gate")
    print("PASS: FSM, delivery lease/expiry, reboot recovery, legacy state/queue migration")
    print("PASS: header auth, storage provisioning, least privilege, lifecycle safety")
    print("NOTE: HIL/electrical, endurance, OTA, Alembic, dependency and frontend production CI remain certification gates")
if __name__ == "__main__": main()
