import os
import threading
from collections import deque

class MemoryManager:
    """Tracks RAM, Swap, and EMS RSS in RAM with trend detection."""
    def __init__(self):
        self.metrics = {
            "ram_total": 0, "ram_used": 0, "swap_total": 0, "swap_used": 0,
            "swap_in": 0, "swap_out": 0, "oom_kills": 0, "ems_rss": 0,
            "ems_vms": 0, "ems_threads": 0, "ems_fds": 0,
            "memory_state": "MEMORY_NORMAL"
        }
        # Store 24 hours of history (144 samples @ 10 min interval)
        self.rss_history = deque(maxlen=144)
        self.vms_history = deque(maxlen=144)
        self.fd_history = deque(maxlen=144)
        self.lock = threading.Lock()

    def update_metrics(self):
        with self.lock:
            try:
                with open("/proc/meminfo", "r") as f:
                    meminfo = dict(line.split(":") for line in f.readlines())
                    self.metrics["ram_total"] = int(meminfo.get("MemTotal", "0").strip().split()[0])
                    self.metrics["ram_used"] = self.metrics["ram_total"] - int(meminfo.get("MemAvailable", "0").strip().split()[0])
                    self.metrics["swap_total"] = int(meminfo.get("SwapTotal", "0").strip().split()[0])
                    self.metrics["swap_used"] = self.metrics["swap_total"] - int(meminfo.get("SwapFree", "0").strip().split()[0])
            except: pass
            
            try:
                with open("/proc/vmstat", "r") as f:
                    vmstats = dict(line.split() for line in f.readlines() if len(line.split()) == 2)
                    self.metrics["swap_in"] = int(vmstats.get("pswpin", "0"))
                    self.metrics["swap_out"] = int(vmstats.get("pswpout", "0"))
                    self.metrics["oom_kills"] = int(vmstats.get("oom_kill", "0"))
            except: pass

            try:
                pid = os.getpid()
                with open(f"/proc/{pid}/status", "r") as f:
                    for line in f:
                        if line.startswith("VmRSS:"): self.metrics["ems_rss"] = int(line.split()[1])
                        elif line.startswith("VmSize:"): self.metrics["ems_vms"] = int(line.split()[1])
                        elif line.startswith("Threads:"): self.metrics["ems_threads"] = int(line.split()[1])
                
                self.metrics["ems_fds"] = len(os.listdir(f"/proc/{pid}/fd"))
            except: pass

            # Track trends
            self.rss_history.append(self.metrics["ems_rss"])
            self.vms_history.append(self.metrics["ems_vms"])
            self.fd_history.append(self.metrics["ems_fds"])
            
            # Industrial Memory Leak Detection (Sustained Trends)
            if len(self.rss_history) >= 100:
                # Simple slope calculation: compare recent quarter to oldest quarter
                old_rss = list(self.rss_history)[:25]
                recent_rss = list(self.rss_history)[-25:]
                avg_old = sum(old_rss) / len(old_rss)
                avg_recent = sum(recent_rss) / len(recent_rss)
                
                old_fds = list(self.fd_history)[:25]
                recent_fds = list(self.fd_history)[-25:]
                avg_old_fds = sum(old_fds) / len(old_fds)
                avg_recent_fds = sum(recent_fds) / len(recent_fds)

                # Require sustained monotonic growth ( > 20% over 10 hours)
                if avg_recent > avg_old * 1.2 and avg_recent_fds > avg_old_fds * 1.1:
                    self.metrics["memory_state"] = "MEMORY_LEAK_SUSPECTED"
                elif self.metrics["swap_used"] > 10000: # > 10MB swap used
                    self.metrics["memory_state"] = "SWAP_ACTIVE"
                elif self.metrics["oom_kills"] > 0:
                    self.metrics["memory_state"] = "OOM_DETECTED"
                else:
                    self.metrics["memory_state"] = "MEMORY_NORMAL"

    def is_memory_pressure(self) -> bool:
        """Returns True if system is under memory pressure (triggers storage degradation)."""
        with self.lock:
            return self.metrics["memory_state"] in ["SWAP_ACTIVE", "MEMORY_LEAK_SUSPECTED", "OOM_DETECTED"]

    def get_metrics(self) -> dict:
        with self.lock:
            return self.metrics.copy()