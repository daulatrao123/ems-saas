import os
import threading

class MemoryManager:
    """Tracks RAM, Swap, and EMS RSS in RAM."""
    def __init__(self):
        self.metrics = {
            "ram_total": 0, "ram_used": 0, "swap_total": 0, "swap_used": 0,
            "swap_in": 0, "swap_out": 0, "oom_kills": 0, "ems_rss": 0
        }
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
                        if line.startswith("VmRSS:"):
                            self.metrics["ems_rss"] = int(line.split()[1])
                            break
            except: pass

    def get_metrics(self) -> dict:
        with self.lock:
            return self.metrics.copy()