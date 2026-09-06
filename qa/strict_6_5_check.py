"""Self-contained static QA gate for the 6.5.0 hardened candidate."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

def fail(msg):
    raise SystemExit("FAIL: " + msg)

def main():
    backend = (ROOT / "backend/main.py").read_text(encoding="utf-8-sig")
    render = (ROOT / "backend/render.yaml").read_text()
    req = (ROOT / "backend/requirements.txt").read_text()
    api = (ROOT / "frontend/src/lib/api.ts").read_text()
    login = (ROOT / "frontend/src/app/login/page.tsx").read_text()
    state = (ROOT / "pi_firmware/state.py").read_text()
    queue = (ROOT / "pi_firmware/offline_queue.py").read_text()
    controller = (ROOT / "pi_firmware/ems_controller.py").read_text()

    if re.search(r"CREATE\s+TABLE|ALTER\s+TABLE|DROP\s+TABLE", backend, re.I): fail("application startup still contains DDL")
    if '"queued": {"delivered", "expired"}' not in backend: fail("queued->executing is still allowed")
    if 'COMMAND_DELIVERY_LEASE_SECONDS = 120' not in backend or 'COMMAND_EXPIRY_SECONDS = 300' not in backend: fail("lease/expiry constants missing")
    if 'status IN (\'queued\',\'delivered\')' not in backend: fail("absolute expiry enforcement missing")
    if "status='delivered' AND delivered_at < %s" not in backend: fail("delivery lease reclaim missing")
    if 'X-CSRF-Token' not in backend or 'ems_access' not in backend or 'ems_refresh' not in backend: fail("cookie/CSRF auth hardening missing")
    if 'auth_refresh_tokens' not in (ROOT / "backend/alembic/versions/0001_ems_current_schema.py").read_text(): fail("refresh-token migration missing")
    if 'alembic upgrade head' not in (ROOT / "backend/start.sh").read_text(): fail("migration-first startup missing")
    if 'alembic==1.13.3' not in req or 'SQLAlchemy==2.0.36' not in req: fail("Alembic dependencies missing")
    if 'rootDir: backend' not in render or 'startCommand: ./start.sh' not in render: fail("Render migration-first configuration missing")
    if 'localStorage.setItem("token"' in login or 'localStorage.getItem("token"' in api: fail("JWT still stored in browser storage")
    if 'withCredentials: true' not in api: fail("credentialed cookie requests missing")
    if 'data.get("last_usage_date")' not in state or 'data.get("last_reset_period")' not in state: fail("legacy state defaults missing")
    if "status IN ('EXECUTING', 'HARDWARE_VERIFIED', 'UNKNOWN_AFTER_REBOOT')" not in queue: fail("reboot recovery set missing")
    if 'status=\'DELIVERED\'' not in queue or 'UNKNOWN_AFTER_REBOOT' not in queue: fail("normal queue safety markers missing")
    if 'persist EXECUTING before touching hardware' not in controller: fail("pre-hardware persistence safety invariant missing")
    if 'User=pi' not in (ROOT / "pi_firmware/ems-controller.service").read_text(): fail("least privilege service missing")
    if 'Group=pi' not in (ROOT / "pi_firmware/ems-controller.service").read_text(): fail("least privilege group missing")
    if 'EMS_DATA_DEVICE' not in (ROOT / "pi_firmware/setup_pi.sh").read_text(): fail("explicit data device provisioning missing")
    print("PASS: 6.5.0 static industrial hardening invariants")

if __name__ == "__main__": main()
