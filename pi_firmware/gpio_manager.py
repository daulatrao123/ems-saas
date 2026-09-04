import time
import threading
from config import get_hardware_profile, FEEDBACK_TIMEOUT_MS, FEEDBACK_DEBOUNCE_MS, INTERLOCK_DELAY_MS, LOCAL_MONITOR_INTERVAL_S
from state import PiStateManager, CommandedState, GpioOutputState, FeedbackState, VerificationState, SystemState
from logger import logger

try:
    from gpiozero import OutputDevice, Button
except ImportError:
    logger.warning("gpiozero not found. Using mock hardware.")
    class OutputDevice:
        def __init__(self, pin): self.pin = pin; self.is_active = False
        def on(self): self.is_active = True
        def off(self): self.is_active = False
    class Button:
        def __init__(self, pin, pull_up=False): self.pin = pin; self._state = False
        def is_pressed(self): return self._state

class GPIOManager:
    def __init__(self, state_manager: PiStateManager, device_config: dict):
        self.profile = get_hardware_profile(device_config.get("hardware_profile", "EMS-4CH-v1"))
        self.state_manager = state_manager
        self.device_config = device_config
        self.relays = {}
        self.feedback_inputs = {}
        self._lock = threading.Lock()
        
        for slot, relay_pin in self.profile["relay_gpio"].items():
            self.relays[slot] = OutputDevice(relay_pin)
        for slot, fb_pin in self.profile["feedback_gpio"].items():
            self.feedback_inputs[slot] = Button(fb_pin, pull_up=False)
            
        self._running = True
        self.monitor_thread = threading.Thread(target=self._local_monitor_loop, daemon=True)
        self.monitor_thread.start()

    def _read_feedback_raw(self, slot: str) -> bool:
        btn = self.feedback_inputs[slot]
        first_read = btn.is_pressed()
        time.sleep(FEEDBACK_DEBOUNCE_MS / 1000.0)
        second_read = btn.is_pressed()
        return first_read and second_read

    def verify_slot(self, slot: str, expected_commanded: CommandedState, feedback_configured: bool) -> VerificationState:
        if not feedback_configured:
            return VerificationState.NOT_CONFIGURED
            
        is_closed = self._read_feedback_raw(slot)
        
        if expected_commanded == CommandedState.ON:
            return VerificationState.VERIFIED_ON if is_closed else VerificationState.MISMATCH_ON_OFF
        elif expected_commanded == CommandedState.OFF:
            return VerificationState.VERIFIED_OFF if is_closed else VerificationState.MISMATCH_OFF_ON
        return VerificationState.PENDING

    def reconcile_hardware_state(self):
        self.state_manager.system_state = SystemState.HARDWARE_RECONCILIATION
        logger.info("Starting Hardware Reconciliation...")
        active_relays = []
        active_feedbacks = []
        
        fb_slots_config = self.device_config.get("slots", {})

        for slot in self.profile["slots"]:
            is_fb_enabled = fb_slots_config.get(slot, {}).get("feedback_enabled", False)
            
            if self.relays[slot].is_active:
                active_relays.append(slot)
                self.state_manager.set_gpio_output(slot, GpioOutputState.ON)
            else:
                self.state_manager.set_gpio_output(slot, GpioOutputState.OFF)
                
            if is_fb_enabled:
                if self._read_feedback_raw(slot):
                    active_feedbacks.append(slot)
                    self.state_manager.set_feedback(slot, FeedbackState.ON)
                else:
                    self.state_manager.set_feedback(slot, FeedbackState.OFF)
            else:
                self.state_manager.set_feedback(slot, FeedbackState.UNKNOWN)

        if len(active_relays) > 1 or len(active_feedbacks) > 1:
            logger.critical("CRITICAL FAULT: MULTIPLE CONTACTORS DETECTED ON!")
            self.state_manager.system_state = SystemState.FAULT
            return False
            
        if len(active_feedbacks) == 1:
            slot = active_feedbacks[0]
            self.state_manager.active_slot = slot
            self.state_manager.set_commanded(slot, CommandedState.ON, immediate=True)
            self.state_manager.set_verification(slot, VerificationState.VERIFIED_ON, immediate=True)
        elif len(active_relays) == 1:
            slot = active_relays[0]
            is_fb_enabled = fb_slots_config.get(slot, {}).get("feedback_enabled", False)
            
            if is_fb_enabled:
                # Relay is ON, but feedback is OFF. This is a MISMATCH, not GPIO_CONFIRMED.
                logger.critical(f"Slot {slot} Relay ON but Feedback OFF during boot! MISMATCH.")
                self.state_manager.set_verification(slot, VerificationState.MISMATCH_ON_OFF, immediate=True)
                self.state_manager.system_state = SystemState.FAULT
                return False
            else:
                self.state_manager.active_slot = slot
                self.state_manager.set_commanded(slot, CommandedState.ON, immediate=True)
                self.state_manager.set_verification(slot, VerificationState.GPIO_CONFIRMED, immediate=True)
        else:
            for slot in self.profile["slots"]:
                self.state_manager.set_commanded(slot, CommandedState.OFF)
                self.state_manager.set_verification(slot, VerificationState.VERIFIED_OFF if fb_slots_config.get(slot, {}).get("feedback_enabled") else VerificationState.NOT_CONFIGURED)
            self.state_manager.active_slot = None
            
        self.state_manager.system_state = SystemState.READY
        logger.info("Hardware Reconciliation Complete. System READY.")
        return True

    def transition_slot(self, target_slot: str, feedback_configured: bool) -> bool:
        with self._lock:
            if self.state_manager.system_state == SystemState.FAULT:
                return False
                
            current_active = self.state_manager.active_slot
            
            if current_active and current_active != target_slot:
                self.relays[current_active].off()
                self.state_manager.set_gpio_output(current_active, GpioOutputState.OFF)
                self.state_manager.set_commanded(current_active, CommandedState.OFF)
                
                start_time = time.time()
                while feedback_configured:
                    v_state = self.verify_slot(current_active, CommandedState.OFF, feedback_configured)
                    if v_state == VerificationState.VERIFIED_OFF: break
                    if v_state == VerificationState.MISMATCH_OFF_ON:
                        logger.critical(f"Slot {current_active} WELDED! Failed to open.")
                        self.state_manager.system_state = SystemState.FAULT
                        return False
                    if (time.time() - start_time) * 1000 > FEEDBACK_TIMEOUT_MS:
                        logger.error(f"Slot {current_active} break timeout.")
                        self.state_manager.system_state = SystemState.FAULT
                        return False
                    time.sleep(0.05)
                    
                time.sleep(INTERLOCK_DELAY_MS / 1000.0)

            self.relays[target_slot].on()
            self.state_manager.set_gpio_output(target_slot, GpioOutputState.ON)
            self.state_manager.set_commanded(target_slot, CommandedState.ON)
            
            start_time = time.time()
            while True:
                v_state = self.verify_slot(target_slot, CommandedState.ON, feedback_configured)
                if feedback_configured and v_state == VerificationState.VERIFIED_ON:
                    self.state_manager.active_slot = target_slot
                    self.state_manager.set_verification(target_slot, VerificationState.VERIFIED_ON, immediate=True)
                    return True
                elif not feedback_configured:
                    self.state_manager.active_slot = target_slot
                    self.state_manager.set_verification(target_slot, VerificationState.GPIO_CONFIRMED, immediate=True)
                    return True
                    
                if (time.time() - start_time) * 1000 > FEEDBACK_TIMEOUT_MS:
                    logger.error(f"Slot {target_slot} failed to close. Timeout.")
                    self.relays[target_slot].off()
                    self.state_manager.set_gpio_output(target_slot, GpioOutputState.OFF)
                    self.state_manager.set_commanded(target_slot, CommandedState.OFF)
                    self.state_manager.system_state = SystemState.FAULT
                    return False
                time.sleep(0.05)

    def _local_monitor_loop(self):
        while self._running:
            if self.state_manager.system_state == SystemState.READY:
                fb_slots_config = self.device_config.get("slots", {})
                for slot, state_obj in self.state_manager.slots.items():
                    is_fb_enabled = fb_slots_config.get(slot, {}).get("feedback_enabled", False)
                    if state_obj.commanded_state in [CommandedState.ON, CommandedState.OFF] and is_fb_enabled:
                        v_state = self.verify_slot(slot, state_obj.commanded_state, is_fb_enabled)
                        
                        if state_obj.commanded_state == CommandedState.ON and v_state == VerificationState.MISMATCH_ON_OFF:
                            logger.critical(f"Slot {slot} physically turned OFF unexpectedly!")
                            self.state_manager.set_verification(slot, v_state, immediate=True)
                            self.state_manager.system_state = SystemState.FAULT
                            
                        elif state_obj.commanded_state == CommandedState.OFF and v_state == VerificationState.MISMATCH_OFF_ON:
                            logger.critical(f"DANGER: Slot {slot} physically turned ON unexpectedly!")
                            self.state_manager.set_verification(slot, v_state, immediate=True)
                            self.state_manager.system_state = SystemState.FAULT
                            
            time.sleep(LOCAL_MONITOR_INTERVAL_S)