import os
import time
import threading
import json
from datetime import datetime
from config import (DATA_DIR, LOG_DIR, TELEMETRY_DIR, TOTAL_DAILY_PHYSICAL_BUDGET_BYTES)
from logger import logger

class StorageIOMeter:
    """Tracks actual block device reads/writes in RAM to calculate WAF and daily budget."""
    def __init__(self):
        self.device = self._get_device_name(DATA_DIR)
        self.current_day = datetime.now().strftime("%Y-%m-%d")
        
        # Daily counters (RAM)
        self.daily_logical_writes = 0
        self.daily_physical_writes = 0
        self.daily_physical_reads = 0
        
        # Lifetime counters (persisted safely)
        self.lifetime_state = self._load_lifetime_state()
        self.session_start_sectors = self._read_diskstats()

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
                        # Field 5: read sectors, Field 9: write sectors
                        return (int(parts[5]) * 512, int(parts[9]) * 512)
        except: return (0, 0)

    def _load_lifetime_state(self):
        # Persisted once per day to avoid flash wear
        state_file = os.path.join(TELEMETRY_DIR, "lifetime_counters.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r') as f: return json.load(f)
            except: pass
        return {"lifetime_logical_writes": 0, "lifetime_physical_writes": 0, "lifetime_physical_reads": 0}

    def _save_lifetime_state(self):
        if not os.path.exists(TELEMETRY_DIR): return
        state_file = os.path.join(TELEMETRY_DIR, "lifetime_counters.json")
        try:
            with open(state_file, 'w') as f: json.dump(self.lifetime_state, f)
        except: pass

    def record_ems_write(self, bytes_written):
        self.daily_logical_writes += bytes_written

    def update_metrics(self):
        if not self.device: return
        
        # Check for day rollover
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self.current_day:
            self.lifetime_state["lifetime_logical_writes"] += self.daily_logical_writes
            self.lifetime_state["lifetime_physical_writes"] += self.daily_physical_writes
            self.lifetime_state["lifetime_physical_reads"] += self.daily_physical_reads
            self._save_lifetime_state() # One write per day
            
            self.daily_logical_writes = 0
            self.daily_physical_writes = 0
            self.daily_physical_reads = 0
            self.current_day = today
            self.session_start_sectors = self._read_diskstats()

        # Calculate daily physical I/O
        current_reads, current_writes = self._read_diskstats()
        start_reads, start_writes = self.session_start_sectors
        
        self.daily_physical_writes = current_writes - start_writes
        self.daily_physical_reads = current_reads - start_reads

    def get_metrics(self) -> dict:
        waf = (self.daily_physical_writes / self.daily_logical_writes) if self.daily_logical_writes > 0 else 0
        return {
            "daily_logical_writes": self.daily_logical_writes,
            "daily_physical_writes": self.daily_physical_writes,
            "daily_physical_reads": self.daily_physical_reads,
            "waf": round(waf, 2),
            "lifetime_logical_writes": self.lifetime_state["lifetime_logical_writes"] + self.daily_logical_writes,
            "lifetime_physical_writes": self.lifetime_state["lifetime_physical_writes"] + self.daily_physical_writes,
        }

class MemoryMonitor:
    """Tracks RAM, Swap, and EMS RSS in RAM."""
    def __init__(self):
        self.metrics = {
            "ram_total": 0, "ram_used": 0, "swap_total": 0, "swap_used": 0,
            "swap_in": 0, "swap_out": 0, "oom_kills": 0, "ems_rss": 0
        }

    def update_metrics(self):
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
            # Read EMS process RSS
            pid = os.getpid()
            with open(f"/proc/{pid}/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        self.metrics["ems_rss"] = int(line.split()[1])
                        break
        except: pass

class StorageManager:
    def __init__(self):
        self.storage_ok = True
        self.write_budget_exceeded = False
        self.io_meter = StorageIOMeter()
        self.memory_monitor = MemoryMonitor()
        self._running = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

    def _monitor_loop(self):
        while self._running:
            time.sleep(60.0)
            self.check_health()
            self.io_meter.update_metrics()
            self.memory_monitor.update_metrics()
            
            # HARD PHYSICAL BUDGET ENFORCEMENT
            if self.io_meter.daily_physical_writes > TOTAL_DAILY_PHYSICAL_BUDGET_BYTES:
                if not self.write_budget_exceeded:
                    logger.critical(f"DAILY PHYSICAL USB BUDGET EXCEEDED ({TOTAL_DAILY_PHYSICAL_BUDGET_BYTES / 1024 / 1024}MB). Halting non-critical writes.")
                    self.write_budget_exceeded = True
            else:
                if self.write_budget_exceeded:
                    logger.info("Daily USB write budget recovered (new day).")
                    self.write_budget_exceeded = False

    def is_write_allowed(self, critical: bool = False) -> bool:
        if critical: return True
        return not self.write_budget_exceeded

    def check_health(self):
        try:
            # 1. Check mount status (Read-Only, no I/O write test)
            if not os.path.ismount(DATA_DIR) and DATA_DIR == "/mnt/ems-data":
                logger.warning(f"{DATA_DIR} is not a separate mount.")

            # 2. Check free space
            stat = os.statvfs(DATA_DIR)
            total_bytes = stat.f_blocks * stat.f_frsize
            free_bytes = stat.f_bavail * stat.f_frsize
            used_percent = ((total_bytes - free_bytes) / total_bytes) * 100

            if used_percent >= 98:
                logger.critical(f"Storage EMERGENCY: {used_percent:.1f}% full.")
                self.storage_ok = False
                self.cleanup_logs(emergency=True)
            elif used_percent >= 95:
                logger.critical(f"Storage CRITICAL: {used_percent:.1f}% full.")
                self.storage_ok = False
                self.cleanup_logs(emergency=True)
            elif used_percent >= 90:
                logger.error(f"Storage HIGH: {used_percent:.1f}% full. Aggressive cleanup.")
                self.cleanup_logs(aggressive=True)
            elif used_percent >= 80:
                logger.warning(f"Storage WARNING: {used_percent:.1f}% full. Standard cleanup.")
                self.cleanup_logs(aggressive=False)

            # 3. Read-only filesystem detection (without write test)
            # We rely on OS mount flags or failed writes rather than a periodic write test.
            
        except Exception as e:
            logger.critical(f"Storage health check failed (USB unmounted?): {e}")
            self.storage_ok = False

    def cleanup_logs(self, aggressive=False, emergency=False):
        try:
            files = [f for f in os.listdir(LOG_DIR) if f.endswith('.log')]
            files.sort(key=lambda x: os.path.getmtime(os.path.join(LOG_DIR, x)))

            if emergency:
                for f in files:
                    if 'critical' not in f:
                        os.remove(os.path.join(LOG_DIR, f))
                logger.info("Emergency log cleanup executed.")
                return

            now = time.time()
            for f in files:
                filepath = os.path.join(LOG_DIR, f)
                if 'critical' in f: continue
                file_age = now - os.path.getmtime(filepath)
                if file_age > (7 * 86400) or (aggressive and len(files) > 2):
                    os.remove(filepath)
                    logger.info(f"Cleaned up log file: {f}")
        except Exception as e:
            logger.error(f"Log cleanup failed: {e}")

    def save_daily_telemetry(self):
        """Flushes RAM telemetry to USB exactly once per day."""
        if not self.is_write_allowed():
            return
            
        try:
            metrics = {**self.io_meter.get_metrics(), **self.memory_monitor.metrics}
            daily_file = os.path.join(TELEMETRY_DIR, f"daily_{datetime.utcnow().strftime('%Y-%m-%d')}.csv")
            with open(daily_file, 'a') as f:
                f.write(f"{datetime.utcnow().isoformat()},{metrics['daily_logical_writes']},{metrics['daily_physical_writes']},{metrics['daily_physical_reads']},{metrics['waf']},{metrics['ram_used']},{metrics['swap_used']},{metrics['ems_rss']}\n")
            logger.info("Daily storage & memory telemetry flushed to USB.")
        except Exception as e:
            logger.error(f"Failed to save daily telemetry: {e}")