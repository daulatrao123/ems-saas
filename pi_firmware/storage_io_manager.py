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
        self.epoch_file = os.path.join(TELEMETRY_DIR, "storage_epoch.json")
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
                # Archive old lifetime state and reset
                self.lifetime_state = {"lifetime_logical_writes": 0, "lifetime_physical_writes": 0, "lifetime_physical_reads": 0}
                self._save_lifetime_state()
                
            self.state = {
                "date": today,
                "device_serial": self.device_serial,
                "baseline_phys_reads": current_reads,
                "baseline_phys_writes": current_writes,
                "logical_writes": {
                    "normal_log": 0, "critical_log": 0, "state": 0, 
                    "queue_db": 0, "telemetry": 0, "other": 0
                }
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
        if os.path.exists(self.epoch_file):
            try:
                with open(self.epoch_file, 'r') as f: return json.load(f)
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
            with open(self.epoch_file, 'w') as f: json.dump(self.state, f)
        except: pass

    def _save_lifetime_state(self):
        try:
            with open(self.lifetime_file, 'w') as f: json.dump(self.lifetime_state, f)
        except: pass

    def record_ems_write(self, category: str, bytes_written: int):
        """Centralized write attribution."""
        with self.lock:
            if category not in self.state["logical_writes"]:
                category = "other"
            self.state["logical_writes"][category] += bytes_written

    def get_sqlite_stats(self):
        """Measure SQLite DB and WAL contribution."""
        db_file = os.path.join(DATA_DIR, "queue", "ems_queue.sqlite")
        wal_file = db_file + "-wal"
        db_size = 0
        wal_size = 0
        try:
            if os.path.exists(db_file): db_size = os.path.getsize(db_file)
            if os.path.exists(wal_file): wal_size = os.path.getsize(wal_file)
        except: pass
        return {"db_size": db_size, "wal_size": wal_size}

    def update_metrics(self):
        with self.lock:
            today = datetime.now().strftime("%Y-%m-%d")
            if today != self.state["date"]:
                # Day rollover: save final totals to lifetime, reset baseline
                total_logical = sum(self.state.get("logical_writes", {}).values())
                self.lifetime_state["lifetime_logical_writes"] += total_logical
                
                current_reads, current_writes = self._read_diskstats()
                phys_writes_today = current_writes - self.state.get("baseline_phys_writes", 0)
                phys_reads_today = current_reads - self.state.get("baseline_phys_reads", 0)
                
                self.lifetime_state["lifetime_physical_writes"] += phys_writes_today
                self.lifetime_state["lifetime_physical_reads"] += phys_reads_today
                self._save_lifetime_state() # One write per day
                
                self.state["date"] = today
                self.state["baseline_phys_reads"] = current_reads
                self.state["baseline_phys_writes"] = current_writes
                self.state["logical_writes"] = {k: 0 for k in self.state["logical_writes"]}
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
            logical_writes_dict = self.state.get("logical_writes", {})
            logical_writes = sum(logical_writes_dict.values())
            
            # Calculate System WAF vs EMS WAF
            system_waf = (phys_writes / logical_writes) if logical_writes > 0 else 0
            
            # Capacity check
            used_percent = 0
            storage_ok = True
            try:
                stat = os.statvfs(DATA_DIR)
                total = stat.f_blocks * stat.f_frsize
                free = stat.f_bavail * stat.f_frsize
                used_percent = ((total - free) / total) * 100 if total > 0 else 100
            except:
                storage_ok = False
                
            budget_exceeded = phys_writes > TOTAL_DAILY_PHYSICAL_BUDGET_BYTES
            
            return {
                "daily_logical_writes": logical_writes,
                "daily_logical_breakdown": logical_writes_dict,
                "daily_physical_writes": phys_writes,
                "daily_physical_reads": phys_reads,
                "sqlite_stats": self.get_sqlite_stats(),
                "system_waf": round(system_waf, 2),
                "budget_exceeded": budget_exceeded,
                "used_percent": used_percent,
                "storage_ok": storage_ok,
                "lifetime_logical_writes": self.lifetime_state["lifetime_logical_writes"] + logical_writes,
                "lifetime_physical_writes": self.lifetime_state["lifetime_physical_writes"] + phys_writes,
                "device_serial": self.device_serial
            }import os
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
        self.epoch_file = os.path.join(TELEMETRY_DIR, "storage_epoch.json")
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
                # Archive old lifetime state and reset
                self.lifetime_state = {"lifetime_logical_writes": 0, "lifetime_physical_writes": 0, "lifetime_physical_reads": 0}
                self._save_lifetime_state()
                
            self.state = {
                "date": today,
                "device_serial": self.device_serial,
                "baseline_phys_reads": current_reads,
                "baseline_phys_writes": current_writes,
                "logical_writes": {
                    "normal_log": 0, "critical_log": 0, "state": 0, 
                    "queue_db": 0, "telemetry": 0, "other": 0
                }
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
        if os.path.exists(self.epoch_file):
            try:
                with open(self.epoch_file, 'r') as f: return json.load(f)
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
            with open(self.epoch_file, 'w') as f: json.dump(self.state, f)
        except: pass

    def _save_lifetime_state(self):
        try:
            with open(self.lifetime_file, 'w') as f: json.dump(self.lifetime_state, f)
        except: pass

    def record_ems_write(self, category: str, bytes_written: int):
        """Centralized write attribution."""
        with self.lock:
            if category not in self.state["logical_writes"]:
                category = "other"
            self.state["logical_writes"][category] += bytes_written

    def get_sqlite_stats(self):
        """Measure SQLite DB and WAL contribution."""
        db_file = os.path.join(DATA_DIR, "queue", "ems_queue.sqlite")
        wal_file = db_file + "-wal"
        db_size = 0
        wal_size = 0
        try:
            if os.path.exists(db_file): db_size = os.path.getsize(db_file)
            if os.path.exists(wal_file): wal_size = os.path.getsize(wal_file)
        except: pass
        return {"db_size": db_size, "wal_size": wal_size}

    def update_metrics(self):
        with self.lock:
            today = datetime.now().strftime("%Y-%m-%d")
            if today != self.state["date"]:
                # Day rollover: save final totals to lifetime, reset baseline
                total_logical = sum(self.state.get("logical_writes", {}).values())
                self.lifetime_state["lifetime_logical_writes"] += total_logical
                
                current_reads, current_writes = self._read_diskstats()
                phys_writes_today = current_writes - self.state.get("baseline_phys_writes", 0)
                phys_reads_today = current_reads - self.state.get("baseline_phys_reads", 0)
                
                self.lifetime_state["lifetime_physical_writes"] += phys_writes_today
                self.lifetime_state["lifetime_physical_reads"] += phys_reads_today
                self._save_lifetime_state() # One write per day
                
                self.state["date"] = today
                self.state["baseline_phys_reads"] = current_reads
                self.state["baseline_phys_writes"] = current_writes
                self.state["logical_writes"] = {k: 0 for k in self.state["logical_writes"]}
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
            logical_writes_dict = self.state.get("logical_writes", {})
            logical_writes = sum(logical_writes_dict.values())
            
            # Calculate System WAF vs EMS WAF
            system_waf = (phys_writes / logical_writes) if logical_writes > 0 else 0
            
            # Capacity check
            used_percent = 0
            storage_ok = True
            try:
                stat = os.statvfs(DATA_DIR)
                total = stat.f_blocks * stat.f_frsize
                free = stat.f_bavail * stat.f_frsize
                used_percent = ((total - free) / total) * 100 if total > 0 else 100
            except:
                storage_ok = False
                
            budget_exceeded = phys_writes > TOTAL_DAILY_PHYSICAL_BUDGET_BYTES
            
            return {
                "daily_logical_writes": logical_writes,
                "daily_logical_breakdown": logical_writes_dict,
                "daily_physical_writes": phys_writes,
                "daily_physical_reads": phys_reads,
                "sqlite_stats": self.get_sqlite_stats(),
                "system_waf": round(system_waf, 2),
                "budget_exceeded": budget_exceeded,
                "used_percent": used_percent,
                "storage_ok": storage_ok,
                "lifetime_logical_writes": self.lifetime_state["lifetime_logical_writes"] + logical_writes,
                "lifetime_physical_writes": self.lifetime_state["lifetime_physical_writes"] + phys_writes,
                "device_serial": self.device_serial
            }