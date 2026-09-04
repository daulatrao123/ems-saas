import time
import threading
from config import SYNC_INTERVAL_S
from state import PiStateManager, SystemState, VerificationState, CommandedState, GpioOutputState
from gpio_manager import GPIOManager
from api_client import ApiClient
from offline_queue import OfflineQueue
from storage_manager import StorageManager
import logger as logger_module
from logger import logger

class EmsController:
    def __init__(self):
        logger.info("Initializing EMS Controller...")
        self.storage = StorageManager()
        logger_module.set_storage_manager(self.storage) # Allow logger to check ResourceGuard
        
        self.state_manager = PiStateManager(self.storage)
        self.queue = OfflineQueue(self.storage)
        self.api = ApiClient()
        self.device_config = {}
        self.gpio_manager = None
        self._running = True
        
        self.last_telemetry_day = time.strftime("%Y-%m-%d")

    def validate_config(self, config: dict) -> bool:
        if not config: return False
        if config.get("device_id") != self.api.device_id: return False
        if config.get("hardware_profile") not in ["EMS-4CH-v1"]: return False
        if set(config.get("slots", {}).keys()) != {"A", "B", "C", "D"}: return False
        if not config.get("config_version"): return False
        
        if not isinstance(config.get("feedback_hardware_installed"), bool): return False
        
        for slot, cfg in config.get("slots", {}).items():
            if not isinstance(cfg.get("feedback_enabled"), bool): return False
            if not isinstance(cfg.get("display_name"), str) or not (1 <= len(cfg["display_name"]) <= 50): return False
            if not isinstance(cfg.get("target_days"), int) or not (0 <= cfg["target_days"] <= 365): return False
            if not isinstance(cfg.get("disabled"), bool): return False
            if cfg.get("feedback_enabled") and not config.get("feedback_hardware_installed"):
                logger.error(f"Slot {slot} has feedback enabled, but device hardware does not support it.")
                return False
        return True

    def run_boot_sequence(self):
        self.state_manager.system_state = SystemState.BOOT
        self.state_manager.system_state = SystemState.SELF_TEST
        
        self.storage.guard.evaluate_state()
        if self.storage.guard.state == "STORAGE_FAILED":
            logger.critical("Self-test failed: Storage unavailable. Entering RAM-ONLY DEGRADED MODE.")
            # Control continues, but no persistent logs/state until recovered
        
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
            
        interrupted = self.queue.get_interrupted()
        for cmd_id, slot, action in interrupted:
            logger.warning(f"Command {cmd_id} was interrupted by reboot. Marking UNKNOWN_AFTER_REBOOT.")
            self.queue.update_status(cmd_id, "UNKNOWN_AFTER_REBOOT")
            
            slot_state = self.state_manager.slots[slot]
            is_off = slot_state.gpio_output_state == GpioOutputState.OFF
            
            if action == "ACTIVATE":
                if slot_state.commanded_state == CommandedState.ON:
                    if slot_state.verification_state == VerificationState.VERIFIED_ON:
                        self.queue.update_status(cmd_id, "HARDWARE_VERIFIED", slot_state.verification_state.value)
                        self.queue.update_status(cmd_id, "COMPLETED", slot_state.verification_state.value)
                    elif slot_state.verification_state == VerificationState.GPIO_CONFIRMED:
                        self.queue.update_status(cmd_id, "COMPLETED", slot_state.verification_state.value)
                    else:
                        self.queue.update_status(cmd_id, "FAILED", "MISMATCH_AFTER_REBOOT")
                else:
                    self.queue.update_status(cmd_id, "FAILED", "MISMATCH_AFTER_REBOOT")
                    
            elif action == "DEACTIVATE":
                if is_off:
                    if slot_state.verification_state == VerificationState.VERIFIED_OFF:
                        self.queue.update_status(cmd_id, "HARDWARE_VERIFIED", slot_state.verification_state.value)
                        self.queue.update_status(cmd_id, "COMPLETED", slot_state.verification_state.value)
                    elif slot_state.verification_state in [VerificationState.GPIO_CONFIRMED, VerificationState.NOT_CONFIGURED]:
                        self.queue.update_status(cmd_id, "COMPLETED", slot_state.verification_state.value)
                    else:
                        self.queue.update_status(cmd_id, "FAILED", "MISMATCH_AFTER_REBOOT")
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
        error_msg = None
        
        try:
            if action == "ACTIVATE":
                success = self.gpio_manager.transition_slot(target_slot)
                if success: verification = VerificationState.VERIFIED_ON if self.gpio_manager._is_feedback_enabled(target_slot) else VerificationState.GPIO_CONFIRMED
            elif action == "DEACTIVATE":
                success = self.gpio_manager.deactivate_slot(target_slot)
                if success: verification = VerificationState.VERIFIED_OFF if self.gpio_manager._is_feedback_enabled(target_slot) else VerificationState.GPIO_CONFIRMED
        except Exception as e:
            error_msg = str(e)
            logger.critical(f"Command {cmd_id} execution failed: {e}")
                
        if success:
            if verification in [VerificationState.VERIFIED_ON, VerificationState.VERIFIED_OFF]:
                self.queue.update_status(cmd_id, "HARDWARE_VERIFIED", verification.value)
            self.queue.update_status(cmd_id, "COMPLETED", verification.value)
        else:
            if not error_msg: error_msg = "Transition failed"
            self.queue.update_status(cmd_id, "FAILED", verification.value, error_msg)
            
        self.state_manager.system_state = SystemState.READY

    def sync_loop(self):
        while self._running:
            time.sleep(SYNC_INTERVAL_S)
            
            current_day = time.strftime("%Y-%m-%d")
            if current_day != self.last_telemetry_day:
                self.storage.save_daily_telemetry()
                self.last_telemetry_day = current_day

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