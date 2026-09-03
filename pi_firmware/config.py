import json, os

class Config:
    def __init__(self, path="/etc/ems/config.json"):
        self.path = path
        with open(path) as f:
            data = json.load(f)
            
        self.deviceId = data["deviceId"]
        self.apiKey = data["apiKey"]
        self.backendUrl = data["backendUrl"].rstrip("/")
        self.firmwareVersion = data.get("firmwareVersion", "unknown")
        self.syncIntervalSec = int(data.get("syncIntervalSec", 60))
        self.pendingCommandIntervalSec = int(data.get("pendingCommandIntervalSec", 5))
        self.statePersistIntervalSec = int(data.get("statePersistIntervalSec", 300))
        self.resetDayDefault = int(data.get("resetDayDefault", 15))
        self.timezone = data.get("timezone", "Asia/Kolkata")
        self.serviceName = data.get("serviceName", "ems-controller.service")
        self.stateFile = data.get("stateFile", "/var/lib/ems/state.json")
        self.offlineDbPath = data.get("offlineDbPath", "/var/lib/ems/offline.db")
        
        # PRODUCTION FIX: 8-Wing Hardware Profile
        # A, B, G are enabled and mapped to old wiring. C-H are unmapped.
        self.wings = {
            "A": {"relay": 17, "toggle": 5},
            "B": {"relay": 27, "toggle": 6},
            "G": {"relay": 23, "toggle": 13}
        }
        self.lcd = data.get("lcd", {"i2cBus": 1, "address": "0x27", "cols": 16, "rows": 2})

    @property
    def wing_codes(self):
        return list(self.wings.keys())