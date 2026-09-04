import os
import time
from config import DATA_DIR, LOG_DIR
from logger import logger

class StorageManager:
    def __init__(self):
        self.storage_ok = True

    def check_health(self):
        try:
            if not os.path.ismount(DATA_DIR) and DATA_DIR == "/mnt/ems-data":
                logger.warning(f"{DATA_DIR} is not a separate mount. USB isolation inactive.")
            
            stat = os.statvfs(DATA_DIR)
            total_bytes = stat.f_blocks * stat.f_frsize
            free_bytes = stat.f_bavail * stat.f_frsize
            used_percent = ((total_bytes - free_bytes) / total_bytes) * 100
            
            if used_percent >= 98:
                logger.critical(f"Storage EMERGENCY: {used_percent:.1f}% full. Halting non-essential writes.")
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
            elif used_percent >= 70:
                logger.info(f"Storage NOTICE: {used_percent:.1f}% full.")
            else:
                self.storage_ok = True
                
            # Read-only / I/O test
            test_file = os.path.join(DATA_DIR, ".health_test")
            try:
                with open(test_file, 'w') as f: f.write("ok")
                os.remove(test_file)
            except OSError as e:
                if e.errno == 30: # Read-only filesystem
                    logger.critical("Storage CRITICAL: Filesystem is READ-ONLY.")
                    self.storage_ok = False
                else:
                    raise
                    
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