import time
import threading
from config import SYNC_INTERVAL_S
from state import PiStateManager, SystemState, VerificationState
from gpio_manager import GPIOManager
from api_client import ApiClient
from offline_queue import OfflineQueue
from storage_manager import StorageManager
from logger import logger

class EmsController:
    def __init__(self):
        logger.info("Initializing EMS Controller...")
        self.state_manager = PiStateManager()
        self.queue = OfflineQueue()
        self.api = ApiClient()
        self.storage = StorageManager()
        self.device_config = {}
        self.gpio_manager = None
        self._running = True
        
    def run_boot_sequence(self):
        self.state_manager.system_state = SystemState.BOOT
        self.state_manager.system_state = SystemState.SELF_TEST
        
        self.storage.check_health()
        if not self.storage.storage_ok:
            logger.critical("Self-test failed: Storage unavailable.")
            self.state_manager.system_state = SystemState.FAULT
            return False
            
        logger.info("Fetching cloud configuration...")
        self.device_config = self.api.get_config()
        
        if not self.device_config:
            logger.error("Cloud offline. Cannot fetch config. Entering CLOUD_OFFLINE state.")
            self.state_manager.system_state = SystemState.CLOUD_OFFLINE
            return False
            
        self.gpio_manager = GPIOManager(self.state_manager, self.device_config)
        
        logger.info("System State: HARDWARE_RECONCILIATION")
        success = self.gpio_manager.reconcile_hardware_state()
        
        if not success:
            logger.critical("System State: FAULT (Hardware Mismatch)")
            return False
            
        logger.info("System State: READY")
        return True

    def handle_command(self, command: tuple):
        cmd_id, target_slot, action = command
        logger.info(f"Executing command {cmd_id}: {action} slot {target_slot}")
        
        self.queue.update_status(cmd_id, "EXECUTING")
        self.state_manager.system_state = SystemState.EXECUTING
        
        is_fb_enabled = self.device_config.get("slots", {}).get(target_slot, {}).get("feedback_enabled", False)
        success = False
        verification = VerificationState.NOT_CONFIGURED
        
        if action == "ACTIVATE":
            success = self.gpio_manager.transition_slot(target_slot, is_fb_enabled)
            if success: verification = VerificationState.VERIFIED_ON if is_fb_enabled else VerificationState.GPIO_CONFIRMED
        elif action == "DEACTIVATE":
            success = self.gpio_manager.deactivate_slot(target_slot, is_fb_enabled)
            if success: verification = VerificationState.VERIFIED_OFF if is_fb_enabled else VerificationState.GPIO_CONFIRMED
            
        self.queue.update_status(cmd_id, "COMPLETED" if success else "FAILED", verification.value)
        self.state_manager.system_state = SystemState.READY

    def sync_loop(self):
        while self._running:
            time.sleep(SYNC_INTERVAL_S)
            if self.state_manager.system_state == SystemState.READY:
                snapshot = {
                    "system_state": self.state_manager.system_state.value,
                    "active_slot": self.state_manager.active_slot,
                    "slots": {c: s.to_dict() for c, s in self.state_manager.slots.items()}
                }
                self.api.push_state(snapshot)
                
                commands = self.api.get_commands()
                for cmd in commands:
                    self.queue.add_command(cmd["id"], cmd["slot"], cmd["action"], cmd.get("created_at"), cmd.get("expires_at"))
                
                unacked = self.queue.get_unacked()
                for cmd_id, status, verif in unacked:
                    if self.api.push_ack(cmd_id, status, verif):
                        self.queue.mark_acked(cmd_id)
                    break

    def main_loop(self):
        if not self.run_boot_sequence():
            logger.error("Boot sequence failed. Entering safe fault mode.")
            while self._running: time.sleep(1)
                
        logger.info("Entering main operational loop.")
        sync_thread = threading.Thread(target=self.sync_loop, daemon=True)
        sync_thread.start()
        
        while self._running:
            cmd = self.queue.get_next()
            if cmd:
                self.handle_command(cmd)
            else:
                time.sleep(1)

if __name__ == "__main__":
    controller = EmsController()
    try:
        controller.main_loop()
    except KeyboardInterrupt:
        logger.info("Shutting down EMS Controller...")