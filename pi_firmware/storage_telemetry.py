import time
import os
import subprocess
import csv
from datetime import datetime
from config import TELEMETRY_DIR
from logger import logger

class StorageTelemetry:
    def __init__(self, mount_point="/"):
        self.device_name = self._get_device_name(mount_point)
        if not self.device_name:
            logger.error("Telemetry: Could not find block device.")
            return
        
        self.current_day = datetime.utcnow().strftime("%Y-%m-%d")
        self.daily_file = os.path.join(TELEMETRY_DIR, f"telemetry_{self.current_day}.csv")
        
        # Track sectors written at the start of the day
        self.start_sectors = self._read_diskstats()
        
        if not os.path.exists(self.daily_file):
            with open(self.daily_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['timestamp', 'device', 'sectors_written_delta', 'mb_written'])

    def _get_device_name(self, mount_point):
        try:
            result = subprocess.run(["df", mount_point], capture_output=True, text=True)
            device = result.stdout.split('\n')[1].split()[0]
            base_device = os.path.basename(device)
            if base_device.startswith("mmcblk"):
                return base_device.replace("p2", "").replace("p1", "")
            elif base_device.startswith("sd"):
                return base_device[0:3]
            return None
        except Exception:
            return None

    def _read_diskstats(self):
        if not self.device_name: return None
        try:
            with open("/proc/diskstats", "r") as f:
                for line in f:
                    parts = line.split()
                    if parts[2] == self.device_name:
                        return int(parts[9]) # Sectors written
        except Exception:
            return None

    def log_daily_usage(self):
        """Called once per day (e.g., via cron or internal scheduler)."""
        if not self.device_name: return
        
        current_sectors = self._read_diskstats()
        if current_sectors is None or self.start_sectors is None:
            return
            
        delta_sectors = current_sectors - self.start_sectors
        delta_mb = round((delta_sectors * 512) / (1024 * 1024), 2)
        
        # Append single line to CSV (minimal wear)
        with open(self.daily_file, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([datetime.utcnow().isoformat(), self.device_name, delta_sectors, delta_mb])
            
        logger.info(f"Telemetry: {delta_mb} MB written to disk today.")
        
        # Reset for next day
        self.start_sectors = current_sectors