import sqlite3
from config import DB_FILE, SQLITE_BUSY_TIMEOUT_MS
from logger import logger

class OfflineQueue:
    def __init__(self):
        self.conn = sqlite3.connect(DB_FILE, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000.0, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS commands (
            id TEXT PRIMARY KEY, slot TEXT, action TEXT, status TEXT DEFAULT 'DELIVERED',
            created_at TEXT, received_at TEXT, started_at TEXT, completed_at TEXT,
            expires_at TEXT, attempt_count INT DEFAULT 0, last_error TEXT,
            config_version TEXT, hardware_verification TEXT, ack_status TEXT DEFAULT 'PENDING'
        )""")
        self.conn.commit()

    def add_command(self, cmd_id, slot, action, created_at, expires_at):
        try:
            self.conn.execute("""INSERT INTO commands 
                (id, slot, action, status, created_at, received_at, expires_at) 
                VALUES (?, ?, ?, 'DELIVERED', ?, ?, ?)""",
                (cmd_id, slot, action, created_at, created_at, expires_at))
            self.conn.commit()
        except sqlite3.IntegrityError: pass

    def get_next(self):
        cur = self.conn.execute("SELECT id, slot, action FROM commands WHERE status='DELIVERED' ORDER BY created_at LIMIT 1")
        return cur.fetchone()

    def update_status(self, cmd_id, status, verification=None, error=None):
        self.conn.execute("""UPDATE commands SET status=?, hardware_verification=?, last_error=? WHERE id=?""",
                           (status, verification, error, cmd_id))
        self.conn.commit()

    def mark_acked(self, cmd_id):
        self.conn.execute("UPDATE commands SET ack_status='ACKED' WHERE id=?", (cmd_id,))
        self.conn.commit()

    def get_unacked(self):
        cur = self.conn.execute("SELECT id, status, hardware_verification FROM commands WHERE ack_status='PENDING' AND status IN ('COMPLETED', 'FAILED', 'EXPIRED')")
        return cur.fetchall()

    def cleanup_acked(self):
        self.conn.execute("DELETE FROM commands WHERE ack_status='ACKED'")
        self.conn.commit()