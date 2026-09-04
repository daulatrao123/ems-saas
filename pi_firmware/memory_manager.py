import os
import threading
from collections import deque


class MemoryManager:
    """
    RAM-only memory health monitor.

    IMPORTANT:
    This module intentionally does not persist every sample.
    Frequent persistence would defeat the flash-life objective.
    """

    def __init__(self):

        self.metrics = {
            "ram_total": 0,
            "ram_used": 0,
            "ram_available": 0,

            "swap_total": 0,
            "swap_used": 0,

            "swap_in": 0,
            "swap_out": 0,

            "oom_kills": 0,

            "ems_rss": 0,
            "ems_vms": 0,
            "ems_threads": 0,
            "ems_fds": 0,

            "memory_state": "MEMORY_NORMAL",
        }

        self.rss_history = deque(
            maxlen=144
        )

        self.vms_history = deque(
            maxlen=144
        )

        self.fd_history = deque(
            maxlen=144
        )

        self.thread_history = deque(
            maxlen=144
        )

        self.lock = threading.RLock()

        self.initial_oom_kills = None

    # ============================================================
    # HELPERS
    # ============================================================

    @staticmethod
    def _read_kb_value(
        values,
        key,
    ):
        raw = values.get(key)

        if raw is None:
            return 0

        try:
            return int(
                raw.strip().split()[0]
            )
        except (
            ValueError,
            IndexError,
        ):
            return 0

    # ============================================================
    # UPDATE
    # ============================================================

    def update_metrics(self):

        with self.lock:

            # ----------------------------------------------------
            # RAM / SWAP
            # ----------------------------------------------------

            try:

                meminfo = {}

                with open(
                    "/proc/meminfo",
                    "r",
                    encoding="utf-8",
                ) as fh:

                    for line in fh:

                        if ":" not in line:
                            continue

                        key, value = (
                            line.split(
                                ":",
                                1,
                            )
                        )

                        meminfo[key] = value

                ram_total = self._read_kb_value(
                    meminfo,
                    "MemTotal",
                )

                ram_available = self._read_kb_value(
                    meminfo,
                    "MemAvailable",
                )

                swap_total = self._read_kb_value(
                    meminfo,
                    "SwapTotal",
                )

                swap_free = self._read_kb_value(
                    meminfo,
                    "SwapFree",
                )

                self.metrics[
                    "ram_total"
                ] = ram_total

                self.metrics[
                    "ram_available"
                ] = ram_available

                self.metrics[
                    "ram_used"
                ] = max(
                    0,
                    ram_total - ram_available,
                )

                self.metrics[
                    "swap_total"
                ] = swap_total

                self.metrics[
                    "swap_used"
                ] = max(
                    0,
                    swap_total - swap_free,
                )

            except Exception:
                pass

            # ----------------------------------------------------
            # VMSTAT
            # ----------------------------------------------------

            try:

                vmstats = {}

                with open(
                    "/proc/vmstat",
                    "r",
                    encoding="utf-8",
                ) as fh:

                    for line in fh:

                        parts = line.split()

                        if len(parts) == 2:
                            vmstats[
                                parts[0]
                            ] = parts[1]

                self.metrics[
                    "swap_in"
                ] = int(
                    vmstats.get(
                        "pswpin",
                        0,
                    )
                )

                self.metrics[
                    "swap_out"
                ] = int(
                    vmstats.get(
                        "pswpout",
                        0,
                    )
                )

                current_oom = int(
                    vmstats.get(
                        "oom_kill",
                        0,
                    )
                )

                self.metrics[
                    "oom_kills"
                ] = current_oom

                if self.initial_oom_kills is None:
                    self.initial_oom_kills = (
                        current_oom
                    )

            except Exception:
                pass

            # ----------------------------------------------------
            # EMS PROCESS
            # ----------------------------------------------------

            try:

                pid = os.getpid()

                status_file = (
                    f"/proc/{pid}/status"
                )

                with open(
                    status_file,
                    "r",
                    encoding="utf-8",
                ) as fh:

                    for line in fh:

                        if line.startswith(
                            "VmRSS:"
                        ):

                            self.metrics[
                                "ems_rss"
                            ] = int(
                                line.split()[1]
                            )

                        elif line.startswith(
                            "VmSize:"
                        ):

                            self.metrics[
                                "ems_vms"
                            ] = int(
                                line.split()[1]
                            )

                        elif line.startswith(
                            "Threads:"
                        ):

                            self.metrics[
                                "ems_threads"
                            ] = int(
                                line.split()[1]
                            )

                self.metrics[
                    "ems_fds"
                ] = len(
                    os.listdir(
                        f"/proc/{pid}/fd"
                    )
                )

            except Exception:
                pass

            # ----------------------------------------------------
            # RAM-ONLY HISTORY
            # ----------------------------------------------------

            self.rss_history.append(
                self.metrics["ems_rss"]
            )

            self.vms_history.append(
                self.metrics["ems_vms"]
            )

            self.fd_history.append(
                self.metrics["ems_fds"]
            )

            self.thread_history.append(
                self.metrics["ems_threads"]
            )

            # ----------------------------------------------------
            # STATE
            # ----------------------------------------------------

            oom_delta = (
                self.metrics["oom_kills"]
                - (
                    self.initial_oom_kills
                    or 0
                )
            )

            if oom_delta > 0:

                self.metrics[
                    "memory_state"
                ] = "OOM_DETECTED"

                return

            ram_total = self.metrics[
                "ram_total"
            ]

            ram_available = self.metrics[
                "ram_available"
            ]

            available_percent = 100

            if ram_total > 0:

                available_percent = (
                    ram_available
                    / ram_total
                ) * 100

            if (
                self.metrics["swap_used"]
                > 10 * 1024
                or
                available_percent < 15
            ):

                self.metrics[
                    "memory_state"
                ] = "MEMORY_PRESSURE"

            elif len(
                self.rss_history
            ) >= 72:

                leak = (
                    self._detect_trend_leak()
                )

                self.metrics[
                    "memory_state"
                ] = (
                    "MEMORY_LEAK_SUSPECTED"
                    if leak
                    else "MEMORY_NORMAL"
                )

            else:

                self.metrics[
                    "memory_state"
                ] = "MEMORY_NORMAL"

    # ============================================================
    # TREND DETECTION
    # ============================================================

    def _growth_percent(
        self,
        history,
        window=24,
    ):

        if len(history) < window * 2:
            return 0.0

        values = list(history)

        old = values[
            -window * 2:
            -window
        ]

        recent = values[
            -window:
        ]

        old_avg = (
            sum(old)
            / len(old)
        )

        recent_avg = (
            sum(recent)
            / len(recent)
        )

        if old_avg <= 0:
            return 0.0

        return (
            (
                recent_avg
                - old_avg
            )
            / old_avg
        ) * 100

    def _detect_trend_leak(self):

        rss_growth = (
            self._growth_percent(
                self.rss_history
            )
        )

        vms_growth = (
            self._growth_percent(
                self.vms_history
            )
        )

        fd_growth = (
            self._growth_percent(
                self.fd_history
            )
        )

        thread_growth = (
            self._growth_percent(
                self.thread_history
            )
        )

        return (
            rss_growth >= 20
            or
            vms_growth >= 20
            or
            fd_growth >= 25
            or
            thread_growth >= 25
        )

    # ============================================================
    # PUBLIC
    # ============================================================

    def is_memory_pressure(self):

        with self.lock:

            return self.metrics[
                "memory_state"
            ] in (
                "MEMORY_PRESSURE",
                "MEMORY_LEAK_SUSPECTED",
                "OOM_DETECTED",
            )

    def get_metrics(self):

        with self.lock:
            return dict(
                self.metrics
            )