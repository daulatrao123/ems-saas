import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone

from config import (
    DATA_DIR,
    TELEMETRY_DIR,
    HEALTH_DIR,
    TOTAL_DAILY_PHYSICAL_BUDGET_BYTES,
)

from logger import logger


DEFAULT_LOGICAL_COUNTERS = {
    "normal_log": 0,
    "critical_log": 0,
    "state": 0,
    "queue_db": 0,
    "telemetry": 0,
    "diagnostics": 0,
    "other": 0,
}


class StorageIOMeter:
    """
    Storage accounting.

    IMPORTANT:
    /proc/diskstats reports block-device I/O.
    It does NOT directly report NAND wear.

    Therefore:
        physical_* = block-device I/O observed by Linux
        logical_*  = bytes intentionally written by EMS
        block_io_ratio = observed block writes / EMS logical writes

    block_io_ratio must NOT be described as true NAND WAF.
    """

    def __init__(self):

        self.lock = threading.RLock()

        self.device = (
            self._get_device_name(
                DATA_DIR
            )
        )

        self.device_serial = (
            self._get_device_serial()
        )

        self.epoch_file = os.path.join(
            TELEMETRY_DIR,
            "storage_epoch.json",
        )

        self.lifetime_file = os.path.join(
            TELEMETRY_DIR,
            "lifetime_counters.json",
        )

        self.history_file = os.path.join(
            TELEMETRY_DIR,
            "epoch_history.json",
        )

        self.last_wal_size = 0

        self.last_persist_time = time.monotonic()

        self.state = self._load_json(
            self.epoch_file,
            {},
        )

        self.lifetime_state = self._load_json(
            self.lifetime_file,
            {
                "lifetime_logical_writes": 0,
                "lifetime_block_writes": 0,
                "lifetime_block_reads": 0,
            },
        )

        current_reads, current_writes = (
            self._read_diskstats()
        )

        today = datetime.now().strftime(
            "%Y-%m-%d"
        )

        previous_device = self.state.get(
            "device_serial"
        )

        previous_date = self.state.get(
            "date"
        )

        previous_baseline = self.state.get(
            "baseline_block_writes",
            0,
        )

        device_changed = (
            previous_device
            and
            previous_device != self.device_serial
        )

        counter_reset = (
            previous_baseline > 0
            and
            current_writes < previous_baseline
        )

        if device_changed:

            logger.critical(
                "STORAGE_DEVICE_CHANGED: "
                "storage identity changed."
            )

            self._archive_epoch()

        if (
            previous_date != today
            or device_changed
            or counter_reset
        ):

            self.state = {
                "date": today,
                "device_serial": self.device_serial,
                "device": self.device,
                "baseline_block_reads": current_reads,
                "baseline_block_writes": current_writes,
                "logical_writes":
                    dict(DEFAULT_LOGICAL_COUNTERS),
            }

            self._atomic_save(
                self.epoch_file,
                self.state,
            )

    # ============================================================
    # DEVICE
    # ============================================================

    @staticmethod
    def _get_device_name(path):

        try:

            result = subprocess.run(
                [
                    "df",
                    "-P",
                    path,
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )

            lines = result.stdout.strip().splitlines()

            if len(lines) < 2:
                return None

            filesystem = (
                lines[1].split()[0]
            )

            base = os.path.basename(
                filesystem
            )

            if base.startswith(
                "mmcblk"
            ):

                if base.endswith(
                    "p1"
                ) or base.endswith(
                    "p2"
                ):
                    base = base[:-2]

                return base

            if base.startswith("nvme"):

                # nvme0n1p2 -> nvme0n1
                if "p" in base:
                    base = base.split(
                        "p",
                        1,
                    )[0]

                return base

            if base.startswith("sd"):

                return base[:3]

        except Exception:
            pass

        return None

    def _get_device_serial(self):

        if not self.device:
            return "UNKNOWN"

        try:

            result = subprocess.run(
                [
                    "lsblk",
                    "-n",
                    "-o",
                    "SERIAL",
                    f"/dev/{self.device}",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            serial = (
                result.stdout.strip()
            )

            return serial or "UNKNOWN"

        except Exception:
            return "UNKNOWN"

    # ============================================================
    # DISKSTATS
    # ============================================================

    def _read_diskstats(self):

        if not self.device:
            return 0, 0

        try:

            with open(
                "/proc/diskstats",
                "r",
                encoding="utf-8",
            ) as fh:

                for line in fh:

                    parts = line.split()

                    if len(parts) < 14:
                        continue

                    if parts[2] != self.device:
                        continue

                    # Linux diskstats:
                    # field 6 = sectors read
                    # field 10 = sectors written

                    sectors_read = int(
                        parts[5]
                    )

                    sectors_written = int(
                        parts[9]
                    )

                    return (
                        sectors_read * 512,
                        sectors_written * 512,
                    )

        except Exception:
            pass

        return 0, 0

    # ============================================================
    # JSON
    # ============================================================

    @staticmethod
    def _load_json(
        path,
        default,
    ):

        try:

            with open(
                path,
                "r",
                encoding="utf-8",
            ) as fh:

                return json.load(fh)

        except Exception:
            return default

    @staticmethod
    def _atomic_save(
        path,
        data,
    ):

        directory = os.path.dirname(
            path
        )

        os.makedirs(
            directory,
            exist_ok=True,
        )

        temp = (
            path
            + ".tmp"
        )

        try:

            encoded = json.dumps(
                data,
                sort_keys=True,
                separators=(
                    ",",
                    ":",
                ),
            ).encode(
                "utf-8"
            )

            with open(
                temp,
                "wb",
            ) as fh:

                fh.write(encoded)
                fh.flush()
                os.fsync(
                    fh.fileno()
                )

            os.replace(
                temp,
                path,
            )

            dir_fd = os.open(
                directory,
                os.O_DIRECTORY,
            )

            try:
                os.fsync(
                    dir_fd
                )
            finally:
                os.close(
                    dir_fd
                )

            return True

        except Exception as exc:

            try:
                os.unlink(
                    temp
                )
            except OSError:
                pass

            logger.error(
                "Storage metrics persistence failed: %s",
                exc,
            )

            return False

    # ============================================================
    # EPOCH ARCHIVE
    # ============================================================

    def _archive_epoch(self):

        try:

            history = self._load_json(
                self.history_file,
                [],
            )

            if not isinstance(
                history,
                list,
            ):
                history = []

            history.append(
                {
                    "serial":
                        self.state.get(
                            "device_serial"
                        ),

                    "device":
                        self.state.get(
                            "device"
                        ),

                    "archived_at":
                        datetime.now(
                            timezone.utc
                        ).isoformat(),

                    "logical_writes":
                        dict(
                            self.state.get(
                                "logical_writes",
                                {},
                            )
                        ),

                    "lifetime":
                        dict(
                            self.lifetime_state
                        ),
                }
            )

            # Prevent this metadata file itself from growing forever.
            history = history[-100:]

            self._atomic_save(
                self.history_file,
                history,
            )

        except Exception as exc:

            logger.error(
                "Storage epoch archive failed: %s",
                exc,
            )

    # ============================================================
    # LOGICAL WRITE ACCOUNTING
    # ============================================================

    def record_ems_write(
        self,
        category,
        bytes_written,
    ):

        if bytes_written <= 0:
            return

        with self.lock:

            if category not in (
                self.state[
                    "logical_writes"
                ]
            ):

                category = "other"

            self.state[
                "logical_writes"
            ][
                category
            ] += int(
                bytes_written
            )

    # ============================================================
    # SQLITE
    # ============================================================

    def get_sqlite_stats(self):

        db_file = os.path.join(
            DATA_DIR,
            "queue",
            "ems_queue.sqlite",
        )

        wal_file = (
            db_file
            + "-wal"
        )

        shm_file = (
            db_file
            + "-shm"
        )

        def size(path):

            try:
                return os.path.getsize(
                    path
                )
            except OSError:
                return 0

        db_size = size(
            db_file
        )

        wal_size = size(
            wal_file
        )

        shm_size = size(
            shm_file
        )

        wal_delta = max(
            0,
            wal_size
            - self.last_wal_size,
        )

        self.last_wal_size = (
            wal_size
        )

        return {
            "db_size": db_size,
            "wal_size": wal_size,
            "shm_size": shm_size,
            "wal_delta": wal_delta,
        }

    # ============================================================
    # PERSIST COUNTERS
    # ============================================================

    def persist_counters(
        self,
        force=False,
    ):

        with self.lock:

            now = time.monotonic()

            if (
                not force
                and
                (
                    now
                    - self.last_persist_time
                )
                < 3600
            ):
                return False

            current_reads, current_writes = (
                self._read_diskstats()
            )

            baseline_reads = self.state.get(
                "baseline_block_reads",
                current_reads,
            )

            baseline_writes = self.state.get(
                "baseline_block_writes",
                current_writes,
            )

            block_reads = max(
                0,
                current_reads
                - baseline_reads,
            )

            block_writes = max(
                0,
                current_writes
                - baseline_writes,
            )

            logical = sum(
                self.state.get(
                    "logical_writes",
                    {},
                ).values()
            )

            self.lifetime_state[
                "lifetime_logical_writes"
            ] += logical

            self.lifetime_state[
                "lifetime_block_reads"
            ] += block_reads

            self.lifetime_state[
                "lifetime_block_writes"
            ] += block_writes

            self.state[
                "baseline_block_reads"
            ] = current_reads

            self.state[
                "baseline_block_writes"
            ] = current_writes

            self.state[
                "logical_writes"
            ] = dict(
                DEFAULT_LOGICAL_COUNTERS
            )

            self._atomic_save(
                self.lifetime_file,
                self.lifetime_state,
            )

            self._atomic_save(
                self.epoch_file,
                self.state,
            )

            self.last_persist_time = now

            return True

    # ============================================================
    # DAILY ROTATION
    # ============================================================

    def _rotate_day_if_needed(self):

        today = datetime.now().strftime(
            "%Y-%m-%d"
        )

        if self.state.get(
            "date"
        ) == today:
            return

        self.persist_counters(
            force=True
        )

        current_reads, current_writes = (
            self._read_diskstats()
        )

        self.state = {
            "date": today,
            "device_serial":
                self.device_serial,
            "device":
                self.device,
            "baseline_block_reads":
                current_reads,
            "baseline_block_writes":
                current_writes,
            "logical_writes":
                dict(
                    DEFAULT_LOGICAL_COUNTERS
                ),
        }

        self._atomic_save(
            self.epoch_file,
            self.state,
        )

    # ============================================================
    # UPDATE
    # ============================================================

    def update_metrics(self):

        with self.lock:

            self._rotate_day_if_needed()

            current_reads, current_writes = (
                self._read_diskstats()
            )

            baseline_reads = self.state.get(
                "baseline_block_reads",
                current_reads,
            )

            baseline_writes = self.state.get(
                "baseline_block_writes",
                current_writes,
            )

            # Detect counter reset.
            if (
                current_writes
                < baseline_writes
            ):

                self.state[
                    "baseline_block_writes"
                ] = current_writes

                self.state[
                    "baseline_block_reads"
                ] = current_reads

                self._atomic_save(
                    self.epoch_file,
                    self.state,
                )

    # ============================================================
    # METRICS
    # ============================================================

    def get_metrics(self):

        with self.lock:

            current_reads, current_writes = (
                self._read_diskstats()
            )

            baseline_reads = self.state.get(
                "baseline_block_reads",
                current_reads,
            )

            baseline_writes = self.state.get(
                "baseline_block_writes",
                current_writes,
            )

            block_reads = max(
                0,
                current_reads
                - baseline_reads,
            )

            block_writes = max(
                0,
                current_writes
                - baseline_writes,
            )

            logical_dict = self.state.get(
                "logical_writes",
                {},
            )

            logical_writes = sum(
                logical_dict.values()
            )

            sqlite = (
                self.get_sqlite_stats()
            )

            # This is intentionally NOT called WAF.
            block_io_ratio = (
                block_writes
                / logical_writes
                if logical_writes > 0
                else 0
            )

            try:

                stat = os.statvfs(
                    DATA_DIR
                )

                total = (
                    stat.f_blocks
                    * stat.f_frsize
                )

                free = (
                    stat.f_bavail
                    * stat.f_frsize
                )

                used_percent = (
                    (
                        total
                        - free
                    )
                    / total
                    * 100
                    if total > 0
                    else 100
                )

                storage_ok = True

            except Exception:

                used_percent = 100
                storage_ok = False

            budget_exceeded = (
                block_writes
                >
                TOTAL_DAILY_PHYSICAL_BUDGET_BYTES
            )

            return {
                "device":
                    self.device,

                "device_serial":
                    self.device_serial,

                "daily_logical_writes":
                    logical_writes,

                "logical_write_breakdown":
                    dict(logical_dict),

                "daily_block_writes":
                    block_writes,

                "daily_block_reads":
                    block_reads,

                "block_io_ratio":
                    round(
                        block_io_ratio,
                        2,
                    ),

                "sqlite_db_size":
                    sqlite["db_size"],

                "sqlite_wal_size":
                    sqlite["wal_size"],

                "sqlite_shm_size":
                    sqlite["shm_size"],

                "sqlite_wal_delta":
                    sqlite["wal_delta"],

                "budget_exceeded":
                    budget_exceeded,

                "used_percent":
                    used_percent,

                "storage_ok":
                    storage_ok,

                "lifetime_block_writes":
                    self.lifetime_state.get(
                        "lifetime_block_writes",
                        0,
                    )
                    + block_writes,

                "lifetime_block_reads":
                    self.lifetime_state.get(
                        "lifetime_block_reads",
                        0,
                    )
                    + block_reads,
            }