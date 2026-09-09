import logging
import os
import threading
import time
from collections import deque
from datetime import datetime

from config import (
    LOG_DIR,
    DAILY_LOG_BUDGET_BYTES,
    CRITICAL_LOG_BUDGET_BYTES,
)


_storage_mgr = None
_storage_lock = threading.Lock()

# Logging policy: at import only stdout (journald, volatile) + a bounded RAM ring exist.
# File sinks under LOG_DIR are attached by the controller ONLY after the secondary data volume
# has been verified SECONDARY_HEALTHY, and detached the moment it stops being so.
# Nothing in this module ever creates a directory: a missing LOG_DIR fails closed.
RING_MAX_RECORDS = 200
_file_handlers = []
_sink_lock = threading.Lock()


class RingHandler(logging.Handler):
    """Bounded RAM sink for ERROR/CRITICAL records. Never touches disk."""

    def __init__(self, maxlen=RING_MAX_RECORDS):
        super().__init__(level=logging.ERROR)
        self.records = deque(maxlen=maxlen)
        self.dropped = 0

    def emit(self, record):
        try:
            if len(self.records) == self.records.maxlen:
                self.dropped += 1
            self.records.append({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(record.created)),
                "level": record.levelname,
                "message": record.getMessage(),
            })
        except Exception:
            self.handleError(record)

    def drain(self, limit=None):
        out = []
        while self.records and (limit is None or len(out) < limit):
            out.append(self.records.popleft())
        return out


ring = RingHandler()


def drain_ring(limit=None):
    return ring.drain(limit)


def set_storage_manager(
    storage_mgr,
):

    global _storage_mgr

    with _storage_lock:

        _storage_mgr = (
            storage_mgr
        )


class DailyBudgetHandler(
    logging.Handler
):

    def __init__(
        self,
        filename_prefix,
        budget_bytes,
        category,
        fsync_each_write=False,
    ):

        super().__init__()

        self.filename_prefix = (
            filename_prefix
        )

        self.budget_bytes = (
            budget_bytes
        )

        self.category = (
            category
        )

        self.fsync_each_write = (
            fsync_each_write
        )

        self.lock = threading.RLock()

        self.current_date = (
            datetime.now().strftime(
                "%Y-%m-%d"
            )
        )

        self.current_filename = (
            f"{self.filename_prefix}."
            f"{self.current_date}"
        )

        # No os.makedirs here: directories exist only after the secondary volume was verified
        # (config.ensure_data_dirs). A missing LOG_DIR must fail, never be created on the primary.
        self.fh = open(
            self.current_filename,
            "a",
            encoding="utf-8",
            buffering=1,
        )

        try:

            self.bytes_written = (
                os.path.getsize(
                    self.current_filename
                )
            )

        except OSError:

            self.bytes_written = 0

    def _rotate_if_needed(self):

        today = (
            datetime.now().strftime(
                "%Y-%m-%d"
            )
        )

        if today == self.current_date:
            return

        try:
            self.fh.flush()
        except Exception:
            pass

        try:
            self.fh.close()
        except Exception:
            pass

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

        try:

            self.bytes_written = (
                os.path.getsize(
                    self.current_filename
                )
            )

        except OSError:

            self.bytes_written = 0

    def emit(
        self,
        record,
    ):

        try:

            with self.lock:

                self._rotate_if_needed()

                with _storage_lock:
                    storage_mgr = (
                        _storage_mgr
                    )

                if (
                    storage_mgr
                    and not
                    storage_mgr
                    .is_write_allowed(
                        self.category
                    )
                ):

                    return

                message = (
                    self.format(
                        record
                    )
                    + "\n"
                )

                encoded = (
                    message.encode(
                        "utf-8"
                    )
                )

                size = len(
                    encoded
                )

                if (
                    self.bytes_written
                    + size
                    >
                    self.budget_bytes
                ):

                    # Budget exhausted.
                    # Deliberately do not keep
                    # writing this category.
                    return

                self.fh.write(
                    message
                )

                if self.fsync_each_write:

                    self.fh.flush()

                    os.fsync(
                        self.fh.fileno()
                    )

                self.bytes_written += (
                    size
                )

                if storage_mgr:

                    storage_mgr.io_meter.record_ems_write(
                        self.category,
                        size,
                    )

        except Exception:

            self.handleError(
                record
            )

    def close(self):

        try:

            with self.lock:

                if self.fh:

                    try:
                        self.fh.flush()
                    except Exception:
                        pass

                    try:
                        self.fh.close()
                    except Exception:
                        pass

        finally:

            super().close()


_FORMATTER = logging.Formatter(
    "%(asctime)s "
    "[%(levelname)s] "
    "%(message)s",
    datefmt=(
        "%Y-%m-%dT%H:%M:%S%z"
    ),
)


class NormalFilter(
    logging.Filter
):

    def filter(
        self,
        record,
    ):

        return (
            record.levelno
            < logging.ERROR
        )


def _build_file_handlers():
    """Opens the two daily log files under LOG_DIR (raises OSError if LOG_DIR is missing)."""
    normal_handler = DailyBudgetHandler(
        os.path.join(LOG_DIR, "ems_app.log"),
        DAILY_LOG_BUDGET_BYTES,
        "normal_log",
        fsync_each_write=False,
    )
    normal_handler.setFormatter(_FORMATTER)
    normal_handler.setLevel(logging.INFO)
    normal_handler.addFilter(NormalFilter())

    try:
        critical_handler = DailyBudgetHandler(
            os.path.join(LOG_DIR, "critical.log"),
            CRITICAL_LOG_BUDGET_BYTES,
            "critical_log",
            fsync_each_write=True,
        )
    except OSError:
        normal_handler.close()
        raise
    critical_handler.setFormatter(_FORMATTER)
    critical_handler.setLevel(logging.ERROR)
    return [normal_handler, critical_handler]


def attach_file_sinks():
    """Attach the daily file sinks. Caller MUST have verified the secondary volume HEALTHY.
    Returns False (and attaches nothing) when the files cannot be opened. Idempotent."""
    ems_logger = logging.getLogger("EMS")
    with _sink_lock:
        if _file_handlers:
            return True
        try:
            handlers = _build_file_handlers()
        except OSError as exc:
            ems_logger.error("Log file sinks not attached: %s", exc)
            return False
        for handler in handlers:
            ems_logger.addHandler(handler)
        _file_handlers.extend(handlers)
        return True


def detach_file_sinks():
    """Remove and close the file sinks; logging continues on stdout + RAM ring only."""
    ems_logger = logging.getLogger("EMS")
    with _sink_lock:
        for handler in _file_handlers:
            ems_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        _file_handlers.clear()


def file_sinks_attached():
    return bool(_file_handlers)


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

    # Import-time sinks: console (journald is volatile on the Pi) + RAM ring. No file, no flash write.
    stream_handler = (
        logging.StreamHandler()
    )

    stream_handler.setFormatter(
        _FORMATTER
    )

    ems_logger.addHandler(
        stream_handler
    )

    ring.setFormatter(_FORMATTER)
    ems_logger.addHandler(ring)

    return ems_logger


logger = setup_logger()