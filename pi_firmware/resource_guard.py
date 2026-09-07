import threading

from config import (
    STORAGE_WARNING_PERCENT,
    STORAGE_CLEANUP_PERCENT,
    STORAGE_REDUCED_PERCENT,
    STORAGE_PROTECTED_PERCENT,
)


class ResourceGuard:
    """
    Central resource-protection policy.

    Philosophy:

        protect safety-critical durability

    while

        aggressively reducing optional writes
        when storage or memory is under pressure.

    Storage bands (Phase 0.5, boundaries are inclusive on the lower edge):

        used <  70%        NORMAL
        70% <= used < 80%  WARNING
        80% <= used < 90%  CLEANUP_ELIGIBLE
        90% <= used < 95%  CRITICAL
        used >= 95%        STORAGE_PROTECTION
        statvfs failure    STORAGE_FAILED

    Safety-critical categories (critical_log, state, queue_db) are always
    attempted regardless of band. A full disk must surface as a persistence
    failure, never as a silently dropped command record.
    """

    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CLEANUP_ELIGIBLE = "CLEANUP_ELIGIBLE"
    CRITICAL = "CRITICAL"
    STORAGE_PROTECTION = "STORAGE_PROTECTION"
    STORAGE_FAILED = "STORAGE_FAILED"

    STATE_RANK = {
        NORMAL: 0,
        WARNING: 1,
        CLEANUP_ELIGIBLE: 2,
        CRITICAL: 3,
        STORAGE_PROTECTION: 4,
        STORAGE_FAILED: 5,
    }

    ALWAYS_ALLOWED = (
        "critical_log",
        "state",
        "queue_db",
    )

    OPTIONAL_CATEGORIES = (
        "normal_log",
        "telemetry",
        "diagnostics",
        "other",
    )

    def __init__(
        self,
        storage_io_meter,
        memory_manager,
    ):
        self.io_meter = storage_io_meter
        self.memory_monitor = memory_manager

        self.state = self.NORMAL
        self.write_reduced = False
        self.lock = threading.RLock()

    @classmethod
    def classify_usage(
        cls,
        used_percent,
        storage_ok=True,
    ):
        if not storage_ok:
            return cls.STORAGE_FAILED

        used_percent = float(used_percent)

        if used_percent >= STORAGE_PROTECTED_PERCENT:
            return cls.STORAGE_PROTECTION

        if used_percent >= STORAGE_REDUCED_PERCENT:
            return cls.CRITICAL

        if used_percent >= STORAGE_CLEANUP_PERCENT:
            return cls.CLEANUP_ELIGIBLE

        if used_percent >= STORAGE_WARNING_PERCENT:
            return cls.WARNING

        return cls.NORMAL

    def evaluate_state(self):

        io_metrics = self.io_meter.get_metrics()
        mem_metrics = self.memory_monitor.get_metrics()

        used_percent = float(
            io_metrics.get(
                "used_percent",
                0,
            )
        )

        budget_exceeded = bool(
            io_metrics.get(
                "budget_exceeded",
                False,
            )
        )

        storage_ok = bool(
            io_metrics.get(
                "storage_ok",
                True,
            )
        )

        memory_state = mem_metrics.get(
            "memory_state",
            "MEMORY_NORMAL",
        )

        new_state = self.classify_usage(
            used_percent,
            storage_ok,
        )

        # Daily write budget / memory pressure reduce optional writes
        # without changing the reported storage-capacity band.
        write_reduced = (
            budget_exceeded
            or memory_state in (
                "MEMORY_PRESSURE",
                "MEMORY_LEAK_SUSPECTED",
                "OOM_DETECTED",
            )
        )

        with self.lock:
            old_state = self.state
            self.state = new_state
            self.write_reduced = write_reduced

        # Intentionally do not call logger here.
        #
        # Logger itself depends on ResourceGuard.
        # Logging from this method can create a circular
        # dependency/deadlock.

        return (
            old_state,
            new_state,
        )

    def is_write_allowed(
        self,
        category,
    ):

        with self.lock:
            state = self.state
            write_reduced = self.write_reduced

        # Safety-critical data is always attempted.
        if category in self.ALWAYS_ALLOWED:
            return True

        # >= 95% or unreadable filesystem: optional writes stop.
        if state in (
            self.STORAGE_PROTECTION,
            self.STORAGE_FAILED,
        ):
            return False

        # 90-95% (or budget/memory pressure): diagnostics only.
        if (
            state == self.CRITICAL
            or write_reduced
        ):
            return category == "diagnostics"

        # NORMAL / WARNING / CLEANUP_ELIGIBLE.
        return category in self.OPTIONAL_CATEGORIES

    def get_state(self):
        with self.lock:
            return self.state

    def is_write_reduced(self):
        with self.lock:
            return self.write_reduced
