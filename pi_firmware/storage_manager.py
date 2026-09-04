import os
import time
import threading
from datetime import datetime
from config import DATA_DIR, LOG_DIR, TELEMETRY_DIR, TOTAL_DAILY_PHYSICAL_BUDGET_BYTES
from logger import logger
from storage_io_manager import StorageIOMeter
from memory_manager import MemoryManager

class StorageManager:
    def __init__(self):
        self.storage_ok = True
        self.storage_state = "STORAGE_NORMAL"
        self.io_meter = StorageIOMeter()
        self.memory_monitor = MemoryManager()
        self._running = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

    def _monitor_loop(self):
        while self._running:
            time.sleep(60.0)
            self.check_health()
            self.io_meter.update_metrics()
            self.memory_monitor.update_metrics()
            self._evaluate_degradation()

    def _evaluate_degradation(self):
        """Two-way protection: Storage protects memory, Memory protects storage."""
        metrics = self.io_meter.get_metrics()
        mem_pressure = self.memory_monitor.is_memory_pressure()
        
        used_percent = 0
        try:
            stat = os.statvfs(DATA_DIR)
            total = stat.f_blocks * stat.f_frsize
            free = stat.f_bavail * stat.f_frsize
            used_percent = ((total - free) / total) * 100 if total > 0 else 100
        except: pass

        # Determine highest severity state
        new_state = "STORAGE_NORMAL"
        if not self.storage_ok:
            new_state = "STORAGE_FAILED"
        elif used_percent >= 98 or metrics["budget_exceeded"]:
            new_state = "STORAGE_CRITICAL"
        elif used_percent >= 95 or mem_pressure:
            new_state = "WRITE_PROTECTED"
        elif used_percent >= 90:
            new_state = "WRITE_REDUCED"
            
        if new_state != self.storage_state:
            logger.critical(f"Storage state transitioned: {self.storage_state} -> {new_state}")
            self.storage_state = new_state

    def is_write_allowed(self, category: str = "other") -> bool:
        """Centralized write policy enforcement."""
        if self.storage_state == "STORAGE_FAILED":
            return False
        elif self.storage_state == "STORAGE_CRITICAL":
            # Only critical events and state changes allowed
            return category in ["critical_log", "state"]
        elif self.storage_state == "WRITE_PROTECTED":
            # No telemetry, no normal logs
            return category in ["critical_log", "state", "queue_db"]
        elif self.storage_state == "WRITE_REDUCED":
            # No normal logs
            return category != "normal_log"
        return True

    def check_health(self):
        try:
            if not os.path.ismount(DATA_DIR) and DATA_DIR == "/mnt/ems-data":
                logger.warning(f"{DATA_DIR} is not a separate mount.")

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
            else:
                self.storage_ok = True

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
        if not self.is_write_allowed("telemetry"):
            return
            
        try:
            io_metrics = self.io_meter.get_metrics()
            mem_metrics = self.memory_monitor.get_metrics()
            daily_file = os.path.join(TELEMETRY_DIR, f"daily_{datetime.utcnow().strftime('%Y-%m-%d')}.csv")
            
            with open(daily_file, 'a') as f:
                f.write(f"{datetime.utcnow().isoformat()},{io_metrics['daily_logical_writes']},{io_metrics['daily_physical_writes']},{io_metrics['daily_physical_reads']},{io_metrics['waf']},{mem_metrics['ram_used']},{mem_metrics['swap_used']},{mem_metrics['ems_rss']},{mem_metrics['memory_state']}\n")
                
            logger.info("Daily storage & memory telemetry flushed to USB.")
        except Exception as e:
            logger.error(f"Failed to save daily telemetry: {e}")