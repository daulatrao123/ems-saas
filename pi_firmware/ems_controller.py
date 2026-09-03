#!/usr/bin/env python3
import signal, time, threading, os, random, subprocess
import sdnotify
from datetime import datetime
import pytz

from config import Config
from logger import setup_logger
from state import State
from api_client import ApiClient
from gpio_manager import GpioManager
from lcd_display import Lcd
from commands import CommandHandler
from ds3231 import RTC
from offline_queue import OfflineQueue
import system_info

class EmsController:
    def __init__(self):
        self.cfg = Config(path=os.environ.get("EMS_CONFIG", "/etc/ems/config.json"))
        self.log = setup_logger()
        
        self.rtc = RTC(self.log)
        clock_src = self.rtc.sync_to_system()
        
        self.db = OfflineQueue(self.cfg, self.log)
        self.state = State(self.cfg, self.log, clock_src, self.db)
        self.lcd = Lcd(self.cfg, self.log)
        self.lcd.show("EMS Booting", f"fw {self.cfg.firmwareVersion}")
        
        self.api = ApiClient(self.cfg, self.log)
        self.gpio = GpioManager(self.cfg, self.log)
        self.cmd_handler = CommandHandler(self.cfg, self.state, self.gpio, self.lcd, self.log, self.api, self.db)

        self.notifier = sdnotify.SystemdNotifier()
        self._stop = threading.Event()
        self._last_heartbeat = time.time()
        self._last_rtc_sync = 0

        # PRODUCTION FIX: Crash Recovery & Reconciliation
        self._reconcile_interrupted_commands()

    def _reconcile_interrupted_commands(self):
        """Check for commands that were STARTED but never completed (due to crash/power loss)."""
        interrupted = self.db.get_interrupted_commands()
        if not interrupted:
            return
            
        self.log.warning(f"CRASH RECOVERY: Found {len(interrupted)} interrupted commands. Reconciling hardware state...")
        
        for cid in interrupted:
            # In a full system, we would check the cloud's desired state vs actual GPIO here.
            # For now, we mark them as UNKNOWN_AFTER_REBOOT so the cloud knows the state is uncertain.
            self.db.update_command_status(cid, "UNKNOWN_AFTER_REBOOT")
            self.state.add_event("SYSTEM", f"Crash recovery: Cmd {cid[:8]} marked UNKNOWN")
            
        self.log.info("Crash recovery complete. Interrupted commands marked.")

    def _sync_loop(self):
        while not self._stop.is_set():
            self._last_heartbeat = time.time()
            
            now = self._local_now()
            today = now.tm_mday
            current_period = now.strftime("%Y-%m")
            
            if today >= self.state.data["resetDay"] and current_period != self.state.last_reset_period:
                self.log.info(f"Reset day reached for {current_period}. Zeroing used_days.")
                self.state.reset_days()
                self.state.add_event("SYSTEM", "Monthly reset executed")
                self.state.last_reset_period = current_period
                self.db.set_state("last_reset_period", current_period)

            self.state.update_toggles(self.gpio)
            self.state.update_metrics(system_info.uptime_seconds(), system_info.cpu_temp(), system_info.disk_free_mb())
            self.state.mark_sync()
            
            offline_events = self.db.load_events()
            for _, ev_id, payload in offline_events:
                if not any(e["eventId"] == ev_id for e in self.state.data["events"]):
                    import json
                    self.state.data["events"].append(json.loads(payload))
            
            payload = self.state.to_sync_payload()
            resp = self.api.sync(payload)

            if resp:
                cloud_version = resp.get("configVersion")
                cloud_wings = resp.get("wingConfigs")
                reset_day = resp.get("resetDay")
                if cloud_wings or reset_day:
                    self.state.apply_cloud_config(cloud_version, cloud_wings, reset_day)

                self.state.clear_events()
                for db_id, _, _ in offline_events:
                    self.db.delete_event(db_id)
                
                if self.state.clock_source == "NTP" and (time.time() - self._last_rtc_sync) > 86400:
                    if self.rtc.sync_from_system():
                        self._last_rtc_sync = time.time()

                pending_acks = self.db.load_acks()
                for ack in pending_acks:
                    if self.api.ack_command(ack["command_id"], ack["success"], ack["result"], ack["error"]):
                        self.db.delete_ack(ack["command_id"])

                cmd_type = resp.get("command")
                cmd_id = resp.get("command_id")
                
                if cmd_type and cmd_id:
                    self.log.info(f"Received command: {cmd_type}")
                    wing = resp.get("wing")
                    params = resp.get("params") or {}
                    
                    cmd_payload = {
                        "command_id": cmd_id,
                        "command": cmd_type,
                        "wing": wing,
                        "params": params
                    }
                    
                    # Execute via the strict state machine handler
                    ack_data = self.cmd_handler.execute(cmd_payload)
                    
                    if ack_data:
                        cid, success, result, error = ack_data
                        self.state.force_persist()
                        if not self.api.ack_command(cid, success, result, error):
                            self.db.save_ack(cid, success, result, error)
                else:
                    self.state.maybe_persist()
            else:
                self.state.maybe_persist()

            has_pending = bool(self.db.load_acks())
            wait = self.cfg.pendingCommandIntervalSec if has_pending else self.cfg.syncIntervalSec
            jitter = random.uniform(0, 10)
            self._stop.wait(wait + jitter)

    def _watchdog_loop(self):
        while not self._stop.is_set():
            if (time.time() - self._last_heartbeat) > 60:
                self.log.error("Core sync loop stalled! Stopping watchdog notifications to trigger systemd restart.")
                break
            
            self.notifier.notify("WATCHDOG=1")
            self._stop.wait(10)

    def _local_now(self):
        tz = pytz.timezone(self.cfg.timezone)
        return datetime.now(tz).timetuple()

    def start(self):
        self.log.info(f"EMS controller starting (firmware {self.cfg.firmwareVersion})")
        self.notifier.notify("READY=1")
        threads = [
            threading.Thread(target=self._sync_loop, name="sync", daemon=True),
            threading.Thread(target=self._watchdog_loop, name="watchdog", daemon=True),
        ]
        for t in threads: t.start()
        for t in threads: t.join()

    def stop(self, signum=None, frame=None):
        self.log.info("EMS controller stopping — flushing state")
        self._stop.set()
        try:
            self.state.force_persist()
            self.gpio.cleanup()
        except Exception:
            pass

if __name__ == "__main__":
    c = EmsController()
    signal.signal(signal.SIGTERM, c.stop)
    signal.signal(signal.SIGINT,  c.stop)
    try:
        c.start()
    finally:
        c.stop()