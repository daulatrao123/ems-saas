from pathlib import Path
import re
ROOT=Path(__file__).resolve().parents[1]
backend=(ROOT/'backend/main.py').read_text()
state=(ROOT/'pi_firmware/state.py').read_text()
queue=(ROOT/'pi_firmware/offline_queue.py').read_text()
api=(ROOT/'pi_firmware/api_client.py').read_text()
service=(ROOT/'pi_firmware/ems-controller.service').read_text()
setup=(ROOT/'pi_firmware/setup_pi.sh').read_text()
frontend=(ROOT/'frontend/src/lib/api.ts').read_text()
checks={
 'no startup DDL': 'ensure_db_schema' not in backend and 'CREATE TABLE' not in backend,
 'strict queued transition': '"queued": {"delivered", "expired"}' in backend and 'queued", "executing' not in backend,
 'delivery lease': 'COMMAND_DELIVERY_LEASE_SECONDS = 120' in backend,
 'absolute expiry': 'COMMAND_EXPIRY_SECONDS = 300' in backend,
 'cookie session': 'ems_session' in backend and 'httponly=True' in backend and 'samesite="none"' in backend,
 'frontend credentials cookie': 'withCredentials: true' in frontend and 'localStorage' not in frontend,
 'signed OTA backend': 'EMS_FIRMWARE_SIGNING_PRIVATE_KEY_B64' in backend and 'Ed25519PrivateKey' in backend,
 'signed OTA pi verify': 'Ed25519PublicKey' in (ROOT/'pi_firmware/ota_manager.py').read_text() and 'key.verify' in (ROOT/'pi_firmware/ota_manager.py').read_text(),
 'A/B OTA boot': 'slot_a' in (ROOT/'pi_firmware/ota_manager.py').read_text() and 'slot_b' in (ROOT/'pi_firmware/ota_manager.py').read_text() and 'ota_boot.sh' in service,
 'least privilege service': 'User=pi' in service and 'Group=pi' in service and 'NoNewPrivileges=true' in service,
 'explicit storage device': 'EMS_DATA_DEVICE' in setup and 'mkfs' not in setup,
 'Pi header auth': 'X-Device-ID' in api and 'X-API-Key' in api and 'payload["key"]' not in api,
 'legacy state compatibility': 'data.get("used_days", 0)' in state and 'data.get("last_reset_period")' in state,
 'unknown reboot excluded': "status IN ('EXECUTING', 'HARDWARE_VERIFIED', 'UNKNOWN_AFTER_REBOOT')" in queue,
 'hardware verified contract': 'HARDWARE_VERIFIED' in backend and 'VERIFIED_ON' in backend,
}
for k,v in checks.items(): print(('PASS' if v else 'FAIL')+' '+k)
failed=[k for k,v in checks.items() if not v]
raise SystemExit(1 if failed else 0)
