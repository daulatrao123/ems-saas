import threading


class ResourceGuard:
    """
    Central resource-protection policy.

    Philosophy:

        protect safety-critical durability

    while

        aggressively reducing optional writes
        when storage or memory is under pressure.
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
        self.io_meter = storage_io_meter
        self.memory_monitor = memory_manager

        self.state = self.NORMAL
        self.lock = threading.RLock()

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

        if not storage_ok:
            new_state = self.STORAGE_FAILED

        elif used_percent >= 98:
            new_state = self.STORAGE_CRITICAL

        elif used_percent >= 95:
            new_state = self.STORAGE_PROTECTED

        elif (
            used_percent >= 90
            or budget_exceeded
            or memory_state in (
                "MEMORY_PRESSURE",
                "MEMORY_LEAK_SUSPECTED",
                "OOM_DETECTED",
            )
        ):
            new_state = self.WRITE_REDUCED

        else:
            new_state = self.NORMAL

        with self.lock:
            old_state = self.state
            self.state = new_state

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

        # Safety-critical data is always attempted.
        if category in (
            "critical_log",
            "state",
            "queue_db",
        ):
            return True

        if state == self.NORMAL:
            return category in (
                "normal_log",
                "telemetry",
                "diagnostics",
                "other",
            )

        if state == self.WRITE_REDUCED:
            return category in (
                "diagnostics",
            )

        # Under serious storage failure/pressure,
        # optional writes stop.
        return False

    def get_state(self):
        with self.lock:
            return self.state