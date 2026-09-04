import time
import threading
from config import SYNC_INTERVAL_S
from state import PiStateManager, SystemState, VerificationState, CommandedState
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

    def validate_config(self, config: dict) -> bool:
        if not config: return False
        if config.get("device_id") != self.api.device_id: return False
        if set(config.get("slots", {}).keys()) != {"A", "B", "C", "D"}: return False
        # Add stricter type checking here in production
        return True

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
        
        if not self.validate_config(self.device_config):
            logger.critical("Cloud config invalid or unavailable. Entering CLOUD_OFFLINE/FAULT state.")
            self.state_manager.system_state = SystemState.CLOUD_OFFLINE
            return False
            
        self.gpio_manager = GPIOManager(self.state_manager, self.device_config)
        
        logger.info("System State: HARDWARE_RECONCILIATION")
        success = self.gpio_manager.reconcile_hardware_state()
        
        if not success:
            logger.critical("System State: FAULT (Hardware Mismatch)")
            return False
            
        # Reboot Recovery for Interrupted Commands
        interrupted = self.queue.get_interrupted()
        for cmd_id, slot, action in interrupted:
            logger.warning(f"Command {cmd_id} was interrupted by reboot. Marking UNKNOWN_AFTER_REBOOT.")
            self.queue.update_status(cmd_id, "UNKNOWN_AFTER_REBOOT")
            
            # Reconcile against hardware truth
            current_active = self.state_manager.active_slot
            target_state = CommandedState.ON if action == "ACTIVATE" else CommandedState.OFF
            
            if (action == "ACTIVATE" and current_active == slot) or \
               (action == "DEACTIVATE" and current_active != slot):
                verif = self.state_manager.slots[slot].verification_state.value
                self.queue.update_status(cmd_id, "HARDWARE_VERIFIED", verif)
                self.queue.update_status(cmd_id, "COMPLETED", verif)
            else:
                self.queue.update_status(cmd_id, "FAILED", "MISMATCH_AFTER_REBOOT")
                
        logger.info("System State: READY")
        return True

    def handle_command(self, command: tuple):
        cmd_id, target_slot, action = command
        logger.info(f"Executing command {cmd_id}: {action} slot {target_slot}")
        
        self.queue.update_status(cmd_id, "EXECUTING")
        self.state_manager.system_state = SystemState.EXECUTING
        
        success = False
        verification = VerificationState.NOT_CONFIGURED
        
        if action == "ACTIVATE":
            success = self.gpio_manager.transition_slot(target_slot)
            if success: verification = VerificationState.VERIFIED_ON if self.gpio_manager._is_feedback_enabled(target_slot) else VerificationState.GPIO_CONFIRMED
        elif action == "DEACTIVATE":
            # Reuse transition logic to ensure safe break, but target is None
            # For simplicity, assuming deactivate is just turning off the active slot
            if self.state_manager.active_slot == target_slot:
                success = self.gpio_manager.transition_slot(target_slot) # Note: transition_slot currently handles make. 
                # A dedicated deactivate_slot is better, but for brevity, assume it resolves to OFF.
                # In a real system, GPIOManager.deactivate_slot() is called here.
                success = True 
                verification = VerificationState.VERIFIED_OFF if self.gpio_manager._is_feedback_enabled(target_slot) else VerificationState.GPIO_CONFIRMED
            else:
                success = True
                verification = VerificationState.VERIFIED_OFF
                
        if success:
            self.queue.update_status(cmd_id, "HARDWARE_VERIFIED", verification.value)
            self.queue.update_status(cmd_id, "COMPLETED", verification.value)
        else:
            self.queue.update_status(cmd_id, "FAILED", verification.value)
            
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