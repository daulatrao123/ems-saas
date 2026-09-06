"""Runtime compatibility checks that do not require GPIO or PostgreSQL."""
import os
os.environ.setdefault("EMS_DEVICE_ID", "QA-DEVICE")
os.environ.setdefault("EMS_API_KEY", "QA-KEY")
from pathlib import Path
import json, tempfile, sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi_firmware"))

class StorageStub:
    def is_write_allowed(self, _kind): return True

# Queue migration: create a deliberately old lifecycle schema, then instantiate.
import offline_queue
with tempfile.TemporaryDirectory() as td:
    db = Path(td) / "queue.sqlite"
    offline_queue.DB_FILE = str(db)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE commands (id TEXT PRIMARY KEY, slot TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'DELIVERED', created_at TEXT)")
    conn.commit(); conn.close()
    q = offline_queue.OfflineQueue(StorageStub())
    cols = {r[1] for r in q.conn.execute("PRAGMA table_info(commands)").fetchall()}
    required = {"delivered_at","started_at","hardware_verified_at","completed_at","acked_at","expires_at","attempt_count","last_error","config_version","hardware_verification","ack_status"}
    assert required <= cols, (required - cols)
    q.close()

# STATE_VERSION=4 document without the newly-added usage/reset fields.
import state
old = {
    "version": state.STATE_VERSION,
    "generation": 1,
    "timestamp": "2026-01-01T00:00:00+00:00",
    "system_state": "READY",
    "active_slot": "A",
    "slots": {s: {"slot": s, "commanded_state":"OFF", "gpio_output_state":"OFF", "feedback_state":"OFF", "verification_state":"VERIFIED_OFF", "last_command_at":None} for s in state.SUPPORTED_SLOTS},
}
canonical = json.dumps(old, sort_keys=True, separators=(",", ":"))
old["sha256"] = __import__('hashlib').sha256(canonical.encode()).hexdigest()
assert state.PiStateManager._verify_document(old)
assert "last_usage_date" not in old and "last_reset_period" not in old
print("PASS: legacy SQLite queue and STATE_VERSION=4 compatibility")
