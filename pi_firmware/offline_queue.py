import sqlite3
from datetime import datetime
from config import DB_FILE, SQLITE_BUSY_TIMEOUT_MS
from logger import logger

# Valid state transitions
VALID_TRANSITIONS = {
    "DELIVERED": ["EXECUTING", "EXPIRED"],
    "EXECUTING": ["HARDWARE_VERIFIED", "FAILED", "UNKNOWN_AFTER_REBOOT"],
    "UNKNOWN_AFTER_REBOOT": ["HARDWARE_VERIFIED", "FAILED", "COMPLETED"],
    "HARDWARE_VERIFIED": ["COMPLETED"],
    "COMPLETED": ["ACKED"],
    "FAILED": ["ACKED"],
    "EXPIRED": ["ACKED"]
}

class OfflineQueue:
    def __init__(self):
        self.conn = sqlite3.connect(DB_FILE, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000.0, check_same_thread=False)
        
        # Industrial Flash Optimization
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;") 
        self.conn.execute("PRAGMA temp_store=MEMORY;")
        self.conn.execute("PRAGMA wal_autocheckpoint=1000;")
        
        self.conn.execute("""CREATE TABLE IF NOT EXISTS commands (
            id TEXT PRIMARY KEY, slot TEXT, action TEXT, status TEXT DEFAULT 'DELIVERED',
            created_at TEXT, delivered_at TEXT, started_at TEXT, hardware_verified_at TEXT,
            completed_at TEXT, acked_at TEXT, expires_at TEXT, attempt_count INT DEFAULT 0, 
            last_error TEXT, config_version TEXT, hardware_verification TEXT, ack_status TEXT DEFAULT 'PENDING'
        )""")
        self.conn.commit()

    def add_command(self, cmd_id, slot, action, created_at, expires_at):
        try:
            ts = datetime.utcnow().isoformat()
            self.conn.execute("""INSERT INTO commands 
                (id, slot, action, status, created_at, delivered_at, expires_at) 
                VALUES (?, ?, ?, 'DELIVERED', ?, ?, ?)""",
                (cmd_id, slot, action, created_at, ts, expires_at))
            self.conn.commit()
            # Approximate DB write attribution
            if hasattr(self, '_storage_mgr'): self._storage_mgr.io_meter.record_ems_write("queue_db", 512)
        except sqlite3.IntegrityError: pass

    def get_next(self):
        cur = self.conn.execute("SELECT id, slot, action FROM commands WHERE status='DELIVERED' ORDER BY created_at LIMIT 1")
        return cur.fetchone()

    def get_interrupted(self):
        cur = self.conn.execute("SELECT id, slot, action FROM commands WHERE status='EXECUTING'")
        return cur.fetchall()

    def update_status(self, cmd_id, status, verification=None, error=None):
        # Enforce State Machine
        cur = self.conn.execute("SELECT status FROM commands WHERE id=?", (cmd_id,))
        row = cur.fetchone()
        if not row: return
        
        current_status = row[0]
        if status not in VALID_TRANSITIONS.get(current_status, []):
            logger.error(f"Invalid state transition: {current_status} -> {status} for cmd {cmd_id}")
            return

        ts = datetime.utcnow().isoformat()
        field = None
        if status == "EXECUTING": field = "started_at"
        elif status == "HARDWARE_VERIFIED": field = "hardware_verified_at"
        elif status in ["COMPLETED", "FAILED", "EXPIRED"]: field = "completed_at"
        
        if field:
            self.conn.execute(f"""UPDATE commands SET status=?, hardware_verification=?, last_error=?, {field}=? WHERE id=?""",
                               (status, verification, error, ts, cmd_id))
        else:
            self.conn.execute("""UPDATE commands SET status=?, hardware_verification=?, last_error=? WHERE id=?""",
                               (status, verification, error, cmd_id))
        self.conn.commit()
        if hasattr(self, '_storage_mgr'): self._storage_mgr.io_meter.record_ems_write("queue_db", 512)

    def mark_acked(self, cmd_id):
        ts = datetime.utcnow().isoformat()
        self.conn.execute("UPDATE commands SET ack_status='ACKED', acked_at=? WHERE id=?", (ts, cmd_id))
        self.conn.commit()
        if hasattr(self, '_storage_mgr'): self._storage_mgr.io_meter.record_ems_write("queue_db", 256)

    def get_unacked(self):
        cur = self.conn.execute("SELECT id, status, hardware_verification FROM commands WHERE ack_status='PENDING' AND status IN ('COMPLETED', 'FAILED', 'EXPIRED')")
        return cur.fetchall()

    def cleanup_acked(self):
        # Strict bounded queue: Keep max 500 records to prevent database bloat
        self.conn.execute("DELETE FROM commands WHERE ack_status='ACKED' LIMIT 100")
        
        cur = self.conn.execute("SELECT COUNT(*) FROM commands")
        count = cur.fetchone()[0]
        if count > 500:
            self.conn.execute("""DELETE FROM commands WHERE id IN (
                SELECT id FROM commands ORDER BY created_at ASC LIMIT ?
            )""", (count - 500,))
            
        self.conn.commit()