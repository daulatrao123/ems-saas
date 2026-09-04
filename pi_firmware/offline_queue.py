import os
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
    "DELIVERED": (
        "EXECUTING",
        "EXPIRED",
    ),

    "EXECUTING": (
        "HARDWARE_VERIFIED",
        "FAILED",
        "UNKNOWN_AFTER_REBOOT",
    ),

    "UNKNOWN_AFTER_REBOOT": (
        "HARDWARE_VERIFIED",
        "FAILED",
        "COMPLETED",
    ),

    "HARDWARE_VERIFIED": (
        "COMPLETED",
    ),

    "COMPLETED": (
        "ACKED",
    ),

    "FAILED": (
        "ACKED",
    ),

    "EXPIRED": (
        "ACKED",
    ),
}


FINAL_STATUSES = {
    "COMPLETED",
    "FAILED",
    "EXPIRED",
}


class OfflineQueue:
    """
    Durable command queue.

    Design rules:

    1. SQLite WAL.
    2. synchronous=FULL explicitly.
    3. Only command lifecycle data is persisted here.
    4. Routine polling is read-only.
    5. Commands are atomically claimed.
    6. Expired commands never reach hardware.
    7. Duplicate cloud delivery is idempotent.
    8. Only ACKED history may be deleted.
    9. Unacked / executing commands are never deleted.
    """

    def __init__(self, storage_manager):
        self.storage = storage_manager
        self.lock = threading.RLock()

        os.makedirs(
            os.path.dirname(DB_FILE),
            exist_ok=True,
        )

        self.conn = sqlite3.connect(
            DB_FILE,
            timeout=SQLITE_BUSY_TIMEOUT_MS / 1000.0,
            check_same_thread=False,
        )

        self.conn.execute(
            f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS};"
        )

        self.conn.execute(
            "PRAGMA journal_mode=WAL;"
        )

        # IMPORTANT:
        # Command lifecycle is safety-critical.
        self.conn.execute(
            "PRAGMA synchronous=FULL;"
        )

        self.conn.execute(
            "PRAGMA temp_store=MEMORY;"
        )

        self.conn.execute(
            f"PRAGMA wal_autocheckpoint="
            f"{SQLITE_WAL_AUTOCHECKPOINT_PAGES};"
        )

        self._create_schema()
        self._integrity_check()

    # ============================================================
    # DATABASE INTEGRITY
    # ============================================================

    def _integrity_check(self):
        with self.lock:
            try:
                row = self.conn.execute(
                    "PRAGMA integrity_check;"
                ).fetchone()

                result = (
                    str(row[0]).strip().lower()
                    if row
                    else ""
                )

                if result != "ok":
                    logger.critical(
                        "FATAL: SQLite integrity check failed: %s",
                        result,
                    )
                    raise RuntimeError(
                        "EMS command queue integrity check failed"
                    )

            except sqlite3.Error as exc:
                logger.critical(
                    "FATAL: SQLite integrity check error: %s",
                    exc,
                )
                raise RuntimeError(
                    "EMS command queue integrity check failed"
                ) from exc

    # ============================================================
    # SCHEMA
    # ============================================================

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
                        NOT NULL DEFAULT 0,

                    last_error TEXT,
                    config_version TEXT,
                    hardware_verification TEXT,

                    ack_status TEXT
                        NOT NULL DEFAULT 'PENDING'
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

    # ============================================================
    # INSERT / IDEMPOTENCY
    # ============================================================

    def add_command(
        self,
        cmd_id,
        slot,
        action,
        created_at,
        expires_at,
        config_version=None,
    ):
        if not self.storage.is_write_allowed("queue_db"):
            logger.critical(
                "Queue persistence blocked."
            )
            return False

        with self.lock:
            try:
                now = datetime.now(
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
                        expires_at,
                        config_version
                    )
                    VALUES
                    (
                        ?, ?, ?,
                        'DELIVERED',
                        ?, ?, ?, ?
                    )
                    """,
                    (
                        str(cmd_id),
                        str(slot),
                        str(action),
                        created_at,
                        now,
                        expires_at,
                        (
                            str(config_version)
                            if config_version is not None
                            else None
                        ),
                    ),
                )

                self.conn.commit()
                return True

            except sqlite3.IntegrityError:
                # Duplicate delivery.
                # Verify that the duplicate is actually
                # the same command, rather than silently
                # accepting a conflicting command ID.
                row = self.conn.execute(
                    """
                    SELECT slot, action
                    FROM commands
                    WHERE id=?
                    """,
                    (str(cmd_id),),
                ).fetchone()

                if row and (
                    str(row[0]) == str(slot)
                    and str(row[1]) == str(action)
                ):
                    return True

                logger.critical(
                    "Command ID collision detected: %s",
                    cmd_id,
                )
                return False

            except sqlite3.Error as exc:
                logger.critical(
                    "Queue insert failed: %s",
                    exc,
                )
                return False

    # ============================================================
    # ATOMIC CLAIM
    # ============================================================

    def claim_next(self):
        """
        Atomically:

            DELIVERED
                ↓
            EXECUTING

        and returns:

            (id, slot, action)

        Expired commands are marked EXPIRED and never
        returned to the controller.
        """

        if not self.storage.is_write_allowed("queue_db"):
            return None

        now = datetime.now(
            timezone.utc
        )

        now_iso = now.isoformat()

        with self.lock:
            try:
                self.conn.execute(
                    "BEGIN IMMEDIATE;"
                )

                # Expire old commands first.
                self.conn.execute(
                    """
                    UPDATE commands
                    SET
                        status='EXPIRED',
                        completed_at=?,
                        last_error='COMMAND_EXPIRED'
                    WHERE
                        status='DELIVERED'
                        AND expires_at IS NOT NULL
                        AND expires_at <= ?
                    """,
                    (
                        now_iso,
                        now_iso,
                    ),
                )

                row = self.conn.execute(
                    """
                    SELECT
                        id,
                        slot,
                        action
                    FROM commands
                    WHERE
                        status='DELIVERED'
                        AND (
                            expires_at IS NULL
                            OR expires_at > ?
                        )
                    ORDER BY
                        created_at ASC
                    LIMIT 1
                    """,
                    (now_iso,),
                ).fetchone()

                if not row:
                    self.conn.commit()
                    return None

                cmd_id, slot, action = row

                updated = self.conn.execute(
                    """
                    UPDATE commands
                    SET
                        status='EXECUTING',
                        started_at=?,
                        attempt_count =
                            attempt_count + 1
                    WHERE
                        id=?
                        AND status='DELIVERED'
                        AND (
                            expires_at IS NULL
                            OR expires_at > ?
                        )
                    """,
                    (
                        now_iso,
                        cmd_id,
                        now_iso,
                    ),
                ).rowcount

                if updated != 1:
                    self.conn.rollback()
                    return None

                self.conn.commit()

                return (
                    cmd_id,
                    slot,
                    action,
                )

            except sqlite3.Error as exc:
                try:
                    self.conn.rollback()
                except Exception:
                    pass

                logger.critical(
                    "Atomic command claim failed: %s",
                    exc,
                )
                return None

    # ============================================================
    # INTERRUPTED COMMANDS
    # ============================================================

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
                ORDER BY started_at ASC
                """
            )

            return cur.fetchall()

    # ============================================================
    # UNACKNOWLEDGED
    # ============================================================

    def get_unacked(self):
        with self.lock:
            cur = self.conn.execute(
                """
                SELECT
                    id,
                    status,
                    hardware_verification,
                    last_error
                FROM commands
                WHERE
                    ack_status='PENDING'
                    AND status IN (
                        'COMPLETED',
                        'FAILED',
                        'EXPIRED'
                    )
                ORDER BY
                    completed_at ASC
                LIMIT 10
                """
            )

            return cur.fetchall()

    # ============================================================
    # STATUS TRANSITION
    # ============================================================

    def update_status(
        self,
        cmd_id,
        status,
        verification=None,
        error=None,
    ):
        if not self.storage.is_write_allowed("queue_db"):
            logger.critical(
                "Queue status persistence blocked."
            )
            return False

        status = str(status).upper()

        with self.lock:
            try:
                row = self.conn.execute(
                    """
                    SELECT status
                    FROM commands
                    WHERE id=?
                    """,
                    (str(cmd_id),),
                ).fetchone()

                if not row:
                    return False

                current_status = str(
                    row[0]
                ).upper()

                if status not in VALID_TRANSITIONS.get(
                    current_status,
                    (),
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
                    "EXECUTING": "started_at",
                    "HARDWARE_VERIFIED":
                        "hardware_verified_at",
                    "COMPLETED": "completed_at",
                    "FAILED": "completed_at",
                    "EXPIRED": "completed_at",
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
                        WHERE
                            id=?
                            AND status=?
                        """,
                        (
                            status,
                            verification,
                            error,
                            now,
                            str(cmd_id),
                            current_status,
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
                        WHERE
                            id=?
                            AND status=?
                        """,
                        (
                            status,
                            verification,
                            error,
                            str(cmd_id),
                            current_status,
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

    # ============================================================
    # ACK
    # ============================================================

    def mark_acked(self, cmd_id):
        if not self.storage.is_write_allowed("queue_db"):
            return False

        with self.lock:
            try:
                row = self.conn.execute(
                    """
                    SELECT status, ack_status
                    FROM commands
                    WHERE id=?
                    """,
                    (str(cmd_id),),
                ).fetchone()

                if not row:
                    return False

                status, ack_status = row

                if status not in FINAL_STATUSES:
                    return False

                if ack_status == "ACKED":
                    return True

                now = datetime.now(
                    timezone.utc
                ).isoformat()

                self.conn.execute(
                    """
                    UPDATE commands
                    SET
                        ack_status='ACKED',
                        acked_at=?
                    WHERE
                        id=?
                        AND status IN (
                            'COMPLETED',
                            'FAILED',
                            'EXPIRED'
                        )
                    """,
                    (
                        now,
                        str(cmd_id),
                    ),
                )

                self.conn.commit()
                return True

            except sqlite3.Error as exc:
                logger.critical(
                    "Queue ACK update failed: %s",
                    exc,
                )
                return False

    # ============================================================
    # CLEANUP
    # ============================================================

    def cleanup_acked(self):
        """
        Deletes only acknowledged history.

        Safety rule:
        NEVER delete DELIVERED / EXECUTING /
        UNKNOWN_AFTER_REBOOT / HARDWARE_VERIFIED /
        unacknowledged final commands.
        """

        if not self.storage.is_write_allowed("queue_db"):
            return

        with self.lock:
            try:
                self.conn.execute(
                    """
                    DELETE FROM commands
                    WHERE id IN (
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

                # 500 is a history target, not a reason
                # to destroy safety-critical unacked records.
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

                # Passive means:
                # do not force a large blocking checkpoint.
                self.conn.execute(
                    "PRAGMA wal_checkpoint(PASSIVE);"
                )

            except sqlite3.Error as exc:
                logger.error(
                    "Queue cleanup failed: %s",
                    exc,
                )

    # ============================================================
    # SHUTDOWN
    # ============================================================

    def close(self):
        with self.lock:
            try:
                self.conn.execute(
                    "PRAGMA wal_checkpoint(TRUNCATE);"
                )
            except sqlite3.Error:
                pass

            try:
                self.conn.close()
            except sqlite3.Error:
                pass