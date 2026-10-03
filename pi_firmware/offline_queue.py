import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from config import (
    DB_FILE,
    SQLITE_BUSY_TIMEOUT_MS,
    SQLITE_WAL_AUTOCHECKPOINT_PAGES,
)

from logger import logger
import device_obs


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
        try:
            mode = self.conn.execute("PRAGMA journal_mode;").fetchone()
            sync = self.conn.execute("PRAGMA synchronous;").fetchone()
            device_obs.safe_observe("queue", lambda: device_obs.note_sqlite_settings(
                mode[0] if mode else None,
                sync[0] if sync else None,
            ))
        except Exception:
            device_obs.safe_observe("queue", lambda: device_obs.note_sqlite_settings("UNKNOWN", "UNKNOWN"))

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

            # Backward-compatible migration for queues created by older EMS
            # firmware. Migration MUST run before indexes reference new columns.
            existing_columns = {
                row[1] for row in self.conn.execute(
                    "PRAGMA table_info(commands);"
                ).fetchall()
            }
            migrations = {
                "delivered_at": "TEXT",
                "started_at": "TEXT",
                "hardware_verified_at": "TEXT",
                "completed_at": "TEXT",
                "acked_at": "TEXT",
                "expires_at": "TEXT",
                "attempt_count": "INTEGER NOT NULL DEFAULT 0",
                "last_error": "TEXT",
                "config_version": "TEXT",
                "hardware_verification": "TEXT",
                "ack_status": "TEXT NOT NULL DEFAULT 'PENDING'",
                "sequence_no": "INTEGER",
                "cloud_attempt": "INTEGER",
                "ack_attempts": "INTEGER NOT NULL DEFAULT 0",
                "ack_error": "TEXT",
            }
            for column, definition in migrations.items():
                if column not in existing_columns:
                    self.conn.execute(
                        f"ALTER TABLE commands ADD COLUMN {column} {definition}"
                    )

            # T6: durable execution identity that survives reboot and acked-history cleanup.
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS execution_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
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
        sequence_no=None,
        cloud_attempt=None,
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
                        config_version,
                        sequence_no,
                        cloud_attempt
                    )
                    VALUES
                    (
                        ?, ?, ?,
                        'DELIVERED',
                        ?, ?, ?, ?, ?, ?
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
                        int(sequence_no) if sequence_no is not None else None,
                        int(cloud_attempt) if cloud_attempt is not None else None,
                    ),
                )

                self.conn.commit()
                return True

            except sqlite3.IntegrityError:
                # Duplicate delivery. Roll back the failed INSERT so the implicit
                # transaction does not poison the next atomic claim.
                self.conn.rollback()
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

        device_obs.safe_observe("queue", device_obs.claim_called)
        if not self.storage.is_write_allowed("queue_db"):
            device_obs.safe_observe("queue", lambda: device_obs.claim_empty(False))
            return None

        started = time.perf_counter()
        now = datetime.now(
            timezone.utc
        )

        now_iso = now.isoformat()

        with self.lock:
            try:
                # Idle polling has nothing to expire or claim. A read under the
                # same lock avoids a write transaction. Any DELIVERED row, including
                # an expired one, still takes the durable BEGIN IMMEDIATE path below.
                pending = self.conn.execute(
                    "SELECT 1 FROM commands WHERE status='DELIVERED' LIMIT 1"
                ).fetchone()
                if pending is None:
                    device_obs.safe_observe("queue", lambda: device_obs.claim_empty(False))
                    return None

                self.conn.execute(
                    "BEGIN IMMEDIATE;"
                )
                device_obs.safe_observe("queue", device_obs.claim_tx_started)

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
                    device_obs.safe_observe("queue", lambda: device_obs.claim_empty(True))
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
                    device_obs.safe_observe("queue", device_obs.claim_rollback)
                    return None

                # T6: durable "I executed up to sequence N" written atomically with the claim.
                seq_row = self.conn.execute(
                    "SELECT sequence_no FROM commands WHERE id=?", (cmd_id,)
                ).fetchone()
                if seq_row and seq_row[0] is not None:
                    self.conn.execute(
                        """
                        INSERT INTO execution_meta (key, value)
                        VALUES ('last_executed_sequence', ?)
                        ON CONFLICT(key) DO UPDATE SET
                            value = CASE
                                WHEN CAST(excluded.value AS INTEGER) > CAST(value AS INTEGER)
                                THEN excluded.value ELSE value END
                        """,
                        (str(int(seq_row[0])),),
                    )

                self.conn.commit()
                device_obs.safe_observe("queue", lambda: device_obs.claim_command(
                    cmd_id, action, slot, time.perf_counter() - started,
                ))

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
                device_obs.safe_observe("queue", device_obs.claim_rollback)

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
                    action,
                    status
                FROM commands
                WHERE status IN ('EXECUTING', 'HARDWARE_VERIFIED', 'UNKNOWN_AFTER_REBOOT')
                ORDER BY COALESCE(started_at, hardware_verified_at, created_at) ASC
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

    def count_ack_attempt(self, cmd_id):
        with self.lock:
            try:
                self.conn.execute(
                    "UPDATE commands SET ack_attempts=ack_attempts+1 WHERE id=?", (str(cmd_id),)
                )
                self.conn.commit()
            except sqlite3.Error as exc:
                logger.warning("ack_attempts update failed: %s", exc)

    def get_ack_attempts(self, cmd_id):
        with self.lock:
            row = self.conn.execute(
                "SELECT ack_attempts, ack_status, ack_error FROM commands WHERE id=?", (str(cmd_id),)
            ).fetchone()
            return {"attempts": row[0], "ack_status": row[1], "ack_error": row[2]} if row else None

    def mark_ack_rejected(self, cmd_id, code):
        """Cloud answered 409 (genuine conflict): this ACK is non-retryable. Attempt count is kept."""
        if not self.storage.is_write_allowed("queue_db"):
            return False
        with self.lock:
            try:
                now = datetime.now(timezone.utc).isoformat()
                self.conn.execute(
                    """
                    UPDATE commands
                    SET ack_status='REJECTED', ack_error=?, acked_at=?
                    WHERE id=? AND ack_status='PENDING'
                    """,
                    (str(code)[:120], now, str(cmd_id)),
                )
                self.conn.commit()
                return True
            except sqlite3.Error as exc:
                logger.critical("Queue ACK reject update failed: %s", exc)
                return False

    # ============================================================
    # T6 EXECUTION IDENTITY (reported in every sync)
    # ============================================================

    EXECUTED_IDS_MAX = 50

    def get_last_executed_sequence(self):
        with self.lock:
            row = self.conn.execute(
                "SELECT value FROM execution_meta WHERE key='last_executed_sequence'"
            ).fetchone()
            return int(row[0]) if row else 0

    def get_executed_command_ids(self, limit=EXECUTED_IDS_MAX):
        """Bounded, most-recent-first list of commands that reached GPIO execution."""
        with self.lock:
            cur = self.conn.execute(
                """
                SELECT id FROM commands
                WHERE status IN ('EXECUTING', 'HARDWARE_VERIFIED', 'COMPLETED',
                                 'FAILED', 'UNKNOWN_AFTER_REBOOT')
                ORDER BY COALESCE(started_at, delivered_at) DESC
                LIMIT ?
                """,
                (int(limit),),
            )
            return [r[0] for r in cur.fetchall()]

    def get_cloud_attempt(self, cmd_id):
        with self.lock:
            row = self.conn.execute(
                "SELECT cloud_attempt FROM commands WHERE id=?", (str(cmd_id),)
            ).fetchone()
            return row[0] if row else None

    # ============================================================
    # T7 APPLIED CONFIGURATION (durable, on-change only)
    # ============================================================

    APPLIED_CONFIG_KEYS = (
        "applied_config_json",
        "applied_config_version",
        "applied_config_hash",
        "applied_config_at",
    )

    def get_applied_config(self):
        """Returns {json, version, hash, at} or None when nothing was ever applied."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT key, value FROM execution_meta WHERE key IN (?,?,?,?)",
                self.APPLIED_CONFIG_KEYS,
            ).fetchall()
        meta = {k: v for k, v in rows}
        if "applied_config_hash" not in meta or "applied_config_json" not in meta:
            return None
        try:
            version = int(meta.get("applied_config_version", 0))
        except (TypeError, ValueError):
            return None
        return {
            "json": meta["applied_config_json"],
            "version": version,
            "hash": meta["applied_config_hash"],
            "at": meta.get("applied_config_at"),
        }

    CAPABILITY_KEYS = ("applied_capability_json", "applied_capability_hash")

    def get_applied_capabilities(self):
        with self.lock:
            rows = self.conn.execute(
                "SELECT key, value FROM execution_meta WHERE key IN (?,?)",
                self.CAPABILITY_KEYS,
            ).fetchall()
        meta = {k: v for k, v in rows}
        if "applied_capability_json" not in meta:
            return None
        return {"json": meta["applied_capability_json"], "hash": meta.get("applied_capability_hash")}

    def persist_applied_capabilities(self, canonical_json, capability_hash):
        """On-change only. A matching hash does not rewrite flash."""
        with self.lock:
            current = self.conn.execute(
                "SELECT value FROM execution_meta WHERE key='applied_capability_hash'"
            ).fetchone()
            if current and current[0] == str(capability_hash):
                return True
            if not self.storage.is_write_allowed("queue_db"):
                return False
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
                for key, value in (
                    ("applied_capability_json", str(canonical_json)),
                    ("applied_capability_hash", str(capability_hash)),
                ):
                    self.conn.execute(
                        "INSERT INTO execution_meta (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value),
                    )
                self.conn.commit()
                return True
            except sqlite3.Error as exc:
                try:
                    self.conn.rollback()
                except Exception:
                    pass
                logger.critical("Applied-capability persistence failed: %s", exc)
                return False

    def persist_config_bundle(
        self,
        canonical_json,
        version,
        config_hash,
        applied_at,
        capability_json=None,
        capability_hash=None,
        include_config=True,
    ):
        """Write canonical config and capabilities in one SQLite transaction.

        synchronous=FULL is already set on this connection. A power loss before
        commit keeps the previous pair. A commit replaces only the keys that
        changed, together. An identical pair does not rewrite flash.
        """
        with self.lock:
            if self._bundle_matches(config_hash, version, capability_hash, include_config):
                return True
            if not self.storage.is_write_allowed("queue_db"):
                return False
            pairs = []
            if include_config:
                pairs.extend((
                    ("applied_config_json", str(canonical_json)),
                    ("applied_config_version", str(int(version))),
                    ("applied_config_hash", str(config_hash)),
                    ("applied_config_at", str(applied_at)),
                ))
            if capability_json is not None and capability_hash is not None:
                pairs.extend((
                    ("applied_capability_json", str(capability_json)),
                    ("applied_capability_hash", str(capability_hash)),
                ))
            if not pairs:
                return True
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
                for key, value in pairs:
                    self.conn.execute(
                        "INSERT INTO execution_meta (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value),
                    )
                self.conn.commit()
                return True
            except sqlite3.Error as exc:
                try:
                    self.conn.rollback()
                except Exception:
                    pass
                logger.critical("Configuration bundle persistence failed: %s", exc)
                return False

    def _bundle_matches(self, config_hash, version, capability_hash, include_config):
        if include_config:
            row = self.conn.execute(
                "SELECT value FROM execution_meta WHERE key='applied_config_hash'"
            ).fetchone()
            ver = self.conn.execute(
                "SELECT value FROM execution_meta WHERE key='applied_config_version'"
            ).fetchone()
            if not row or row[0] != str(config_hash) or not ver or ver[0] != str(int(version)):
                return False
        if capability_hash is not None:
            current = self.conn.execute(
                "SELECT value FROM execution_meta WHERE key='applied_capability_hash'"
            ).fetchone()
            if not current or current[0] != str(capability_hash):
                return False
        return include_config or capability_hash is not None

    def persist_applied_config(self, canonical_json, version, config_hash, applied_at):
        """Single transaction: effective configuration + its identity land together
        (synchronous=FULL), so a power cut can never leave hash without config."""
        if not self.storage.is_write_allowed("queue_db"):
            return False
        with self.lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
                for key, value in (
                    ("applied_config_json", str(canonical_json)),
                    ("applied_config_version", str(int(version))),
                    ("applied_config_hash", str(config_hash)),
                    ("applied_config_at", str(applied_at)),
                ):
                    self.conn.execute(
                        "INSERT INTO execution_meta (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value),
                    )
                self.conn.commit()
                return True
            except sqlite3.Error as exc:
                try:
                    self.conn.rollback()
                except Exception:
                    pass
                logger.critical("Applied-config persistence failed: %s", exc)
                return False

    # ============================================================
    # CLEANUP (Phase 0.5 retention)
    # ============================================================

    # Retain ACKED history for 30 days OR the newest 500 ACKED rows,
    # whichever keeps MORE. Only ACKED rows ever count or get deleted.
    ACKED_RETENTION_DAYS = 30
    ACKED_RETENTION_ROWS = 500
    CLEANUP_BATCH_LIMIT = 100

    def cleanup_acked(self, now=None):
        """
        Deletes only acknowledged history beyond the retention policy.

        KEEP an ACKED row if:
            acked_at >= now - 30 days
            OR it ranks within the newest 500 ACKED rows.

        Deletion is bounded to CLEANUP_BATCH_LIMIT rows per call so a
        large backlog drains gradually over hourly runs. Returns the
        number of rows deleted.

        Safety rule:
        NEVER delete DELIVERED / EXECUTING /
        UNKNOWN_AFTER_REBOOT / HARDWARE_VERIFIED /
        unacknowledged final commands.
        """

        if not self.storage.is_write_allowed("queue_db"):
            return 0

        if now is None:
            now = datetime.now(timezone.utc)

        cutoff = (
            now - timedelta(days=self.ACKED_RETENTION_DAYS)
        ).isoformat()

        with self.lock:
            try:
                # acked_at of the 500th-newest ACKED row (NULL when fewer
                # than 500 exist -> comparison is NULL -> nothing deleted).
                deleted = self.conn.execute(
                    """
                    DELETE FROM commands
                    WHERE id IN (
                        SELECT id
                        FROM commands
                        WHERE ack_status IN ('ACKED','REJECTED')
                          AND acked_at IS NOT NULL
                          AND acked_at < ?
                          AND acked_at < (
                              SELECT acked_at
                              FROM commands
                              WHERE ack_status IN ('ACKED','REJECTED')
                                AND acked_at IS NOT NULL
                              ORDER BY acked_at DESC
                              LIMIT 1 OFFSET ?
                          )
                        ORDER BY acked_at ASC
                        LIMIT ?
                    )
                    """,
                    (
                        cutoff,
                        self.ACKED_RETENTION_ROWS - 1,
                        self.CLEANUP_BATCH_LIMIT,
                    ),
                ).rowcount

                self.conn.commit()

                if deleted > 0:
                    # Passive means:
                    # do not force a large blocking checkpoint.
                    # Only issued when history actually changed.
                    self.conn.execute(
                        "PRAGMA wal_checkpoint(PASSIVE);"
                    )

                return max(0, deleted)

            except sqlite3.Error as exc:
                try:
                    self.conn.rollback()
                except Exception:
                    pass
                logger.error(
                    "Queue cleanup failed: %s",
                    exc,
                )
                return 0

    def count_acked(self):
        with self.lock:
            return self.conn.execute(
                "SELECT COUNT(*) FROM commands WHERE ack_status IN ('ACKED','REJECTED')"
            ).fetchone()[0]

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