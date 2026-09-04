import csv
import os
import threading
import time
from datetime import datetime, timezone

from config import (
    DATA_DIR,
    LOG_DIR,
    TELEMETRY_DIR,
    HEALTH_DIR,
    STORAGE_METRICS_INTERVAL_S,
    STORAGE_HEALTH_PERSIST_INTERVAL_S,
    STORAGE_DAILY_PERSIST_INTERVAL_S,
    STORAGE_WARNING_PERCENT,
    STORAGE_CLEANUP_PERCENT,
    STORAGE_REDUCED_PERCENT,
    STORAGE_PROTECTED_PERCENT,
    STORAGE_CRITICAL_PERCENT,
)

from logger import logger, set_storage_manager
from storage_io_manager import StorageIOMeter
from memory_manager import MemoryManager
from resource_guard import ResourceGuard


class StorageManager:
    """
    Central resource supervisor.

    Monitoring is RAM-only at high frequency.
    USB persistence is deliberately low frequency.
    """

    NORMAL_LOG_RETENTION_DAYS = 30
    CRITICAL_LOG_RETENTION_DAYS = 365
    TELEMETRY_RETENTION_DAYS = 365
    DIAGNOSTIC_RETENTION_DAYS = 30

    def __init__(self):
        self.io_meter = StorageIOMeter()
        self.memory_monitor = MemoryManager()

        self.guard = ResourceGuard(
            self.io_meter,
            self.memory_monitor,
        )

        # Connect logger before monitor thread starts.
        set_storage_manager(self)

        self._running = True
        self._last_state = None
        self._last_health_persist = 0.0
        self._last_daily_persist_date = None

        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="EMS-ResourceMonitor",
            daemon=True,
        )

        self.monitor_thread.start()

    # ------------------------------------------------------------
    # WRITE POLICY
    # ------------------------------------------------------------

    def is_write_allowed(self, category):
        return self.guard.is_write_allowed(
            category
        )

    # ------------------------------------------------------------
    # MONITOR
    # ------------------------------------------------------------

    def _monitor_loop(self):
        while self._running:
            try:
                self.io_meter.update_metrics()
                self.memory_monitor.update_metrics()

                previous = self.guard.get_state()
                current = self.guard.evaluate_state()

                if current != previous:
                    self._handle_resource_transition(
                        previous,
                        current,
                    )

                now = time.monotonic()

                if (
                    now - self._last_health_persist
                    >= STORAGE_HEALTH_PERSIST_INTERVAL_S
                ):
                    if self.is_write_allowed(
                        "telemetry"
                    ):
                        self.persist_health()

                    self._last_health_persist = now

                today = datetime.now(
                    timezone.utc
                ).strftime("%Y-%m-%d")

                if (
                    today != self._last_daily_persist_date
                    and self.is_write_allowed(
                        "telemetry"
                    )
                ):
                    self.save_daily_telemetry()
                    self._last_daily_persist_date = today

                self.cleanup_if_needed()

            except Exception as exc:
                logger.critical(
                    "Resource monitor failure: %s",
                    exc,
                )

            time.sleep(
                STORAGE_METRICS_INTERVAL_S
            )

    # ------------------------------------------------------------
    # STATE TRANSITIONS
    # ------------------------------------------------------------

    def _handle_resource_transition(
        self,
        previous,
        current,
    ):
        logger.critical(
            "RESOURCE STATE: %s -> %s",
            previous,
            current,
        )

    # ------------------------------------------------------------
    # HEALTH SNAPSHOT
    # ------------------------------------------------------------

    def persist_health(self):
        if not self.is_write_allowed(
            "telemetry"
        ):
            return

        metrics = self.io_meter.get_metrics()
        memory = self.memory_monitor.get_health_summary()

        path = os.path.join(
            HEALTH_DIR,
            "resource_health.csv",
        )

        exists = os.path.exists(path)

        try:
            with open(
                path,
                "a",
                newline="",
                encoding="utf-8",
            ) as fh:
                writer = csv.writer(fh)

                if not exists:
                    writer.writerow([
                        "timestamp",
                        "storage_state",
                        "used_percent",
                        "free_bytes",
                        "daily_physical_reads",
                        "daily_physical_writes",
                        "estimated_block_io_ratio",
                        "memory_state",
                        "ram_used_percent",
                        "ram_available_kb",
                        "swap_used_kb",
                        "oom_kills",
                        "ems_rss_kb",
                        "ems_vms_kb",
                        "threads",
                        "fds",
                    ])

                writer.writerow([
                    datetime.now(
                        timezone.utc
                    ).isoformat(),

                    self.guard.get_state(),

                    metrics["used_percent"],
                    metrics["storage_free_bytes"],

                    metrics[
                        "daily_physical_reads"
                    ],

                    metrics[
                        "daily_physical_writes"
                    ],

                    metrics[
                        "estimated_block_io_ratio"
                    ],

                    memory["state"],
                    memory["ram_used_percent"],
                    memory["ram_available_kb"],
                    memory["swap_used_kb"],
                    memory["oom_kills"],
                    memory["rss_kb"],
                    memory["vms_kb"],
                    memory["threads"],
                    memory["fds"],
                ])

            self.io_meter.record_ems_write(
                "telemetry",
                512,
            )

        except OSError as exc:
            logger.critical(
                "Health telemetry write failed: %s",
                exc,
            )

    # ------------------------------------------------------------
    # DAILY TELEMETRY
    # ------------------------------------------------------------

    def save_daily_telemetry(self):
        if not self.is_write_allowed(
            "telemetry"
        ):
            return

        metrics = self.io_meter.get_metrics()
        memory = self.memory_monitor.get_metrics()

        path = os.path.join(
            TELEMETRY_DIR,
            "daily_resource.csv",
        )

        exists = os.path.exists(path)

        try:
            with open(
                path,
                "a",
                newline="",
                encoding="utf-8",
            ) as fh:
                writer = csv.writer(fh)

                if not exists:
                    writer.writerow([
                        "timestamp",
                        "daily_logical_writes",
                        "daily_physical_writes",
                        "daily_physical_reads",
                        "estimated_block_io_ratio",
                        "ram_used_kb",
                        "swap_used_kb",
                        "ems_rss_kb",
                        "ems_vms_kb",
                        "memory_state",
                    ])

                row = [
                    datetime.now(
                        timezone.utc
                    ).isoformat(),

                    metrics[
                        "daily_logical_total"
                    ],

                    metrics[
                        "daily_physical_writes"
                    ],

                    metrics[
                        "daily_physical_reads"
                    ],

                    metrics[
                        "estimated_block_io_ratio"
                    ],

                    memory["ram_used_kb"],
                    memory["swap_used_kb"],
                    memory["ems_rss_kb"],
                    memory["ems_vms_kb"],
                    memory["memory_state"],
                ]

                writer.writerow(row)

            # Record ONLY the bytes actually appended.
            self.io_meter.record_ems_write(
                "telemetry",
                512,
            )

        except OSError as exc:
            logger.error(
                "Daily telemetry failed: %s",
                exc,
            )

    # ------------------------------------------------------------
    # CLEANUP
    # ------------------------------------------------------------

    @staticmethod
    def _file_age_seconds(path):
        try:
            return (
                time.time()
                - os.path.getmtime(path)
            )
        except OSError:
            return 0

    def _delete_old_files(
        self,
        directory,
        retention_days,
        exclude_prefixes=(),
    ):
        if not os.path.isdir(directory):
            return

        cutoff = retention_days * 86400

        for name in os.listdir(directory):
            path = os.path.join(
                directory,
                name,
            )

            if not os.path.isfile(path):
                continue

            if any(
                name.startswith(prefix)
                for prefix in exclude_prefixes
            ):
                continue

            if (
                self._file_age_seconds(path)
                > cutoff
            ):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def cleanup_if_needed(self):
        try:
            metrics = self.io_meter.get_metrics()
            used = metrics["used_percent"]

            if used < STORAGE_CLEANUP_PERCENT:
                return

            # NEVER delete critical logs here.
            self._delete_old_files(
                LOG_DIR,
                self.NORMAL_LOG_RETENTION_DAYS,
                exclude_prefixes=(
                    "critical.log",
                ),
            )

            self._delete_old_files(
                TELEMETRY_DIR,
                self.TELEMETRY_RETENTION_DAYS,
            )

            self._delete_old_files(
                os.path.join(
                    DATA_DIR,
                    "diagnostics",
                ),
                self.DIAGNOSTIC_RETENTION_DAYS,
            )

            if used >= STORAGE_REDUCED_PERCENT:
                logger.warning(
                    "Storage usage %.1f%%; "
                    "cleanup performed.",
                    used,
                )

            if used >= STORAGE_PROTECTED_PERCENT:
                logger.critical(
                    "Storage %.1f%% full: "
                    "protected write mode.",
                    used,
                )

            if used >= STORAGE_CRITICAL_PERCENT:
                logger.critical(
                    "Storage %.1f%% full: "
                    "CRITICAL protection active.",
                    used,
                )

        except Exception as exc:
            logger.critical(
                "Storage cleanup failed: %s",
                exc,
            )

    # ------------------------------------------------------------
    # LOW-FREQUENCY PERSISTENCE
    # ------------------------------------------------------------

    def persist_counters(self):
        if not self.is_write_allowed(
            "telemetry"
        ):
            return

        self.io_meter.persist_counters()

    # ------------------------------------------------------------
    # SHUTDOWN
    # ------------------------------------------------------------

    def stop(self):
        self._running = False

        try:
            self.persist_counters()
        except Exception:
            pass