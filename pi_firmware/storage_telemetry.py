import time
import os
import json
import subprocess
from datetime import datetime
from logger import logger

TELEMETRY_FILE = "storage_telemetry.json"

def get_device_name(mount_point="/"):
    """Finds the block device backing the root filesystem."""
    try:
        # Find device mounted at /
        result = subprocess.run(["df", mount_point], capture_output=True, text=True)
        device = result.stdout.split('\n')[1].split()[0]
        # e.g., /dev/sda2 -> sda
        base_device = os.path.basename(device)
        if base_device.startswith("mmcblk"):
            return base_device.replace("p2", "").replace("p1", "")
        elif base_device.startswith("sd"):
            return base_device[0:3] # sda, sdb, etc.
        return None
    except Exception:
        return None

def read_diskstats(device_name):
    """Reads sector writes from /proc/diskstats."""
    if not device_name:
        return None
    try:
        with open("/proc/diskstats", "r") as f:
            for line in f:
                parts = line.split()
                if parts[2] == device_name:
                    # Field 11 (index 10) is "Sectors written"
                    # Note: kernel 5.x+ might need field 11 (index 10) which is writes completed, 
                    # but we want sectors written which is field 11 (0-indexed: 10 is writes, 11 is sectors? 
                    # Actually: 3: reads, 5: read sectors, 7: writes, 9: write sectors. Wait, 0-indexed.
                    # Let's use standard positions:
                    # 0=major, 1=minor, 2=name, 3=reads, 4=read_merged, 5=read_sectors...
                    # 7=writes, 8=writes_merged, 9=write_sectors
                    sectors_written = int(parts[9])
                    return sectors_written
    except Exception as e:
        logger.error(f"Failed to read diskstats: {e}")
        return None
    return None

def run_telemetry(interval_seconds=3600):
    """
    Logs the delta of sectors written every hour.
    Run this for 7-30 days to calculate actual NAND workload.
    1 sector = 512 bytes.
    """
    device = get_device_name("/")
    if not device:
        logger.error("Telemetry: Could not find block device.")
        return

    logger.info(f"Starting storage telemetry on device /dev/{device}")

    # Baseline
    last_sectors = read_diskstats(device)
    last_time = time.time()

    # Ensure baseline file exists
    if not os.path.exists(TELEMETRY_FILE):
        with open(TELEMETRY_FILE, 'w') as f:
            json.dump({"data_points": []}, f)

    while True:
        time.sleep(interval_seconds)
        
        current_sectors = read_diskstats(device)
        current_time = time.time()
        
        if current_sectors is not None and last_sectors is not None:
            delta_sectors = current_sectors - last_sectors
            delta_bytes = delta_sectors * 512
            delta_time_s = current_time - last_time
            
            # Calculate MB/day equivalent rate
            bytes_per_sec = delta_bytes / delta_time_s
            mb_per_day = (bytes_per_sec * 86400) / (1024 * 1024)
            
            data_point = {
                "timestamp": datetime.utcnow().isoformat(),
                "delta_sectors": delta_sectors,
                "delta_mb": delta_bytes / (1024 * 1024),
                "projected_mb_per_day": round(mb_per_day, 2)
            }
            
            # Save to telemetry file
            try:
                with open(TELEMETRY_FILE, 'r+') as f:
                    data = json.load(f)
                    data["data_points"].append(data_point)
                    f.seek(0)
                    json.dump(data, f, indent=4)
                    f.truncate()
                    
                logger.info(f"Telemetry: {delta_bytes / (1024*1024):.2f} MB written in last hour. (Projected: {mb_per_day:.2f} MB/day)")
            except Exception as e:
                logger.error(f"Failed to write telemetry: {e}")
                
        last_sectors = current_sectors
        last_time = current_time

if __name__ == "__main__":
    # Run as a background daemon on the Pi during testing
    run_telemetry()