import time
import threading
from config import get_hardware_profile, SYNC_INTERVAL_S
from state import PiStateManager, SystemState, CommandedState
from gpio_manager import GPIOManager
from logger import logger

class EmsController:
    def __init__(self):
        logger.info("Initializing EMS Controller...")
        self.state_manager = PiStateManager()
        self.gpio_manager = GPIOManager(self.state_manager)
        self._running = True
        
        # Placeholder for config fetched from cloud
        self.cloud_config = {
            "feedback_hardware_installed": True,
            "slots": {
                "A": {"feedback_enabled": True},
                "B": {"feedback_enabled": True},
                "C": {"feedback_enabled": False},
                "D": {"feedback_enabled": False}
            }
        }

    def run_boot_sequence(self):
        """Executes the industrial boot sequence."""
        self.state_manager.system_state = SystemState.BOOT
        logger.info("System State: BOOT")
        
        # 1. Self Test (could expand to check RTC, Storage, etc.)
        self.state_manager.system_state = SystemState.SELF_TEST
        logger.info("System State: SELF_TEST")
        time.sleep(1) # Simulate test
        
        # 2. Hardware Reconciliation (Hardware wins)
        logger.info("System State: HARDWARE_RECONCILIATION")
        fb_configured_map = {
            slot: self.cloud_config["feedback_hardware_installed"] and cfg["feedback_enabled"]
            for slot, cfg in self.cloud_config["slots"].items()
        }
        
        success = self.gpio_manager.reconcile_hardware_state(fb_configured_map)
        
        if not success:
            logger.critical("System State: FAULT (Interlock or Hardware Mismatch detected at boot)")
            # System is locked out until manual intervention
            return False
            
        logger.info("System State: READY")
        return True

    def handle_cloud_command(self, command: dict):
        """Handles commands from the offline queue/cloud sync."""
        if self.state_manager.system_state != SystemState.READY:
            logger.warning(f"Command rejected. System state is {self.state_manager.system_state.value}")
            return False
            
        target_slot = command.get("slot")
        action = command.get("action")
        
        if target_slot not in ["A", "B", "C", "D"]:
            return False
            
        if action == "ACTIVATE":
            logger.info(f"Executing command: ACTIVATE slot {target_slot}")
            fb_cfg = self.cloud_config["slots"][target_slot]["feedback_enabled"]
            return self.gpio_manager.transition_slot(target_slot, fb_cfg)
            
        elif action == "DEACTIVATE":
            logger.info(f"Executing command: DEACTIVATE slot {target_slot}")
            # Logic to turn off specific slot
            # self.gpio_manager.relays[target_slot].off()
            # self.state_manager.set_commanded(target_slot, CommandedState.OFF)
            return True

    def main_loop(self):
        """Main runtime loop."""
        if not self.run_boot_sequence():
            logger.error("Boot sequence failed. Entering safe fault mode.")
            while self._running:
                time.sleep(1) # Wait for watchdog to restart or manual fix
                
        logger.info("Entering main operational loop.")
        
        while self._running:
            # 1. Check for pending commands from CloudSync/OfflineQueue
            # pending_cmd = self.command_queue.get_next()
            # if pending_cmd: self.handle_cloud_command(pending_cmd)
            
            # 2. Periodic cloud sync
            time.sleep(SYNC_INTERVAL_S)

if __name__ == "__main__":
    controller = EmsController()
    try:
        controller.main_loop()
    except KeyboardInterrupt:
        logger.info("Shutting down EMS Controller...")