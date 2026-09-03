import json, os, time, threading, uuid
from datetime import datetime
import pytz

class State:
    def __init__(self, config, logger, clock_source, db):
        self.cfg = config
        self.log = logger
        self.db = db
        self._lock = threading.RLock()
        self._dirty = False
        self._last_persist = 0
        self.tz = pytz.timezone(config.timezone)
        
        self.boot_count = 1
        self.last_shutdown_reason = "COLD_BOOT"
        boot_flag_file = os.path.join(os.path.dirname(config.stateFile), "boot_flag")
        if os.path.exists(boot_flag_file):
            with open(boot_flag_file, "r") as f:
                reason = f.read().strip()
                if reason: self.last_shutdown_reason = reason
                os.remove(boot_flag_file)
                
        self.clock_source = clock_source
        self.last_reboot_reason = self.last_shutdown_reason
        self.config_version = 0
        
        # Load persistent reset period from DB
        self.last_reset_period = self.db.get_state("last_reset_period") or self._local_now().strftime("%Y-%m")
        
        # 1. Initialize defaults FIRST
        self.data = {
            "deviceId": config.deviceId,
            "firmwareVersion": config.firmwareVersion,
            "activeWing": None,
            "resetDay": config.resetDayDefault,
            "emergencyStop": False,
            "watchdogEnabled": True,
            "lastRebootReason": self.last_reboot_reason,
            "lastShutdownReason": self.last_shutdown_reason,
            "bootCount": self.boot_count,
            "clockSource": clock_source,
            "diskFreeMB": 0,
            "lastSync": None,
            "cpuTemp": 0.0,
            "uptimeSeconds": 0,
            "wings": {
                w: {
                    "usedDays": 0,
                    "targetDays": 0, 
                    "lastActiveDate": None, 
                    "physicalToggle": "UNKNOWN",
                    "disabled": False,
                    "clicks": 0
                } for w in config.wing_codes
            },
            "events": []
        }
        
        # 2. Load persisted state from disk (safely overwrites defaults)
        if os.path.exists(config.stateFile):
            self._load_from_disk()
            self.boot_count += 1
            self.data["bootCount"] = self.boot_count
            
        self.data["lastShutdownReason"] = self.last_shutdown_reason

    def _local_now(self):
        return datetime.now(self.tz)

    def _load_from_disk(self):
        try:
            if os.path.exists(self.cfg.stateFile):
                with open(self.cfg.stateFile) as f:
                    saved = json.load(f)
                wings = saved.get("wings", {})
                for w in self.cfg.wing_codes:
                    if w in wings:
                        self.data["wings"][w]["usedDays"] = int(wings[w].get("usedDays", 0))
                        self.data["wings"][w]["lastActiveDate"] = wings[w].get("lastActiveDate")
                self.data["activeWing"] = saved.get("activeWing")
                self.data["resetDay"] = int(saved.get("resetDay", self.cfg.resetDayDefault))
                self.boot_count = int(saved.get("bootCount", 0)) + 1
                self.config_version = int(saved.get("configVersion", 0))
        except Exception as e:
            self.log.warning(f"State load failed: {e}")

    def force_persist(self):
        os.makedirs(os.path.dirname(self.cfg.stateFile), exist_ok=True)
        tmp = self.cfg.stateFile + ".tmp"
        slim = {
            "activeWing": self.data["activeWing"],
            "resetDay": self.data["resetDay"],
            "bootCount": self.data["bootCount"],
            "configVersion": self.config_version,
            "wings": {
                w: {
                    "usedDays": self.data["wings"][w]["usedDays"],
                    "lastActiveDate": self.data["wings"][w]["lastActiveDate"]
                } for w in self.cfg.wing_codes
            }
        }
        with open(tmp, "w") as f:
            json.dump(slim, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.cfg.stateFile)
        self._last_persist = time.time()
        self._dirty = False

    def maybe_persist(self):
        with self._lock:
            if self._dirty and (time.time() - self._last_persist) >= self.cfg.statePersistIntervalSec:
                self.force_persist()

    def prepare_for_shutdown(self, reason):
        flag_file = os.path.join(os.path.dirname(self.cfg.stateFile), "boot_flag")
        with open(flag_file, "w") as f: f.write(reason)
        self.data["lastShutdownReason"] = reason
        self.force_persist()

    def apply_cloud_config(self, cloud_version, cloud_wings, reset_day):
        with self._lock:
            if reset_day:
                self.data["resetDay"] = int(reset_day)
            if cloud_wings:
                for wid, conf in cloud_wings.items():
                    if wid in self.data["wings"]:
                        self.data["wings"][wid]["targetDays"] = int(conf.get("target_days", 0))
                        self.data["wings"][wid]["disabled"] = bool(conf.get("disabled", False))
            if cloud_version and cloud_version > self.config_version:
                self.config_version = cloud_version
                self.force_persist()
                self.log.info(f"Applied cloud config v{cloud_version}")
            self._dirty = True

    def set_active_wing(self, wing):
        with self._lock:
            if self.data["emergencyStop"]:
                self.log.warning("Emergency stop active. Cannot activate wing.")
                return False
            today_str = self._local_now().strftime("%Y-%m-%d")
            w_state = self.data["wings"][wing]
            if w_state["lastActiveDate"] != today_str:
                w_state["usedDays"] += 1
                w_state["lastActiveDate"] = today_str
                self.force_persist()
            self.data["activeWing"] = wing
            self._dirty = True
            return True

    def off_wing(self, wing):
        with self._lock:
            if self.data["activeWing"] == wing:
                self.data["activeWing"] = None
            self._dirty = True

    def off_all(self):
        with self._lock:
            self.data["activeWing"] = None
            self._dirty = True

    def set_reset_day(self, day):
        with self._lock:
            self.data["resetDay"] = day
            self._dirty = True

    def reset_days(self):
        with self._lock:
            for w in self.cfg.wing_codes:
                self.data["wings"][w]["usedDays"] = 0
                self.data["wings"][w]["lastActiveDate"] = None
            self.force_persist()

    def update_toggles(self, gpio_mgr):
        with self._lock:
            for w in self.cfg.wing_codes:
                self.data["wings"][w]["physicalToggle"] = gpio_mgr.get_physical_toggle(w)

    def add_event(self, event_type, message):
        ev = {
            "eventId": str(uuid.uuid4()),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "type": event_type,
            "message": message
        }
        self.db.push_event(ev["eventId"], ev)
        with self._lock:
            self.data["events"].append(ev)
            if len(self.data["events"]) > 20:
                self.data["events"].pop(0)

    def clear_events(self):
        with self._lock:
            self.data["events"] = []

    def update_metrics(self, uptime, temp, disk):
        with self._lock:
            self.data["uptimeSeconds"] = uptime
            self.data["cpuTemp"] = round(temp, 1)
            self.data["diskFreeMB"] = disk

    def mark_sync(self):
        with self._lock:
            self.data["lastSync"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def to_sync_payload(self):
        with self._lock:
            return {
                "deviceId": self.data["deviceId"],
                "key": self.cfg.apiKey,
                "firmwareVersion": self.data["firmwareVersion"],
                "activeWing": self.data["activeWing"],
                "resetDay": self.data["resetDay"],
                "emergencyStop": self.data["emergencyStop"],
                "uptimeSeconds": self.data["uptimeSeconds"],
                "cpuTemp": self.data["cpuTemp"],
                "diskFreeMB": self.data["diskFreeMB"],
                "bootCount": self.data["bootCount"],
                "lastShutdownReason": self.data["lastShutdownReason"],
                "clockSource": self.data["clockSource"],
                "watchdogEnabled": self.data["watchdogEnabled"],
                "lastRebootReason": self.data["lastRebootReason"],
                "wings": {
                    w: {
                        "usedDays": self.data["wings"][w]["usedDays"],
                        "physicalToggle": self.data["wings"][w]["physicalToggle"],
                        "clicks": self.data["wings"][w]["clicks"]
                    } for w in self.cfg.wing_codes
                },
                "events": self.data["events"]
            }