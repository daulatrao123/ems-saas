import logging
import os
import threading
from datetime import datetime

from config import (
    LOG_DIR,
    DAILY_LOG_BUDGET_BYTES,
    CRITICAL_LOG_BUDGET_BYTES,
)


_storage_mgr = None
_storage_lock = threading.Lock()


def set_storage_manager(storage_mgr):
    global _storage_mgr

    with _storage_lock:
        _storage_mgr = storage_mgr


class DailyBudgetHandler(logging.Handler):
    """
    One file per day.

    No backup-count rotation.
    No hourly rewriting.
    No periodic fsync for normal logs.

    Normal logs are intentionally lower durability than
    critical safety/audit events.
    """

    def __init__(
        self,
        filename_prefix,
        budget_bytes,
        category,
        fsync_each_write=False,
    ):
        super().__init__()

        self.filename_prefix = filename_prefix
        self.budget_bytes = budget_bytes
        self.category = category
        self.fsync_each_write = fsync_each_write

        self.lock = threading.RLock()

        self.current_date = (
            datetime.now().strftime("%Y-%m-%d")
        )

        self.current_filename = (
            f"{self.filename_prefix}."
            f"{self.current_date}"
        )

        os.makedirs(
            os.path.dirname(self.current_filename),
            exist_ok=True,
        )

        self.fh = open(
            self.current_filename,
            "a",
            encoding="utf-8",
            buffering=1,
        )

        try:
            self.bytes_written = os.path.getsize(
                self.current_filename
            )
        except OSError:
            self.bytes_written = 0

    def _rotate_if_needed(self):
        today = datetime.now().strftime(
            "%Y-%m-%d"
        )

        if today == self.current_date:
            return

        self.fh.flush()
        self.fh.close()

        self.current_date = today

        self.current_filename = (
            f"{self.filename_prefix}."
            f"{self.current_date}"
        )

        self.fh = open(
            self.current_filename,
            "a",
            encoding="utf-8",
            buffering=1,
        )

        self.bytes_written = 0

    def emit(self, record):
        try:
            with self.lock:
                self._rotate_if_needed()

                with _storage_lock:
                    storage_mgr = _storage_mgr

                if (
                    storage_mgr
                    and not storage_mgr.is_write_allowed(
                        self.category
                    )
                ):
                    return

                message = (
                    self.format(record)
                    + "\n"
                )

                encoded = message.encode(
                    "utf-8"
                )

                size = len(encoded)

                if (
                    self.bytes_written + size
                    > self.budget_bytes
                ):
                    return

                self.fh.write(message)

                if self.fsync_each_write:
                    self.fh.flush()
                    os.fsync(
                        self.fh.fileno()
                    )

                self.bytes_written += size

                if storage_mgr:
                    storage_mgr.io_meter.record_ems_write(
                        self.category,
                        size,
                    )

        except Exception:
            self.handleError(record)

    def close(self):
        try:
            with self.lock:
                if self.fh:
                    self.fh.flush()
                    self.fh.close()
        finally:
            super().close()


def setup_logger():
    ems_logger = logging.getLogger(
        "EMS"
    )

    ems_logger.setLevel(
        logging.INFO
    )

    ems_logger.propagate = False

    if ems_logger.handlers:
        return ems_logger

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    normal_handler = DailyBudgetHandler(
        os.path.join(
            LOG_DIR,
            "ems_app.log",
        ),
        DAILY_LOG_BUDGET_BYTES,
        "normal_log",
        fsync_each_write=False,
    )

    normal_handler.setFormatter(
        formatter
    )

    normal_handler.setLevel(
        logging.INFO
    )

    class NormalFilter(logging.Filter):
        def filter(self, record):
            return record.levelno < logging.ERROR

    normal_handler.addFilter(
        NormalFilter()
    )

    critical_handler = DailyBudgetHandler(
        os.path.join(
            LOG_DIR,
            "critical.log",
        ),
        CRITICAL_LOG_BUDGET_BYTES,
        "critical_log",
        fsync_each_write=True,
    )

    critical_handler.setFormatter(
        formatter
    )

    critical_handler.setLevel(
        logging.ERROR
    )

    ems_logger.addHandler(
        normal_handler
    )

    ems_logger.addHandler(
        critical_handler
    )

    # Console output has NO flash cost.
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(
        formatter
    )

    ems_logger.addHandler(
        stream_handler
    )

    return ems_logger


logger = setup_logger()