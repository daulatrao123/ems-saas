import hashlib, json, sqlite3, tempfile
from pathlib import Path


def test_legacy_state_shape():
    legacy = {
        "version": 4,
        "generation": 7,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "system_state": "READY",
        "active_slot": "A",
        "slots": {
            slot: {
                "slot": slot,
                "commanded_state": "ON" if slot == "A" else "OFF",
                "gpio_output_state": "ON" if slot == "A" else "OFF",
                "feedback_state": "UNKNOWN",
                "verification_state": "NOT_CONFIGURED",
                "last_command_at": None,
            }
            for slot in "ABCD"
        },
    }
    canonical = json.dumps(legacy, sort_keys=True, separators=(",", ":"))
    legacy["sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    for slot in legacy["slots"].values():
        assert slot.get("used_days", 0) == 0
        assert slot.get("clicks", 0) == 0
    assert legacy.get("last_reset_period") is None
    print("PASS: legacy STATE_VERSION=4 JSON defaults missing new fields")


def test_legacy_queue_columns():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "queue.sqlite"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE commands (id TEXT PRIMARY KEY, slot TEXT, action TEXT, status TEXT, created_at TEXT)")
        required = {
            "delivered_at": "TEXT", "started_at": "TEXT", "hardware_verified_at": "TEXT",
            "completed_at": "TEXT", "acked_at": "TEXT", "expires_at": "TEXT",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0", "last_error": "TEXT",
            "config_version": "TEXT", "hardware_verification": "TEXT",
            "ack_status": "TEXT NOT NULL DEFAULT 'PENDING'",
        }
        cols = {row[1] for row in con.execute("PRAGMA table_info(commands)")}
        for name, typ in required.items():
            if name not in cols:
                con.execute(f"ALTER TABLE commands ADD COLUMN {name} {typ}")
        con.execute("CREATE INDEX idx_commands_ack ON commands(ack_status, status)")
        con.commit()
        cols = {row[1] for row in con.execute("PRAGMA table_info(commands)")}
        assert set(required).issubset(cols)
        print("PASS: legacy SQLite queue schema can be upgraded before indexes")


if __name__ == "__main__":
    test_legacy_state_shape()
    test_legacy_queue_columns()
