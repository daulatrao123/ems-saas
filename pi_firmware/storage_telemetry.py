import os
import json
import subprocess
from datetime import datetime
from config import DATA_DIR, TELEMETRY_DIR
from logger import logger

class StorageTelemetry:
    def __init__(self):
        self.device_name = self._get_device_name(DATA_DIR)
        if not self.device_name:
            logger.error("Telemetry: Could not find block device for /mnt/ems-data.")
            return
        
        self.cumulative_file = os.path.join(TELEMETRY_DIR, "cumulative_telemetry.json")
        self.state = self._load_cumulative_state()
        
        # Update start sectors for this session
        current_sectors = self._read_diskstats()
        if current_sectors is not None:
            self.state["session_start_sectors"] = current_sectors
            self._save_cumulative_state()

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

    def _load_cumulative_state(self):
        if os.path.exists(self.cumulative_file):
            try:
                with open(self.cumulative_file, 'r') as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "device": self.device_name,
            "lifetime_sectors_written": 0,
            "session_start_sectors": 0
        }

    def _save_cumulative_state(self):
        try:
            tmp = self.cumulative_file + ".tmp"
            with open(tmp, 'w') as f:
                json.dump(self.state, f, indent=4)
            os.replace(tmp, self.cumulative_file)
        except Exception as e:
            logger.error(f"Failed to save telemetry state: {e}")

    def log_daily_usage(self):
        """Called once per day."""
        if not self.device_name: return
        
        current_sectors = self._read_diskstats()
        if current_sectors is None or self.state["session_start_sectors"] == 0:
            return
            
        # Calculate daily delta
        delta_this_session = current_sectors - self.state["session_start_sectors"]
        delta_mb = round((delta_this_session * 512) / (1024 * 1024), 2)
        
        # Update lifetime
        self.state["lifetime_sectors_written"] += delta_this_session
        lifetime_tb = round((self.state["lifetime_sectors_written"] * 512) / (1024**4), 4)
        
        # Append to daily CSV
        daily_file = os.path.join(TELEMETRY_DIR, f"daily_{datetime.utcnow().strftime('%Y-%m-%d')}.csv")
        with open(daily_file, 'a') as f:
            f.write(f"{datetime.utcnow().isoformat()},{self.device_name},{delta_this_session},{delta_mb}\n")
            
        logger.info(f"Telemetry: {delta_mb} MB written today. Lifetime: {lifetime_tb} TB.")
        
        # Reset session start for tomorrow
        self.state["session_start_sectors"] = current_sectors
        self._save_cumulative_state()