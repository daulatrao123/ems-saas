import threading

from logger import logger


class ResourceGuard:
    """
    Central resource policy.

    Principle:

        REDUCE OPTIONAL WRITES

    but

        NEVER deliberately disable essential
        command/state durability merely because
        an advisory write budget was exceeded.
    """

    NORMAL = "NORMAL"
    WRITE_REDUCED = "WRITE_REDUCED"
    STORAGE_PROTECTED = "STORAGE_PROTECTED"
    STORAGE_CRITICAL = "STORAGE_CRITICAL"
    STORAGE_FAILED = "STORAGE_FAILED"

    def __init__(
        self,
        storage_io_meter,
        memory_manager,
    ):

        self.io_meter = (
            storage_io_meter
        )

        self.memory_monitor = (
            memory_manager
        )

        self.state = self.NORMAL

        self.lock = threading.RLock()

    # ============================================================
    # EVALUATION
    # ============================================================

    def evaluate_state(self):

        with self.lock:

            io_metrics = (
                self.io_meter.get_metrics()
            )

            mem_metrics = (
                self.memory_monitor
                .get_metrics()
            )

            used_percent = (
                io_metrics.get(
                    "used_percent",
                    0,
                )
            )

            budget_exceeded = (
                io_metrics.get(
                    "budget_exceeded",
                    False,
                )
            )

            storage_ok = (
                io_metrics.get(
                    "storage_ok",
                    True,
                )
            )

            memory_state = (
                mem_metrics.get(
                    "memory_state",
                    "MEMORY_NORMAL",
                )
            )

            if not storage_ok:

                new_state = (
                    self.STORAGE_FAILED
                )

            elif (
                used_percent >= 98
            ):

                new_state = (
                    self.STORAGE_CRITICAL
                )

            elif (
                used_percent >= 95
            ):

                new_state = (
                    self.STORAGE_PROTECTED
                )

            elif (
                used_percent >= 90
                or budget_exceeded
                or memory_state
                in (
                    "MEMORY_PRESSURE",
                    "MEMORY_LEAK_SUSPECTED",
                    "OOM_DETECTED",
                )
            ):

                new_state = (
                    self.WRITE_REDUCED
                )

            else:

                new_state = (
                    self.NORMAL
                )

            old_state = self.state

            self.state = new_state

        # IMPORTANT:
        # Do not log while holding self.lock.
        # Otherwise logger -> guard can deadlock.
        if new_state != old_state:

            logger.critical(
                "RESOURCE GUARD: "
                "%s -> %s",
                old_state,
                new_state,
            )

    # ============================================================
    # WRITE POLICY
    # ============================================================

    def is_write_allowed(
        self,
        category,
    ):

        with self.lock:

            # Critical safety/audit events are always attempted.
            if category == "critical_log":
                return True

            # State is essential for crash recovery.
            if category == "state":
                return True

            # Durable command queue is essential.
            if category == "queue_db":
                return True

            # Optional writes are reduced under pressure.
            if self.state == self.NORMAL:

                return category in (
                    "normal_log",
                    "telemetry",
                    "diagnostics",
                    "other",
                )

            if self.state == self.WRITE_REDUCED:

                return category in (
                    "diagnostics",
                )

            if self.state in (
                self.STORAGE_PROTECTED,
                self.STORAGE_CRITICAL,
                self.STORAGE_FAILED,
            ):

                return False

            return False

    def get_state(self):

        with self.lock:
            return self.state