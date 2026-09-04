import json
import os
import subprocess
import threading
from datetime import datetime, timezone

from config import (
    DATA_DIR,
    TELEMETRY_DIR,
    HEALTH_DIR,
    TOTAL_DAILY_PHYSICAL_BUDGET_BYTES,
)
from logger import logger


class StorageIOMeter:
    """
    Measures block-device I/O.

    IMPORTANT:
    /proc/diskstats does NOT expose NAND P/E cycles.
    Therefore this class reports block I/O and an estimated ratio,
    not true NAND wear.
    """

    def __init__(self):
        self.lock = threading.Lock()

        self.device = self._get_device_name(DATA_DIR)
        self.device_serial = self._get_device_serial()

        self.epoch_file = os.path.join(
            TELEMETRY_DIR,
            "storage_epoch.json",
        )

        self.lifetime_file = os.path.join(
            HEALTH_DIR,
            "storage_lifetime.json",
        )

        self.history_file = os.path.join(
            HEALTH_DIR,
            "storage_device_history.json",
        )

        self.state = self._load_json(self.epoch_file)
        self.lifetime_state = self._load_json(
            self.lifetime_file
        )

        self.lifetime_state.setdefault(
            "lifetime_logical_ems_writes",
            0,
        )
        self.lifetime_state.setdefault(
            "lifetime_block_writes",
            0,
        )
        self.lifetime_state.setdefault(
            "lifetime_block_reads",
            0,
        )

        current_reads, current_writes = self._read_diskstats()

        today = datetime.now(
            timezone.utc
        ).strftime("%Y-%m-%d")

        stored_date = self.state.get("date")
        stored_serial = self.state.get("device_serial")

        device_changed = (
            stored_serial
            and stored_serial != self.device_serial
        )

        counter_reset = (
            current_writes
            < self.state.get("baseline_block_writes", 0)
        )

        if device_changed:
            self._archive_previous_device()

        if (
            stored_date != today
            or device_changed
            or counter_reset
        ):
            self.state = {
                "date": today,
                "device": self.device,
                "device_serial": self.device_serial,

                "baseline_block_reads": current_reads,
                "baseline_block_writes": current_writes,

                "logical_writes": {
                    "normal_log": 0,
                    "critical_log": 0,
                    "state": 0,
                    "queue_db": 0,
                    "telemetry": 0,
                    "diagnostics": 0,
                    "other": 0,
                },
            }

            self._atomic_write_json(
                self.epoch_file,
                self.state,
            )

    # ------------------------------------------------------------
    # DEVICE IDENTIFICATION
    # ------------------------------------------------------------

    @staticmethod
    def _get_device_name(path):
        try:
            result = subprocess.run(
                ["df", "-P", path],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )

            lines = result.stdout.strip().splitlines()

            if len(lines) < 2:
                return None

            filesystem = lines[1].split()[0]

            base = os.path.basename(filesystem)

            if base.startswith("mmcblk"):
                if "p" in base:
                    return base.rsplit("p", 1)[0]
                return base

            if base.startswith("nvme"):
                # nvme0n1p2 -> nvme0n1
                if "p" in base:
                    return base.rsplit("p", 1)[0]
                return base

            if base.startswith("sd"):
                # sda1 -> sda
                return base.rstrip("0123456789")

            return base

        except Exception:
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
            )

            serial = result.stdout.strip()

            return serial or "UNKNOWN"

        except Exception:
            return "UNKNOWN"

    # ------------------------------------------------------------
    # DISKSTATS
    # ------------------------------------------------------------

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

                    if len(parts) < 10:
                        continue

                    if parts[2] != self.device:
                        continue

                    # Linux diskstats:
                    # field 6 = sectors read
                    # field 10 = sectors written
                    sectors_read = int(parts[5])
                    sectors_written = int(parts[9])

                    return (
                        sectors_read * 512,
                        sectors_written * 512,
                    )

        except (OSError, ValueError):
            pass

        return 0, 0

    # ------------------------------------------------------------
    # JSON
    # ------------------------------------------------------------

    @staticmethod
    def _load_json(path):
        try:
            with open(
                path,
                "r",
                encoding="utf-8",
            ) as fh:
                return json.load(fh)
        except Exception:
            return {}

    @staticmethod
    def _atomic_write_json(path, data):
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)

        temp_path = path + ".tmp"

        payload = json.dumps(
            data,
            separators=(",", ":"),
            sort_keys=True,
        )

        with open(
            temp_path,
            "w",
            encoding="utf-8",
        ) as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())

        os.replace(temp_path, path)

        dir_fd = os.open(
            directory,
            os.O_DIRECTORY,
        )

        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    # ------------------------------------------------------------
    # DEVICE REPLACEMENT
    # ------------------------------------------------------------

    def _archive_previous_device(self):
        try:
            history = self._load_json(
                self.history_file
            )

            if not isinstance(history, list):
                history = []

            history.append({
                "serial": self.state.get(
                    "device_serial"
                ),
                "device": self.state.get(
                    "device"
                ),
                "archived_at": datetime.now(
                    timezone.utc
                ).isoformat(),
                "lifetime_logical_ems_writes": (
                    self.lifetime_state.get(
                        "lifetime_logical_ems_writes",
                        0,
                    )
                ),
                "lifetime_block_writes": (
                    self.lifetime_state.get(
                        "lifetime_block_writes",
                        0,
                    )
                ),
                "lifetime_block_reads": (
                    self.lifetime_state.get(
                        "lifetime_block_reads",
                        0,
                    )
                ),
            })

            self._atomic_write_json(
                self.history_file,
                history[-20:],
            )

            logger.critical(
                "STORAGE_DEVICE_CHANGED: "
                "storage identity changed."
            )

        except Exception as exc:
            logger.critical(
                "Failed to archive storage device: %s",
                exc,
            )

    # ------------------------------------------------------------
    # LOGICAL WRITE ACCOUNTING
    # ------------------------------------------------------------

    def record_ems_write(
        self,
        category: str,
        bytes_written: int,
    ):
        if bytes_written <= 0:
            return

        with self.lock:
            logical = self.state.setdefault(
                "logical_writes",
                {},
            )

            if category not in logical:
                category = "other"

            logical[category] = (
                logical.get(category, 0)
                + int(bytes_written)
            )

    # ------------------------------------------------------------
    # METRICS
    # ------------------------------------------------------------

    def update_metrics(self):
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

            daily_reads = max(
                0,
                current_reads - baseline_reads,
            )

            daily_writes = max(
                0,
                current_writes - baseline_writes,
            )

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

                daily_writes = 0
                daily_reads = 0

            return {
                "daily_physical_reads": daily_reads,
                "daily_physical_writes": daily_writes,
            }

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

            daily_reads = max(
                0,
                current_reads - baseline_reads,
            )

            daily_writes = max(
                0,
                current_writes - baseline_writes,
            )

            logical = dict(
                self.state.get(
                    "logical_writes",
                    {},
                )
            )

            logical_total = sum(
                logical.values()
            )

            estimated_ratio = (
                daily_writes / logical_total
                if logical_total > 0
                else 0.0
            )

            try:
                stat = os.statvfs(DATA_DIR)

                total_bytes = (
                    stat.f_blocks
                    * stat.f_frsize
                )

                free_bytes = (
                    stat.f_bavail
                    * stat.f_frsize
                )

                used_percent = (
                    ((total_bytes - free_bytes)
                     / total_bytes)
                    * 100.0
                    if total_bytes > 0
                    else 100.0
                )

                storage_ok = True

            except OSError:
                total_bytes = 0
                free_bytes = 0
                used_percent = 100.0
                storage_ok = False

            budget_exceeded = (
                daily_writes
                >= TOTAL_DAILY_PHYSICAL_BUDGET_BYTES
            )

            return {
                "device": self.device,
                "device_serial": self.device_serial,

                "daily_logical_writes": logical,
                "daily_logical_total": logical_total,

                "daily_physical_reads": daily_reads,
                "daily_physical_writes": daily_writes,

                # Deliberately NOT called true WAF.
                "estimated_block_io_ratio": round(
                    estimated_ratio,
                    3,
                ),

                "storage_total_bytes": total_bytes,
                "storage_free_bytes": free_bytes,
                "used_percent": round(
                    used_percent,
                    2,
                ),

                "storage_ok": storage_ok,
                "budget_exceeded": budget_exceeded,

                "lifetime_logical_ems_writes": (
                    self.lifetime_state.get(
                        "lifetime_logical_ems_writes",
                        0,
                    )
                    + logical_total
                ),

                "lifetime_block_writes": (
                    self.lifetime_state.get(
                        "lifetime_block_writes",
                        0,
                    )
                    + daily_writes
                ),

                "lifetime_block_reads": (
                    self.lifetime_state.get(
                        "lifetime_block_reads",
                        0,
                    )
                    + daily_reads
                ),
            }

    # ------------------------------------------------------------
    # PERSIST LOW-FREQUENCY COUNTERS
    # ------------------------------------------------------------

    def persist_counters(self):
        with self.lock:
            metrics = self.get_metrics()

            self.lifetime_state[
                "lifetime_logical_ems_writes"
            ] = metrics[
                "lifetime_logical_ems_writes"
            ]

            self.lifetime_state[
                "lifetime_block_writes"
            ] = metrics[
                "lifetime_block_writes"
            ]

            self.lifetime_state[
                "lifetime_block_reads"
            ] = metrics[
                "lifetime_block_reads"
            ]

            self._atomic_write_json(
                self.lifetime_file,
                self.lifetime_state,
            )

            self._atomic_write_json(
                self.epoch_file,
                self.state,
            )