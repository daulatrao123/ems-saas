import time
import threading
from config import get_hardware_profile, FEEDBACK_TIMEOUT_MS, FEEDBACK_DEBOUNCE_MS, INTERLOCK_DELAY_MS, LOCAL_MONITOR_INTERVAL_S
from state import PiStateManager, CommandedState, FeedbackState, VerificationState, SystemState

# Mock gpiozero for structural integrity. On Pi, use: from gpiozero import OutputDevice, Button
class OutputDevice:
    def __init__(self, pin): self.pin = pin; self.is_active = False
    def on(self): self.is_active = True
    def off(self): self.is_active = False
    def is_active(self): return self.is_active

class Button:
    def __init__(self, pin, pull_up=False): self.pin = pin; self._state = False
    def is_pressed(self): return self._state
    def set_state(self, val): self._state = val

class GPIOManager:
    def __init__(self, state_manager: PiStateManager, profile_name="EMS-4CH-v1"):
        self.profile = get_hardware_profile(profile_name)
        self.state_manager = state_manager
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
        if not self.profile["feedback_capable"]:
            return VerificationState.NOT_AVAILABLE
        if not feedback_configured:
            return VerificationState.NOT_CONFIGURED
            
        is_closed = self._read_feedback_raw(slot)
        
        if expected_commanded == CommandedState.ON:
            return VerificationState.VERIFIED_ON if is_closed else VerificationState.MISMATCH_ON_OFF
        elif expected_commanded == CommandedState.OFF:
            return VerificationState.VERIFIED_OFF if not is_closed else VerificationState.MISMATCH_OFF_ON
        return VerificationState.PENDING

    def reconcile_hardware_state(self, feedback_configured_slots: dict):
        """
        CRITICAL BOOT RECONCILIATION. Hardware wins. 
        Inspects all 4 channels to prevent multiple-active faults.
        """
        self.state_manager.system_state = SystemState.HARDWARE_RECONCILIATION
        active_relays = []
        active_feedbacks = []
        
        for slot in self.profile["slots"]:
            # 1. Read physical relay GPIO
            if self.relays[slot].is_active:
                active_relays.append(slot)
                
            # 2. Read physical feedback
            if feedback_configured_slots.get(slot, False):
                if self._read_feedback_raw(slot):
                    active_feedbacks.append(slot)
                self.state_manager.set_feedback(slot, FeedbackState.ON if active_feedbacks[-1] == slot else FeedbackState.OFF)
            else:
                self.state_manager.set_feedback(slot, FeedbackState.UNKNOWN)

        # 3. Interlock Fault Detection
        if len(active_relays) > 1 or len(active_feedbacks) > 1:
            print("CRITICAL FAULT: MULTIPLE CONTACTORS DETECTED ON!")
            self.state_manager.system_state = SystemState.FAULT
            # In a real system, trigger alarm, do not proceed to READY
            return False
            
        # 4. Reconcile Active Slot
        if len(active_feedbacks) == 1:
            self.state_manager.active_slot = active_feedbacks[0]
            self.state_manager.set_commanded(active_feedbacks[0], CommandedState.ON, immediate=True)
            self.state_manager.set_verification(active_feedbacks[0], VerificationState.VERIFIED_ON, immediate=True)
        elif len(active_relays) == 1:
            self.state_manager.active_slot = active_relays[0]
            self.state_manager.set_commanded(active_relays[0], CommandedState.ON, immediate=True)
            self.state_manager.set_verification(active_relays[0], VerificationState.NOT_CONFIGURED if not feedback_configured_slots.get(active_relays[0]) else VerificationState.MISMATCH_ON_OFF, immediate=True)
        else:
            # Everything is OFF
            for slot in self.profile["slots"]:
                self.state_manager.set_commanded(slot, CommandedState.OFF)
                self.state_manager.set_verification(slot, VerificationState.VERIFIED_OFF if feedback_configured_slots.get(slot) else VerificationState.NOT_CONFIGURED)
            self.state_manager.active_slot = None
            
        self.state_manager.system_state = SystemState.READY
        return True

    def transition_slot(self, target_slot: str, feedback_configured: bool) -> bool:
        with self._lock:
            if self.state_manager.system_state == SystemState.FAULT:
                return False
                
            current_active = self.state_manager.active_slot
            
            # --- BREAK PHASE ---
            if current_active and current_active != target_slot:
                self.relays[current_active].off()
                self.state_manager.set_commanded(current_active, CommandedState.OFF)
                
                start_time = time.time()
                while True:
                    v_state = self.verify_slot(current_active, CommandedState.OFF, feedback_configured)
                    if v_state == VerificationState.VERIFIED_OFF:
                        break
                    if v_state == VerificationState.MISMATCH_OFF_ON:
                        print(f"[FAULT] Slot {current_active} WELDED! Failed to open.")
                        self.state_manager.system_state = SystemState.FAULT
                        return False
                        
                    if (time.time() - start_time) * 1000 > FEEDBACK_TIMEOUT_MS:
                        if feedback_configured:
                            print(f"[FAULT] Slot {current_active} break timeout.")
                            self.state_manager.system_state = SystemState.FAULT
                            return False
                        break 
                        
                time.sleep(INTERLOCK_DELAY_MS / 1000.0)

            # --- MAKE PHASE ---
            self.relays[target_slot].on()
            self.state_manager.set_commanded(target_slot, CommandedState.ON)
            
            start_time = time.time()
            while True:
                v_state = self.verify_slot(target_slot, CommandedState.ON, feedback_configured)
                if v_state == VerificationState.VERIFIED_ON:
                    self.state_manager.active_slot = target_slot
                    self.state_manager.save_state(immediate=True)
                    return True
                    
                if (time.time() - start_time) * 1000 > FEEDBACK_TIMEOUT_MS:
                    if feedback_configured:
                        print(f"[FAULT] Slot {target_slot} failed to close. Timeout.")
                        self.relays[target_slot].off()
                        self.state_manager.set_commanded(target_slot, CommandedState.OFF)
                        self.state_manager.system_state = SystemState.FAULT
                        return False
                    self.state_manager.active_slot = target_slot
                    self.state_manager.save_state(immediate=True)
                    return True
                    
                time.sleep(0.05)

    def _local_monitor_loop(self):
        """Fast 2-sec local monitoring. Does not depend on cloud."""
        while self._running:
            if self.state_manager.system_state == SystemState.READY:
                # In production, this dict comes from cloud config sync
                feedback_configured_slots = {"A": True, "B": True, "C": False, "D": False}
                
                for slot, state_obj in self.state_manager.slots.items():
                    if state_obj.commanded_state in [CommandedState.ON, CommandedState.OFF]:
                        v_state = self.verify_slot(slot, state_obj.commanded_state, feedback_configured_slots.get(slot, False))
                        
                        if state_obj.commanded_state == CommandedState.ON and v_state == VerificationState.MISMATCH_ON_OFF:
                            print(f"[ALERT] Slot {slot} physically turned OFF unexpectedly!")
                            self.state_manager.set_verification(slot, v_state, immediate=True)
                            
                        elif state_obj.commanded_state == CommandedState.OFF and v_state == VerificationState.MISMATCH_OFF_ON:
                            print(f"[ALERT] DANGER: Slot {slot} physically turned ON unexpectedly!")
                            self.state_manager.set_verification(slot, v_state, immediate=True)
                            self.state_manager.system_state = SystemState.FAULT
                            
            time.sleep(LOCAL_MONITOR_INTERVAL_S)