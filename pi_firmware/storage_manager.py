import os
import time
import threading
from datetime import datetime
from config import DATA_DIR, LOG_DIR, QUEUE_DIR, TELEMETRY_DIR, STATE_DIR
from logger import logger

class StorageIOMeter:
    """Tracks actual block device writes in RAM to detect write amplification and budget endurance."""
    def __init__(self):
        self.device = self._get_device_name(DATA_DIR)
        self.last_sectors_written = self._read_diskstats()
        self.ram_metrics = {
            "ems_writes": 0, "ems_read_ops": 0, "block_writes_bytes": 0,
            "write_rate_mbps": 0.0, "ram_total": 0, "ram_used": 0, "swap_used": 0
        }
        self.lock = threading.Lock()

    def _get_device_name(self, path):
        try:
            result = os.popen(f"df {path}").read().split('\n')[1].split()[0]
            base = os.path.basename(result)
            if base.startswith("mmcblk"): return base.replace("p2", "").replace("p1", "")
            if base.startswith("sd"): return base[:3]
            return None
        except: return None

    def _read_diskstats(self):
        if not self.device: return 0
        try:
            with open("/proc/diskstats", "r") as f:
                for line in f:
                    if line.split()[2] == self.device:
                        return int(line.split()[9]) * 512 # Sectors to bytes
        except: return 0

    def record_ems_write(self, bytes_written):
        with self.lock:
            self.ram_metrics["ems_writes"] += bytes_written

    def update_metrics(self):
        if not self.device: return
        with self.lock:
            current_block_writes = self._read_diskstats()
            self.ram_metrics["block_writes_bytes"] = current_block_writes
            
            # Read RAM/Swap from /proc/meminfo
            try:
                with open("/proc/meminfo", "r") as f:
                    meminfo = dict(line.split(":") for line in f.readlines())
                    self.ram_metrics["ram_total"] = int(meminfo.get("MemTotal", "0").strip().split()[0])
                    self.ram_metrics["ram_used"] = self.ram_metrics["ram_total"] - int(meminfo.get("MemAvailable", "0").strip().split()[0])
                    self.ram_metrics["swap_used"] = int(meminfo.get("SwapTotal", "0").strip().split()[0]) - int(meminfo.get("SwapFree", "0").strip().split()[0])
            except: pass

    def get_daily_summary(self) -> dict:
        return self.ram_metrics.copy()

class StorageManager:
    def __init__(self):
        self.storage_ok = True
        self.io_meter = StorageIOMeter()
        self._running = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

    def _monitor_loop(self):
        while self._running:
            time.sleep(60.0) # Check every minute
            self.check_health()
            self.io_meter.update_metrics()
            
            # Write-rate protection (Alert if > 50MB/hour average)
            # 50MB * 60min = 3000MB. In bytes: 3000 * 1024 * 1024
            if self.io_meter.ram_metrics["block_writes_bytes"] > 3000 * 1024 * 1024:
                logger.critical("STORAGE WRITE RATE EXCEEDED! Possible runaway process or write amplification.")
                self.storage_ok = False

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

            test_file = os.path.join(DATA_DIR, ".health_test")
            try:
                with open(test_file, 'w') as f: f.write("ok")
                os.remove(test_file)
            except OSError as e:
                if e.errno == 30: # Read-only filesystem
                    logger.critical("Storage CRITICAL: Filesystem is READ-ONLY.")
                    self.storage_ok = False
                else: raise
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
        try:
            metrics = self.io_meter.get_daily_summary()
            daily_file = os.path.join(TELEMETRY_DIR, f"daily_{datetime.utcnow().strftime('%Y-%m-%d')}.csv")
            with open(daily_file, 'a') as f:
                f.write(f"{datetime.utcnow().isoformat()},{metrics['ems_writes']},{metrics['block_writes_bytes']},{metrics['ram_used']},{metrics['swap_used']}\n")
            # Reset RAM counters for the next day
            self.io_meter.ram_metrics["ems_writes"] = 0
            logger.info("Daily storage telemetry flushed to USB.")
        except Exception as e:
            logger.error(f"Failed to save daily telemetry: {e}")