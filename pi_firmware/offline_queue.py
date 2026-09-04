import sqlite3
from datetime import datetime
from config import DB_FILE, SQLITE_BUSY_TIMEOUT_MS
from logger import logger

class OfflineQueue:
    def __init__(self):
        self.conn = sqlite3.connect(DB_FILE, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000.0, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
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
        except sqlite3.IntegrityError: pass

    def get_next(self):
        cur = self.conn.execute("SELECT id, slot, action FROM commands WHERE status='DELIVERED' ORDER BY created_at LIMIT 1")
        return cur.fetchone()

    def get_interrupted(self):
        cur = self.conn.execute("SELECT id, slot, action FROM commands WHERE status='EXECUTING'")
        return cur.fetchall()

    def update_status(self, cmd_id, status, verification=None, error=None):
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

    def mark_acked(self, cmd_id):
        ts = datetime.utcnow().isoformat()
        self.conn.execute("UPDATE commands SET ack_status='ACKED', acked_at=? WHERE id=?", (ts, cmd_id))
        self.conn.commit()

    def get_unacked(self):
        cur = self.conn.execute("SELECT id, status, hardware_verification FROM commands WHERE ack_status='PENDING' AND status IN ('COMPLETED', 'FAILED', 'EXPIRED')")
        return cur.fetchall()

    def cleanup_acked(self):
        self.conn.execute("DELETE FROM commands WHERE ack_status='ACKED'")
        self.conn.commit()