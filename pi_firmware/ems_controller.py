import time
import json
import sqlite3
import threading
from config import DB_FILE, SYNC_INTERVAL_S, SQLITE_BUSY_TIMEOUT_MS
from state import PiStateManager, SystemState, CommandedState
from gpio_manager import GPIOManager
from storage_telemetry import StorageTelemetry
from logger import logger

class OfflineQueue:
    def __init__(self):
        self.conn = sqlite3.connect(DB_FILE, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000.0, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS commands (
            id TEXT PRIMARY KEY,
            slot TEXT,
            action TEXT,
            status TEXT DEFAULT 'QUEUED',
            created_at TEXT
        )""")
        self.conn.commit()

    def add_command(self, cmd_id, slot, action):
        try:
            self.conn.execute("INSERT INTO commands (id, slot, action, status, created_at) VALUES (?, ?, ?, ?, ?)",
                              (cmd_id, slot, action, "QUEUED", time.strftime("%Y-%m-%dT%H:%M:%S")))
            self.conn.commit()
        except sqlite3.IntegrityError:
            pass # Idempotency: duplicate command ignored

    def get_next(self):
        cur = self.conn.execute("SELECT id, slot, action FROM commands WHERE status='QUEUED' ORDER BY created_at LIMIT 1")
        return cur.fetchone()

    def mark_status(self, cmd_id, status):
        self.conn.execute("UPDATE commands SET status=? WHERE id=?", (status, cmd_id))
        self.conn.commit()

class CloudSyncManager:
    def __init__(self, controller):
        self.controller = controller
        self._running = True
        self.thread = threading.Thread(target=self._sync_loop, daemon=True)
        self.thread.start()

    def _sync_loop(self):
        while self._running:
            time.sleep(SYNC_INTERVAL_S)
            self.fetch_commands()
            self.push_state()

    def fetch_commands(self):
        # MOCK: In production, this is an HTTP GET to the backend
        # Simulating reception of a command
        # mock_cmd = {"id": "cmd-123", "slot": "B", "action": "ACTIVATE"}
        # self.controller.queue.add_command(mock_cmd["id"], mock_cmd["slot"], mock_cmd["action"])
        pass

    def push_state(self):
        # MOCK: In production, this is an HTTP POST with self.controller.state_manager.get_state_snapshot()
        pass

class EmsController:
    def __init__(self):
        logger.info("Initializing EMS Controller...")
        self.state_manager = PiStateManager()
        self.queue = OfflineQueue()
        self.telemetry = StorageTelemetry()
        
        # Initial device config (In production, fetched from cloud)
        self.device_config = {
            "device_id": "PI-001",
            "hardware_profile": "EMS-4CH-v1",
            "feedback_hardware_installed": True,
            "slots": {
                "A": {"feedback_enabled": True, "display_name": "Solar Main"},
                "B": {"feedback_enabled": True, "display_name": "Generator"},
                "C": {"feedback_enabled": False, "display_name": "Grid"},
                "D": {"feedback_enabled": False, "display_name": "Backup"}
            }
        }
        
        self.gpio_manager = GPIOManager(self.state_manager, self.device_config)
        self.cloud_sync = CloudSyncManager(self)
        self._running = True

    def run_boot_sequence(self):
        self.state_manager.system_state = SystemState.BOOT
        logger.info("System State: BOOT")
        
        self.state_manager.system_state = SystemState.SELF_TEST
        logger.info("System State: SELF_TEST")
        # Add RTC, Storage health checks here
        time.sleep(1)
        
        logger.info("System State: HARDWARE_RECONCILIATION")
        success = self.gpio_manager.reconcile_hardware_state(self.device_config)
        
        if not success:
            logger.critical("System State: FAULT (Interlock or Hardware Mismatch)")
            return False
            
        logger.info("System State: READY")
        return True

    def handle_cloud_command(self, command: tuple):
        cmd_id, target_slot, action = command
        
        if self.state_manager.system_state != SystemState.READY:
            logger.warning(f"Command {cmd_id} rejected. System state is {self.state_manager.system_state.value}")
            return
            
        logger.info(f"Executing command {cmd_id}: {action} slot {target_slot}")
        self.queue.mark_status(cmd_id, "EXECUTING")
        
        is_fb_enabled = self.device_config["slots"][target_slot]["feedback_enabled"]
        success = False
        
        if action == "ACTIVATE":
            success = self.gpio_manager.transition_slot(target_slot, is_fb_enabled)
        elif action == "DEACTIVATE":
            success = self.gpio_manager.deactivate_slot(target_slot, is_fb_enabled)
            
        self.queue.mark_status(cmd_id, "COMPLETED" if success else "FAILED")
        
        if success:
            # TODO: Push ACK to cloud
            pass

    def main_loop(self):
        if not self.run_boot_sequence():
            logger.error("Boot sequence failed. Entering safe fault mode.")
            while self._running:
                time.sleep(1)
                
        logger.info("Entering main operational loop.")
        
        while self._running:
            # 1. Check for pending commands
            cmd = self.queue.get_next()
            if cmd:
                self.handle_cloud_command(cmd)
            else:
                time.sleep(1) # Idle throttle

if __name__ == "__main__":
    controller = EmsController()
    try:
        controller.main_loop()
    except KeyboardInterrupt:
        logger.info("Shutting down EMS Controller...")