import sqlite3, json, os, time

class OfflineQueue:
    def __init__(self, config, logger):
        self.db_path = config.offlineDbPath
        self.log = logger
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS pending_acks (
                command_id TEXT PRIMARY KEY, success INTEGER, result TEXT, error TEXT
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS executed_commands (
                command_id TEXT PRIMARY KEY, status TEXT, result TEXT, executed_at INTEGER
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE, payload TEXT, created_at INTEGER
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS state_vars (
                key TEXT PRIMARY KEY, value TEXT
            )""")

    def save_ack(self, cid, success, result, error):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO pending_acks VALUES (?, ?, ?, ?)",
                         (cid, 1 if success else 0, result, error))

    def load_acks(self):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("SELECT command_id, success, result, error FROM pending_acks")
            return [{"command_id": r[0], "success": bool(r[1]), "result": r[2], "error": r[3]} for r in cur]

    def delete_ack(self, cid):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM pending_acks WHERE command_id=?", (cid,))

    def check_command_executed(self, cid):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("SELECT status, result FROM executed_commands WHERE command_id=?", (cid,))
            row = cur.fetchone()
            return {"status": row[0], "result": row[1]} if row else None

    def log_command_start(self, cid):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO executed_commands (command_id, status, result, executed_at) VALUES (?, 'STARTED', NULL, ?)",
                         (cid, int(time.time())))

    def update_command_status(self, cid, status, result):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE executed_commands SET status=?, result=? WHERE command_id=?",
                         (status, result, cid))

    def push_event(self, event_id, payload):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR IGNORE INTO events (event_id, payload, created_at) VALUES (?, ?, ?)",
                         (event_id, json.dumps(payload), int(time.time())))

    def load_events(self, limit=50):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("SELECT id, event_id, payload FROM events ORDER BY created_at ASC LIMIT ?", (limit,))
            return cur.fetchall()

    def delete_event(self, db_id):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM events WHERE id=?", (db_id,))

    def set_state(self, key, value):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO state_vars (key, value) VALUES (?, ?)", (key, str(value)))

    def get_state(self, key):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("SELECT value FROM state_vars WHERE key=?", (key,))
            row = cur.fetchone()
            return row[0] if row else None