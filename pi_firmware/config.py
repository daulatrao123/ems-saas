import json, os

class Config:
    def __init__(self, path="/etc/ems/config.json"):
        self.path = path
        with open(path) as f:
            data = json.load(f)
            
        self.deviceId = data.get("deviceId", os.environ.get("PI_DEVICE_ID"))
        self.apiKey = data.get("apiKey", os.environ.get("PI_API_KEY"))
        self.backendUrl = data.get("backendUrl", os.environ.get("BACKEND_URL", "https://ems-saass.onrender.com")).rstrip("/")
        self.firmwareVersion = data.get("firmwareVersion", "5.3.1-sim")
        self.syncIntervalSec = int(data.get("syncIntervalSec", 60))
        self.pendingCommandIntervalSec = int(data.get("pendingCommandIntervalSec", 5))
        self.statePersistIntervalSec = int(data.get("statePersistIntervalSec", 300))
        self.resetDayDefault = int(data.get("resetDayDefault", 15))
        self.timezone = data.get("timezone", "Asia/Kolkata")
        self.serviceName = data.get("serviceName", "ems-controller.service")
        self.stateFile = data.get("stateFile", f"/tmp/ems_state_{self.deviceId}.json")
        self.offlineDbPath = data.get("offlineDbPath", f"/tmp/ems_queue_{self.deviceId}.db")
        
        # Hardware mapping (centralized)
        self.wings = {
            "A": {"relay": 17, "toggle": 5},
            "B": {"relay": 27, "toggle": 6},
            "G": {"relay": 23, "toggle": 13}
        }
        self.lcd = data.get("lcd", {"i2cBus": 1, "address": "0x27", "cols": 16, "rows": 2})

    @property
    def wing_codes(self):
        return list(self.wings.keys())