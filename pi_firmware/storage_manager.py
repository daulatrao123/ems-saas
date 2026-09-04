import glob
import os
import threading
import time
from datetime import datetime

from config import (
    DATA_DIR,
    LOG_DIR,
    TELEMETRY_DIR,
    NORMAL_LOG_RETENTION_DAYS,
    CRITICAL_LOG_RETENTION_DAYS,
    TELEMETRY_RETENTION_DAYS,
    DIAGNOSTIC_RETENTION_DAYS,
    STORAGE_METRICS_INTERVAL_S,
)

from logger import (
    logger,
    set_storage_manager,
)

from storage_io_manager import (
    StorageIOMeter,
)

from memory_manager import (
    MemoryManager,
)

from resource_guard import (
    ResourceGuard,
)


class StorageManager:

    def __init__(self):

        self.io_meter = (
            StorageIOMeter()
        )

        self.memory_monitor = (
            MemoryManager()
        )

        self.guard = (
            ResourceGuard(
                self.io_meter,
                self.memory_monitor,
            )
        )

        # Connect logger before starting monitor thread.
        set_storage_manager(
            self
        )

        self._running = True

        self._last_cleanup = 0

        self._last_telemetry_day = (
            datetime.now().strftime(
                "%Y-%m-%d"
            )
        )

        self.monitor_thread = (
            threading.Thread(
                target=self._monitor_loop,
                name="EMS-ResourceMonitor",
                daemon=True,
            )
        )

        self.monitor_thread.start()

    # ============================================================
    # MONITOR
    # ============================================================

    def _monitor_loop(self):

        while self._running:

            try:

                self.io_meter.update_metrics()

                self.memory_monitor.update_metrics()

                self.guard.evaluate_state()

                self.cleanup_logs_if_needed()

                # Persist storage counters hourly,
                # not every monitor cycle.
                self.io_meter.persist_counters()

            except Exception as exc:

                logger.critical(
                    "Resource monitor failure: %s",
                    exc,
                )

            time.sleep(
                STORAGE_METRICS_INTERVAL_S
            )

    # ============================================================
    # WRITE POLICY
    # ============================================================

    def is_write_allowed(
        self,
        category,
    ):

        return self.guard.is_write_allowed(
            category
        )

    # ============================================================
    # CAPACITY
    # ============================================================

    def _get_usage_percent(self):

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

            if total <= 0:
                return 100.0

            return (
                (
                    total
                    - free
                )
                / total
            ) * 100

        except Exception:

            return 100.0

    # ============================================================
    # CLEANUP
    # ============================================================

    @staticmethod
    def _remove_old_files(
        directory,
        retention_days,
        protected_prefixes=(),
    ):

        now = time.time()

        cutoff = (
            now
            - retention_days
            * 86400
        )

        for path in glob.glob(
            os.path.join(
                directory,
                "*",
            )
        ):

            if not os.path.isfile(
                path
            ):
                continue

            name = os.path.basename(
                path
            )

            if any(
                name.startswith(prefix)
                for prefix in
                protected_prefixes
            ):
                continue

            try:

                if (
                    os.path.getmtime(
                        path
                    )
                    < cutoff
                ):

                    os.remove(
                        path
                    )

            except OSError:
                pass

    def cleanup_logs_if_needed(self):

        used_percent = (
            self._get_usage_percent()
        )

        now = time.monotonic()

        # Do not repeatedly run cleanup every 60 sec.
        if (
            now
            - self._last_cleanup
            < 3600
        ):
            return

        if used_percent < 80:
            return

        self._last_cleanup = now

        try:

            # Normal cleanup.
            self._remove_old_files(
                LOG_DIR,
                NORMAL_LOG_RETENTION_DAYS,
                protected_prefixes=(
                    "critical.log",
                ),
            )

            self._remove_old_files(
                TELEMETRY_DIR,
                TELEMETRY_RETENTION_DAYS,
            )

            if used_percent >= 90:

                self._remove_old_files(
                    LOG_DIR,
                    7,
                    protected_prefixes=(
                        "critical.log",
                    ),
                )

                self._remove_old_files(
                    TELEMETRY_DIR,
                    30,
                )

            if used_percent >= 95:

                self._remove_old_files(
                    LOG_DIR,
                    2,
                    protected_prefixes=(
                        "critical.log",
                    ),
                )

                self._remove_old_files(
                    TELEMETRY_DIR,
                    7,
                )

                self._remove_old_files(
                    os.path.join(
                        DATA_DIR,
                        "diagnostics",
                    ),
                    7,
                )

            # NEVER intentionally delete critical history
            # as part of normal capacity cleanup.

        except Exception as exc:

            logger.critical(
                "Storage cleanup failed: %s",
                exc,
            )

    # ============================================================
    # DAILY TELEMETRY
    # ============================================================

    def save_daily_telemetry(self):

        if not self.is_write_allowed(
            "telemetry"
        ):
            return False

        try:

            io_metrics = (
                self.io_meter.get_metrics()
            )

            mem_metrics = (
                self.memory_monitor.get_metrics()
            )

            day = datetime.now().strftime(
                "%Y-%m-%d"
            )

            filename = os.path.join(
                TELEMETRY_DIR,
                f"daily_{day}.csv",
            )

            exists = os.path.exists(
                filename
            )

            row = (
                f"{datetime.now().isoformat()},"
                f"{io_metrics.get('daily_logical_writes', 0)},"
                f"{io_metrics.get('daily_block_writes', 0)},"
                f"{io_metrics.get('daily_block_reads', 0)},"
                f"{io_metrics.get('block_io_ratio', 0)},"
                f"{mem_metrics.get('ram_used', 0)},"
                f"{mem_metrics.get('ram_available', 0)},"
                f"{mem_metrics.get('swap_used', 0)},"
                f"{mem_metrics.get('ems_rss', 0)},"
                f"{mem_metrics.get('ems_vms', 0)},"
                f"{mem_metrics.get('ems_threads', 0)},"
                f"{mem_metrics.get('ems_fds', 0)},"
                f"{mem_metrics.get('memory_state', 'UNKNOWN')}\n"
            )

            with open(
                filename,
                "a",
                encoding="utf-8",
            ) as fh:

                if not exists:

                    fh.write(
                        "timestamp,"
                        "logical_writes,"
                        "block_writes,"
                        "block_reads,"
                        "block_io_ratio,"
                        "ram_used_kb,"
                        "ram_available_kb,"
                        "swap_used_kb,"
                        "ems_rss_kb,"
                        "ems_vms_kb,"
                        "ems_threads,"
                        "ems_fds,"
                        "memory_state\n"
                    )

                fh.write(
                    row
                )

            self.io_meter.record_ems_write(
                "telemetry",
                len(
                    row.encode(
                        "utf-8"
                    )
                ),
            )

            return True

        except Exception as exc:

            logger.error(
                "Daily telemetry failed: %s",
                exc,
            )

            return False

    # ============================================================
    # CONTROLLER COMPATIBILITY
    # ============================================================

    def persist_counters(self):

        return self.io_meter.persist_counters(
            force=True
        )

    # ============================================================
    # STATUS
    # ============================================================

    def get_status(self):

        return {
            "resource_state":
                self.guard.get_state(),

            "storage":
                self.io_meter.get_metrics(),

            "memory":
                self.memory_monitor.get_metrics(),
        }

    # ============================================================
    # SHUTDOWN
    # ============================================================

    def stop(self):

        self._running = False

        try:

            self.io_meter.persist_counters(
                force=True
            )

        except Exception as exc:

            logger.error(
                "Final storage counter persistence failed: %s",
                exc,
            )

        if (
            self.monitor_thread
            and self.monitor_thread.is_alive()
        ):

            self.monitor_thread.join(
                timeout=5
            )