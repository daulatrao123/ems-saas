import os
import json
import threading
from datetime import datetime
from config import DATA_DIR, TELEMETRY_DIR, TOTAL_DAILY_PHYSICAL_BUDGET_BYTES
from logger import logger

class StorageIOMeter:
    """Tracks actual block device reads/writes in RAM, persisting baseline once per day."""
    def __init__(self):
        self.device = self._get_device_name(DATA_DIR)
        self.state_file = os.path.join(TELEMETRY_DIR, "io_baseline.json")
        self.lock = threading.Lock()
        
        # Load persisted baseline (survives reboots)
        self.state = self._load_state()
        self.current_day = self.state.get("date", "")
        
        # Get current diskstats
        current_reads, current_writes = self._read_diskstats()
        
        # If date changed or device counters reset (USB replaced), reset baseline
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self.current_day or current_writes < self.state.get("baseline_phys_writes", 0):
            self.state = {
                "date": today,
                "baseline_phys_reads": current_reads,
                "baseline_phys_writes": current_writes,
                "logical_writes": 0
            }
            self._save_state()

    def _get_device_name(self, path):
        try:
            result = os.popen(f"df {path}").read().split('\n')[1].split()[0]
            base = os.path.basename(result)
            if base.startswith("mmcblk"): return base.replace("p2", "").replace("p1", "")
            if base.startswith("sd"): return base[:3]
            return None
        except: return None

    def _read_diskstats(self):
        if not self.device: return (0, 0)
        try:
            with open("/proc/diskstats", "r") as f:
                for line in f:
                    parts = line.split()
                    if parts[2] == self.device:
                        return (int(parts[5]) * 512, int(parts[9]) * 512) # Reads, Writes
        except: return (0, 0)

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f: return json.load(f)
            except: pass
        return {}

    def _save_state(self):
        try:
            with open(self.state_file, 'w') as f: json.dump(self.state, f)
        except: pass

    def record_ems_write(self, bytes_written):
        with self.lock:
            self.state["logical_writes"] += bytes_written

    def update_metrics(self):
        with self.lock:
            today = datetime.now().strftime("%Y-%m-%d")
            if today != self.state["date"]:
                # Day rollover: save final totals to lifetime, reset baseline
                self.state["date"] = today
                current_reads, current_writes = self._read_diskstats()
                self.state["baseline_phys_reads"] = current_reads
                self.state["baseline_phys_writes"] = current_writes
                self.state["logical_writes"] = 0
                self._save_state() # One write per day
            
            current_reads, current_writes = self._read_diskstats()
            
            # Detect counter reset (USB replugged)
            if current_writes < self.state["baseline_phys_writes"]:
                self.state["baseline_phys_writes"] = current_writes
                self.state["baseline_phys_reads"] = current_reads
                self._save_state()

    def get_metrics(self) -> dict:
        with self.lock:
            current_reads, current_writes = self._read_diskstats()
            phys_writes = current_writes - self.state.get("baseline_phys_writes", 0)
            phys_reads = current_reads - self.state.get("baseline_phys_reads", 0)
            logical_writes = self.state.get("logical_writes", 0)
            
            waf = (phys_writes / logical_writes) if logical_writes > 0 else 0
            budget_exceeded = phys_writes > TOTAL_DAILY_PHYSICAL_BUDGET_BYTES
            
            return {
                "daily_logical_writes": logical_writes,
                "daily_physical_writes": phys_writes,
                "daily_physical_reads": phys_reads,
                "waf": round(waf, 2),
                "budget_exceeded": budget_exceeded
            }