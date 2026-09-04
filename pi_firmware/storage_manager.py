import os
import time
import threading
from datetime import datetime
from config import DATA_DIR, LOG_DIR, TELEMETRY_DIR
from logger import logger
from storage_io_manager import StorageIOMeter
from memory_manager import MemoryManager
from resource_guard import ResourceGuard

class StorageManager:
    def __init__(self):
        self.io_meter = StorageIOMeter()
        self.memory_monitor = MemoryManager()
        # ResourceGuard handles all protection logic
        self.guard = ResourceGuard(self.io_meter, self.memory_monitor)
        
        self._running = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

    def _monitor_loop(self):
        while self._running:
            time.sleep(60.0)
            # 1. Update RAM metrics
            self.io_meter.update_metrics()
            self.memory_monitor.update_metrics()
            
            # 2. Evaluate degradation state
            self.guard.evaluate_state()
            
            # 3. Read-only capacity cleanup (No write tests!)
            self.cleanup_logs_if_needed()

    def is_write_allowed(self, category: str) -> bool:
        """Modules must ask ResourceGuard before writing to USB."""
        return self.guard.is_write_allowed(category)

    def cleanup_logs_if_needed(self):
        """Read-only capacity check and cleanup."""
        try:
            stat = os.statvfs(DATA_DIR)
            total_bytes = stat.f_blocks * stat.f_frsize
            free_bytes = stat.f_bavail * stat.f_frsize
            used_percent = ((total_bytes - free_bytes) / total_bytes) * 100

            if used_percent >= 98:
                logger.critical(f"Storage EMERGENCY: {used_percent:.1f}% full.")
                self.cleanup_logs(emergency=True)
            elif used_percent >= 95:
                logger.critical(f"Storage CRITICAL: {used_percent:.1f}% full.")
                self.cleanup_logs(emergency=True)
            elif used_percent >= 90:
                logger.error(f"Storage HIGH: {used_percent:.1f}% full. Aggressive cleanup.")
                self.cleanup_logs(aggressive=True)
            elif used_percent >= 80:
                logger.warning(f"Storage WARNING: {used_percent:.1f}% full. Standard cleanup.")
                self.cleanup_logs(aggressive=False)
        except Exception as e:
            logger.critical(f"Storage capacity check failed (USB unmounted?): {e}")

    def cleanup_logs(self, aggressive=False, emergency=False):
        if not self.is_write_allowed("state"): return # Don't cleanup if storage failed
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
                f.write(f"{datetime.utcnow().isoformat()},{io_metrics['daily_logical_writes']},{io_metrics['daily_physical_writes']},{io_metrics['daily_physical_reads']},{io_metrics['system_waf']},{mem_metrics['ram_used']},{mem_metrics['swap_used']},{mem_metrics['ems_rss']},{mem_metrics['memory_state']}\n")
                
            logger.info("Daily storage & memory telemetry flushed to USB.")
        except Exception as e:
            logger.error(f"Failed to save daily telemetry: {e}")