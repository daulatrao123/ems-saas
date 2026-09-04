import os
import shutil
from config import DATA_DIR, LOG_DIR
from logger import logger

class StorageManager:
    def __init__(self):
        self.storage_ok = True

    def check_health(self):
        try:
            stat = os.statvfs(DATA_DIR)
            total_bytes = stat.f_blocks * stat.f_frsize
            free_bytes = stat.f_bavail * stat.f_frsize
            used_percent = ((total_bytes - free_bytes) / total_bytes) * 100
            
            if used_percent >= 95:
                logger.critical(f"Storage CRITICAL: {used_percent:.1f}% full. Suspending non-essential writes.")
                self.storage_ok = False
                # In production: trigger emergency cleanup
            elif used_percent >= 90:
                logger.error(f"Storage HIGH: {used_percent:.1f}% full. Aggressive cleanup triggered.")
                self.cleanup_logs(aggressive=True)
            elif used_percent >= 80:
                logger.warning(f"Storage WARNING: {used_percent:.1f}% full. Standard cleanup triggered.")
                self.cleanup_logs(aggressive=False)
            else:
                self.storage_ok = True
                
        except Exception as e:
            logger.critical(f"Storage health check failed (USB unmounted?): {e}")
            self.storage_ok = False

    def cleanup_logs(self, aggressive: bool):
        try:
            files = [f for f in os.listdir(LOG_DIR) if f.endswith('.log')]
            files.sort(key=lambda x: os.path.getmtime(os.path.join(LOG_DIR, x)))
            
            # Keep critical.log, delete oldest app logs
            for f in files:
                if 'critical' in f: continue
                if aggressive or len(files) > 5:
                    os.remove(os.path.join(LOG_DIR, f))
                    logger.info(f"Cleaned up log file: {f}")
                    break
        except Exception as e:
            logger.error(f"Log cleanup failed: {e}")