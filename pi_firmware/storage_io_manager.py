import os
import json
import threading
import subprocess
from datetime import datetime
from config import DATA_DIR, TELEMETRY_DIR, TOTAL_DAILY_PHYSICAL_BUDGET_BYTES
from logger import logger

class StorageIOMeter:
    """Tracks actual block device reads/writes in RAM, persisting baseline once per day."""
    def __init__(self):
        self.device = self._get_device_name(DATA_DIR)
        self.device_serial = self._get_device_serial()
        self.state_file = os.path.join(TELEMETRY_DIR, "io_baseline.json")
        self.lifetime_file = os.path.join(TELEMETRY_DIR, "lifetime_counters.json")
        
        self.lock = threading.Lock()
        
        # Load persisted baseline (survives reboots)
        self.state = self._load_state()
        self.lifetime_state = self._load_lifetime_state()
        
        today = datetime.now().strftime("%Y-%m-%d")
        current_reads, current_writes = self._read_diskstats()
        
        # If date changed, device changed, or counters reset (USB replugged), reset baseline
        if today != self.state.get("date") or \
           self.device_serial != self.state.get("device_serial") or \
           current_writes < self.state.get("baseline_phys_writes", 0):
            
            if self.device_serial != self.state.get("device_serial") and self.state.get("device_serial"):
                logger.critical("STORAGE_DEVICE_CHANGED: USB drive replaced! Starting new endurance epoch.")
                
            self.state = {
                "date": today,
                "device_serial": self.device_serial,
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

    def _get_device_serial(self):
        if not self.device: return "UNKNOWN"
        try:
            result = subprocess.run(["lsblk", "-n", "-o", "SERIAL", f"/dev/{self.device}"], capture_output=True, text=True, timeout=5)
            return result.stdout.strip() or "UNKNOWN"
        except: return "UNKNOWN"

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

    def _load_lifetime_state(self):
        if os.path.exists(self.lifetime_file):
            try:
                with open(self.lifetime_file, 'r') as f: return json.load(f)
            except: pass
        return {"lifetime_logical_writes": 0, "lifetime_physical_writes": 0, "lifetime_physical_reads": 0}

    def _save_state(self):
        try:
            with open(self.state_file, 'w') as f: json.dump(self.state, f)
        except: pass

    def _save_lifetime_state(self):
        try:
            with open(self.lifetime_file, 'w') as f: json.dump(self.lifetime_state, f)
        except: pass

    def record_ems_write(self, bytes_written):
        with self.lock:
            self.state["logical_writes"] += bytes_written

    def update_metrics(self):
        with self.lock:
            today = datetime.now().strftime("%Y-%m-%d")
            if today != self.state["date"]:
                # Day rollover: save final totals to lifetime, reset baseline
                self.lifetime_state["lifetime_logical_writes"] += self.state.get("logical_writes", 0)
                
                current_reads, current_writes = self._read_diskstats()
                phys_writes_today = current_writes - self.state.get("baseline_phys_writes", 0)
                phys_reads_today = current_reads - self.state.get("baseline_phys_reads", 0)
                
                self.lifetime_state["lifetime_physical_writes"] += phys_writes_today
                self.lifetime_state["lifetime_physical_reads"] += phys_reads_today
                self._save_lifetime_state() # One write per day
                
                self.state["date"] = today
                self.state["baseline_phys_reads"] = current_reads
                self.state["baseline_phys_writes"] = current_writes
                self.state["logical_writes"] = 0
                self._save_state()
            
            current_reads, current_writes = self._read_diskstats()
            
            # Detect counter reset (USB replugged without date change)
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
                "budget_exceeded": budget_exceeded,
                "lifetime_logical_writes": self.lifetime_state["lifetime_logical_writes"] + logical_writes,
                "lifetime_physical_writes": self.lifetime_state["lifetime_physical_writes"] + phys_writes,
                "device_serial": self.device_serial
            }