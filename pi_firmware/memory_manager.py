import os
import threading
from collections import deque

class MemoryManager:
    """Tracks RAM, Swap, and EMS RSS in RAM with independent trend detection."""
    def __init__(self):
        self.metrics = {
            "ram_total": 0, "ram_used": 0, "swap_total": 0, "swap_used": 0,
            "swap_in": 0, "swap_out": 0, "oom_kills": 0, "ems_rss": 0,
            "ems_vms": 0, "ems_threads": 0, "ems_fds": 0,
            "memory_state": "MEMORY_NORMAL"
        }
        self.rss_history = deque(maxlen=144)
        self.vms_history = deque(maxlen=144)
        self.fd_history = deque(maxlen=144)
        self.thread_history = deque(maxlen=144)
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

            self.rss_history.append(self.metrics["ems_rss"])
            self.vms_history.append(self.metrics["ems_vms"])
            self.fd_history.append(self.metrics["ems_fds"])
            self.thread_history.append(self.metrics["ems_threads"])
            
            # 1. Immediate OOM Detection
            if self.metrics["oom_kills"] > 0:
                self.metrics["memory_state"] = "OOM_DETECTED"
            # 2. Immediate Swap Pressure
            elif self.metrics["swap_used"] > 10000:
                self.metrics["memory_state"] = "SWAP_ACTIVE"
            # 3. Independent Trend Leak Detection
            elif len(self.rss_history) >= 100:
                leak_detected = False
                
                old_rss = list(self.rss_history)[:25]
                recent_rss = list(self.rss_history)[-25:]
                if sum(recent_rss)/len(recent_rss) > sum(old_rss)/len(old_rss) * 1.2:
                    leak_detected = True
                    
                old_fds = list(self.fd_history)[:25]
                recent_fds = list(self.fd_history)[-25:]
                if sum(recent_fds)/len(recent_fds) > sum(old_fds)/len(old_fds) * 1.1:
                    leak_detected = True
                    
                old_threads = list(self.thread_history)[:25]
                recent_threads = list(self.thread_history)[-25:]
                if sum(recent_threads)/len(recent_threads) > sum(old_threads)/len(old_threads) * 1.1:
                    leak_detected = True

                self.metrics["memory_state"] = "MEMORY_LEAK_SUSPECTED" if leak_detected else "MEMORY_NORMAL"

    def is_memory_pressure(self) -> bool:
        with self.lock:
            return self.metrics["memory_state"] in ["SWAP_ACTIVE", "MEMORY_LEAK_SUSPECTED", "OOM_DETECTED"]

    def get_metrics(self) -> dict:
        with self.lock:
            return self.metrics.copy()