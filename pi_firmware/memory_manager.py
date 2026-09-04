import os
import threading
import time
from collections import deque

from config import (
    MEMORY_HISTORY_SAMPLES,
    MEMORY_LEAK_MIN_SAMPLES,
    MEMORY_LEAK_RSS_GROWTH_PERCENT,
    MEMORY_LEAK_VMS_GROWTH_PERCENT,
    MEMORY_LEAK_FD_GROWTH_PERCENT,
    MEMORY_LEAK_THREAD_GROWTH_PERCENT,
    MEMORY_AVAILABLE_PRESSURE_PERCENT,
    SWAP_WARNING_BYTES,
)


class MemoryManager:
    """
    RAM-first memory monitor.

    All historical samples stay in RAM.
    This class intentionally does NOT write every sample to USB.
    """

    def __init__(self):
        self.lock = threading.Lock()

        self.metrics = {
            "ram_total_kb": 0,
            "ram_available_kb": 0,
            "ram_used_kb": 0,
            "ram_used_percent": 0.0,

            "swap_total_kb": 0,
            "swap_used_kb": 0,

            "swap_in": 0,
            "swap_out": 0,
            "oom_kills": 0,

            "ems_rss_kb": 0,
            "ems_vms_kb": 0,
            "ems_threads": 0,
            "ems_fds": 0,

            "memory_state": "MEMORY_NORMAL",
        }

        self.rss_history = deque(maxlen=MEMORY_HISTORY_SAMPLES)
        self.vms_history = deque(maxlen=MEMORY_HISTORY_SAMPLES)
        self.fd_history = deque(maxlen=MEMORY_HISTORY_SAMPLES)
        self.thread_history = deque(maxlen=MEMORY_HISTORY_SAMPLES)
        self.available_history = deque(maxlen=MEMORY_HISTORY_SAMPLES)

        self._last_oom_kills = 0

    @staticmethod
    def _read_meminfo():
        result = {}

        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as fh:
                for line in fh:
                    if ":" not in line:
                        continue

                    key, value = line.split(":", 1)
                    parts = value.strip().split()

                    if parts:
                        result[key] = int(parts[0])

        except (OSError, ValueError):
            pass

        return result

    @staticmethod
    def _read_vmstat():
        result = {}

        try:
            with open("/proc/vmstat", "r", encoding="utf-8") as fh:
                for line in fh:
                    parts = line.split()

                    if len(parts) == 2:
                        try:
                            result[parts[0]] = int(parts[1])
                        except ValueError:
                            pass

        except (OSError, ValueError):
            pass

        return result

    @staticmethod
    def _read_process_metrics():
        pid = os.getpid()

        rss = 0
        vms = 0
        threads = 0
        fds = 0

        try:
            with open(
                f"/proc/{pid}/status",
                "r",
                encoding="utf-8",
            ) as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        rss = int(line.split()[1])

                    elif line.startswith("VmSize:"):
                        vms = int(line.split()[1])

                    elif line.startswith("Threads:"):
                        threads = int(line.split()[1])

        except (OSError, ValueError):
            pass

        try:
            fds = len(os.listdir(f"/proc/{pid}/fd"))
        except OSError:
            pass

        return rss, vms, threads, fds

    @staticmethod
    def _growth_percent(old_values, new_values):
        if not old_values or not new_values:
            return 0.0

        old_avg = sum(old_values) / len(old_values)
        new_avg = sum(new_values) / len(new_values)

        if old_avg <= 0:
            return 0.0

        return ((new_avg - old_avg) / old_avg) * 100.0

    def update_metrics(self):
        with self.lock:
            meminfo = self._read_meminfo()
            vmstat = self._read_vmstat()

            total = meminfo.get("MemTotal", 0)
            available = meminfo.get("MemAvailable", 0)

            used = max(0, total - available)

            swap_total = meminfo.get("SwapTotal", 0)
            swap_free = meminfo.get("SwapFree", 0)
            swap_used = max(0, swap_total - swap_free)

            rss, vms, threads, fds = self._read_process_metrics()

            oom_kills = vmstat.get("oom_kill", 0)

            self.metrics.update({
                "ram_total_kb": total,
                "ram_available_kb": available,
                "ram_used_kb": used,
                "ram_used_percent": (
                    (used / total) * 100.0
                    if total > 0
                    else 100.0
                ),

                "swap_total_kb": swap_total,
                "swap_used_kb": swap_used,

                "swap_in": vmstat.get("pswpin", 0),
                "swap_out": vmstat.get("pswpout", 0),
                "oom_kills": oom_kills,

                "ems_rss_kb": rss,
                "ems_vms_kb": vms,
                "ems_threads": threads,
                "ems_fds": fds,
            })

            self.rss_history.append(rss)
            self.vms_history.append(vms)
            self.fd_history.append(fds)
            self.thread_history.append(threads)
            self.available_history.append(available)

            available_percent = (
                (available / total) * 100.0
                if total > 0
                else 0.0
            )

            # OOM is always highest priority.
            if oom_kills > self._last_oom_kills:
                state = "OOM_DETECTED"

            elif (
                swap_used * 1024 >= SWAP_WARNING_BYTES
            ):
                state = "SWAP_ACTIVE"

            elif (
                available_percent
                <= MEMORY_AVAILABLE_PRESSURE_PERCENT
            ):
                state = "MEMORY_PRESSURE"

            elif len(self.rss_history) >= MEMORY_LEAK_MIN_SAMPLES:
                half = len(self.rss_history) // 2

                rss_growth = self._growth_percent(
                    list(self.rss_history)[:half],
                    list(self.rss_history)[-half:],
                )

                vms_growth = self._growth_percent(
                    list(self.vms_history)[:half],
                    list(self.vms_history)[-half:],
                )

                fd_growth = self._growth_percent(
                    list(self.fd_history)[:half],
                    list(self.fd_history)[-half:],
                )

                thread_growth = self._growth_percent(
                    list(self.thread_history)[:half],
                    list(self.thread_history)[-half:],
                )

                if (
                    rss_growth >= MEMORY_LEAK_RSS_GROWTH_PERCENT
                    or vms_growth >= MEMORY_LEAK_VMS_GROWTH_PERCENT
                    or fd_growth >= MEMORY_LEAK_FD_GROWTH_PERCENT
                    or thread_growth >= MEMORY_LEAK_THREAD_GROWTH_PERCENT
                ):
                    state = "MEMORY_LEAK_SUSPECTED"
                else:
                    state = "MEMORY_NORMAL"

            else:
                state = "MEMORY_NORMAL"

            self.metrics["memory_state"] = state
            self._last_oom_kills = oom_kills

    def is_memory_pressure(self) -> bool:
        with self.lock:
            return self.metrics["memory_state"] in {
                "MEMORY_PRESSURE",
                "SWAP_ACTIVE",
                "MEMORY_LEAK_SUSPECTED",
                "OOM_DETECTED",
            }

    def get_metrics(self):
        with self.lock:
            return dict(self.metrics)

    def get_health_summary(self):
        with self.lock:
            return {
                "state": self.metrics["memory_state"],
                "ram_used_percent": self.metrics["ram_used_percent"],
                "ram_available_kb": self.metrics["ram_available_kb"],
                "swap_used_kb": self.metrics["swap_used_kb"],
                "oom_kills": self.metrics["oom_kills"],
                "rss_kb": self.metrics["ems_rss_kb"],
                "vms_kb": self.metrics["ems_vms_kb"],
                "threads": self.metrics["ems_threads"],
                "fds": self.metrics["ems_fds"],
                "samples": len(self.rss_history),
            }