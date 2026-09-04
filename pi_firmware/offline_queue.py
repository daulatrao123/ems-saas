import sqlite3
import threading
from datetime import datetime, timezone

from config import (
    DB_FILE,
    SQLITE_BUSY_TIMEOUT_MS,
    SQLITE_WAL_AUTOCHECKPOINT_PAGES,
)

from logger import logger


VALID_TRANSITIONS = {
    "DELIVERED": [
        "EXECUTING",
        "EXPIRED",
    ],

    "EXECUTING": [
        "HARDWARE_VERIFIED",
        "FAILED",
        "UNKNOWN_AFTER_REBOOT",
    ],

    "UNKNOWN_AFTER_REBOOT": [
        "HARDWARE_VERIFIED",
        "FAILED",
        "COMPLETED",
    ],

    "HARDWARE_VERIFIED": [
        "COMPLETED",
    ],

    "COMPLETED": [
        "ACKED",
    ],

    "FAILED": [
        "ACKED",
    ],

    "EXPIRED": [
        "ACKED",
    ],
}


class OfflineQueue:
    """
    Durable command queue.

    SQLite is durable only for command lifecycle.
    Routine polling does not write to SQLite.
    """

    def __init__(self, storage_manager):
        self.storage = storage_manager

        self.conn = sqlite3.connect(
            DB_FILE,
            timeout=(
                SQLITE_BUSY_TIMEOUT_MS
                / 1000.0
            ),
            check_same_thread=False,
        )

        self.lock = threading.RLock()

        self.conn.execute(
            "PRAGMA journal_mode=WAL;"
        )

        self.conn.execute(
           "PRAGMA wal_checkpoint(PASSIVE);"
        )

        self.conn.execute(
            "PRAGMA temp_store=MEMORY;"
        )

        self.conn.execute(
            f"PRAGMA wal_autocheckpoint="
            f"{SQLITE_WAL_AUTOCHECKPOINT_PAGES};"
        )

        self.conn.execute(
            "PRAGMA busy_timeout="
            f"{SQLITE_BUSY_TIMEOUT_MS};"
        )

        self._create_schema()

    # ------------------------------------------------------------
    # SCHEMA
    # ------------------------------------------------------------

    def _create_schema(self):
        with self.lock:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS commands (
                    id TEXT PRIMARY KEY,
                    slot TEXT NOT NULL,
                    action TEXT NOT NULL,

                    status TEXT NOT NULL
                        DEFAULT 'DELIVERED',

                    created_at TEXT,
                    delivered_at TEXT,
                    started_at TEXT,
                    hardware_verified_at TEXT,
                    completed_at TEXT,
                    acked_at TEXT,
                    expires_at TEXT,

                    attempt_count INTEGER
                        DEFAULT 0,

                    last_error TEXT,
                    config_version TEXT,
                    hardware_verification TEXT,

                    ack_status TEXT
                        DEFAULT 'PENDING'
                )
                """
            )

            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_commands_status_created
                ON commands(status, created_at)
                """
            )

            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_commands_ack
                ON commands(ack_status, status)
                """
            )

            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_commands_expiry
                ON commands(expires_at)
                """
            )

            self.conn.commit()

    # ------------------------------------------------------------
    # INSERT
    # ------------------------------------------------------------

    def add_command(
        self,
        cmd_id,
        slot,
        action,
        created_at,
        expires_at,
    ):
        if not self.storage.is_write_allowed(
            "queue_db"
        ):
            logger.critical(
                "Queue persistence blocked."
            )
            return False

        with self.lock:
            try:
                ts = datetime.now(
                    timezone.utc
                ).isoformat()

                self.conn.execute(
                    """
                    INSERT INTO commands
                    (
                        id,
                        slot,
                        action,
                        status,
                        created_at,
                        delivered_at,
                        expires_at
                    )
                    VALUES
                    (
                        ?, ?, ?, 'DELIVERED',
                        ?, ?, ?
                    )
                    """,
                    (
                        cmd_id,
                        slot,
                        action,
                        created_at,
                        ts,
                        expires_at,
                    ),
                )

                self.conn.commit()

                return True

            except sqlite3.IntegrityError:
                # Duplicate cloud delivery is expected.
                return True

            except sqlite3.Error as exc:
                logger.critical(
                    "Queue insert failed: %s",
                    exc,
                )
                return False

    # ------------------------------------------------------------
    # READS — NO WRITES
    # ------------------------------------------------------------

    def get_next(self):
        with self.lock:
            cur = self.conn.execute(
                """
                SELECT id, slot, action
                FROM commands
                WHERE status='DELIVERED'
                ORDER BY created_at ASC
                LIMIT 1
                """
            )

            return cur.fetchone()

    def get_interrupted(self):
        with self.lock:
            cur = self.conn.execute(
                """
                SELECT
                    id,
                    slot,
                    action
                FROM commands
                WHERE status='EXECUTING'
                """
            )

            return cur.fetchall()

    def get_unacked(self):
        with self.lock:
            cur = self.conn.execute(
                """
                SELECT
                    id,
                    status,
                    hardware_verification
                FROM commands
                WHERE ack_status='PENDING'
                AND status IN (
                    'COMPLETED',
                    'FAILED',
                    'EXPIRED'
                )
                ORDER BY completed_at ASC
                """
            )

            return cur.fetchall()

    # ------------------------------------------------------------
    # STATUS
    # ------------------------------------------------------------

    def update_status(
        self,
        cmd_id,
        status,
        verification=None,
        error=None,
    ):
        if not self.storage.is_write_allowed(
            "queue_db"
        ):
            logger.critical(
                "Queue status persistence blocked."
            )
            return False

        with self.lock:
            try:
                cur = self.conn.execute(
                    """
                    SELECT status
                    FROM commands
                    WHERE id=?
                    """,
                    (cmd_id,),
                )

                row = cur.fetchone()

                if not row:
                    return False

                current_status = row[0]

                if status not in (
                    VALID_TRANSITIONS.get(
                        current_status,
                        [],
                    )
                ):
                    logger.error(
                        "Invalid command transition "
                        "%s -> %s for %s",
                        current_status,
                        status,
                        cmd_id,
                    )
                    return False

                now = datetime.now(
                    timezone.utc
                ).isoformat()

                timestamp_column = {
                    "EXECUTING":
                        "started_at",

                    "HARDWARE_VERIFIED":
                        "hardware_verified_at",

                    "COMPLETED":
                        "completed_at",

                    "FAILED":
                        "completed_at",

                    "EXPIRED":
                        "completed_at",
                }.get(status)

                if timestamp_column:
                    self.conn.execute(
                        f"""
                        UPDATE commands
                        SET
                            status=?,
                            hardware_verification=?,
                            last_error=?,
                            {timestamp_column}=?
                        WHERE id=?
                        """,
                        (
                            status,
                            verification,
                            error,
                            now,
                            cmd_id,
                        ),
                    )

                else:
                    self.conn.execute(
                        """
                        UPDATE commands
                        SET
                            status=?,
                            hardware_verification=?,
                            last_error=?
                        WHERE id=?
                        """,
                        (
                            status,
                            verification,
                            error,
                            cmd_id,
                        ),
                    )

                self.conn.commit()

                return True

            except sqlite3.Error as exc:
                logger.critical(
                    "Queue status update failed: %s",
                    exc,
                )
                return False

    # ------------------------------------------------------------
    # ACK
    # ------------------------------------------------------------

    def mark_acked(self, cmd_id):
        if not self.storage.is_write_allowed(
            "queue_db"
        ):
            return False

        with self.lock:
            try:
                ts = datetime.now(
                    timezone.utc
                ).isoformat()

                self.conn.execute(
                    """
                    UPDATE commands
                    SET
                        ack_status='ACKED',
                        acked_at=?
                    WHERE id=?
                    """,
                    (ts, cmd_id),
                )

                self.conn.commit()

                return True

            except sqlite3.Error as exc:
                logger.critical(
                    "Queue ACK update failed: %s",
                    exc,
                )
                return False

    # ------------------------------------------------------------
    # CLEANUP
    # ------------------------------------------------------------

    def cleanup_acked(self):
        if not self.storage.is_write_allowed(
            "queue_db"
        ):
            return

        with self.lock:
            try:
                self.conn.execute(
                    """
                    DELETE FROM commands
                    WHERE ack_status='ACKED'
                    AND id IN (
                        SELECT id
                        FROM commands
                        WHERE ack_status='ACKED'
                        ORDER BY acked_at ASC
                        LIMIT 100
                    )
                    """
                )

                count = self.conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM commands
                    """
                ).fetchone()[0]

                if count > 500:
                    excess = count - 500

                    self.conn.execute(
                        """
                        DELETE FROM commands
                        WHERE id IN (
                            SELECT id
                            FROM commands
                            WHERE ack_status='ACKED'
                            ORDER BY acked_at ASC
                            LIMIT ?
                        )
                        """,
                        (excess,),
                    )

                self.conn.commit()

                # Controlled WAL checkpoint.
                self.conn.execute(
                    "PRAGMA wal_checkpoint(PASSIVE);"
                )

            except sqlite3.Error as exc:
                logger.error(
                    "Queue cleanup failed: %s",
                    exc,
                )

    # ------------------------------------------------------------
    # SHUTDOWN
    # ------------------------------------------------------------

    def close(self):
        with self.lock:
            try:
                self.conn.execute(
                    "PRAGMA wal_checkpoint(TRUNCATE);"
                )
            except sqlite3.Error:
                pass

            self.conn.close()