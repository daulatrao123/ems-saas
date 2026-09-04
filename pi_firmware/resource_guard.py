import threading


class ResourceGuard:
    """
    Centralized resource protection policy.

    IMPORTANT:
    This class must never perform logging while holding its lock.
    """

    NORMAL = "NORMAL"
    WRITE_REDUCED = "WRITE_REDUCED"
    STORAGE_PROTECTED = "STORAGE_PROTECTED"
    STORAGE_CRITICAL = "STORAGE_CRITICAL"
    STORAGE_FAILED = "STORAGE_FAILED"

    def __init__(self, storage_io_meter, memory_manager):
        self.io_meter = storage_io_meter
        self.memory_monitor = memory_manager

        self.state = self.NORMAL
        self._lock = threading.Lock()

    def evaluate_state(self):
        io_metrics = self.io_meter.get_metrics()
        mem_metrics = self.memory_monitor.get_metrics()

        used_percent = float(io_metrics.get("used_percent", 0))
        budget_exceeded = bool(io_metrics.get("budget_exceeded", False))
        storage_ok = bool(io_metrics.get("storage_ok", True))

        memory_state = mem_metrics.get(
            "memory_state",
            "MEMORY_NORMAL",
        )

        if not storage_ok:
            new_state = self.STORAGE_FAILED

        elif (
            used_percent >= 98
            or budget_exceeded
        ):
            new_state = self.STORAGE_CRITICAL

        elif (
            used_percent >= 95
            or memory_state == "OOM_DETECTED"
        ):
            new_state = self.STORAGE_PROTECTED

        elif (
            used_percent >= 90
            or memory_state in (
                "MEMORY_PRESSURE",
                "SWAP_ACTIVE",
                "MEMORY_LEAK_SUSPECTED",
            )
        ):
            new_state = self.WRITE_REDUCED

        else:
            new_state = self.NORMAL

        with self._lock:
            previous = self.state
            changed = previous != new_state
            self.state = new_state

        # NEVER log while holding self._lock.
        if changed:
            try:
                from logger import logger

                logger.critical(
                    "RESOURCE GUARD: %s -> %s",
                    previous,
                    new_state,
                )
            except Exception:
                pass

        return new_state

    def get_state(self):
        with self._lock:
            return self.state

    def is_write_allowed(self, category: str) -> bool:
        """
        Categories:

        critical_log
        state
        queue_db
        telemetry
        normal_log
        diagnostics
        """

        with self._lock:
            state = self.state

        if state == self.NORMAL:
            return category in {
                "critical_log",
                "state",
                "queue_db",
                "telemetry",
                "normal_log",
                "diagnostics",
            }

        if state == self.WRITE_REDUCED:
            return category in {
                "critical_log",
                "state",
                "queue_db",
            }

        if state == self.STORAGE_PROTECTED:
            return category in {
                "critical_log",
                "state",
            }

        if state == self.STORAGE_CRITICAL:
            return category in {
                "critical_log",
                "state",
            }

        if state == self.STORAGE_FAILED:
            # No normal persistence.
            # Critical logging remains best-effort.
            return category == "critical_log"

        return False

    def is_degraded(self):
        with self._lock:
            return self.state != self.NORMAL