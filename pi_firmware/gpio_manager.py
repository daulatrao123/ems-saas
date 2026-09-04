import time
import threading
from enum import Enum
from config import get_hardware_profile, FEEDBACK_TIMEOUT_MS, FEEDBACK_DEBOUNCE_MS, INTERLOCK_DELAY_MS, LOCAL_MONITOR_INTERVAL_S
from state import PiStateManager, CommandedState, FeedbackState, VerificationState

# Mocking gpiozero for the sake of code structure. 
# In actual Pi, `from gpiozero import OutputDevice, Button`
class OutputDevice:
    def __init__(self, pin): self.pin = pin; self.is_active = False
    def on(self): self.is_active = True
    def off(self): self.is_active = False

class Button:
    def __init__(self, pin, pull_up=True): self.pin = pin; self.is_active = False
    def is_pressed(self): return self.is_active

class GPIOManager:
    def __init__(self, state_manager: PiStateManager, profile_name="EMS-4CH-v1"):
        self.profile = get_hardware_profile(profile_name)
        self.state_manager = state_manager
        self.relays = {}
        self.feedback_inputs = {}
        self._lock = threading.Lock()
        
        # Initialize GPIO
        for slot, relay_pin in self.profile["relay_gpio"].items():
            self.relays[slot] = OutputDevice(relay_pin)
            
        for slot, fb_pin in self.profile["feedback_gpio"].items():
            self.feedback_inputs[slot] = Button(fb_pin, pull_up=False)
            
        # Start background fault monitor
        self._running = True
        self.monitor_thread = threading.Thread(target=self._local_monitor_loop, daemon=True)
        self.monitor_thread.start()

    def _read_feedback_raw(self, slot: str) -> bool:
        """Reads the physical GPIO, handles debouncing."""
        btn = self.feedback_inputs[slot]
        first_read = btn.is_pressed()
        
        # Debounce check
        time.sleep(FEEDBACK_DEBOUNCE_MS / 1000.0)
        second_read = btn.is_pressed()
        
        return first_read and second_read

    def verify_slot(self, slot: str, expected_commanded: CommandedState, feedback_configured: bool) -> VerificationState:
        """Evaluates the current physical state against the commanded state."""
        if not self.profile["feedback_capable"]:
            return VerificationState.NOT_AVAILABLE
            
        if not feedback_configured:
            return VerificationState.NOT_CONFIGURED
            
        # Read physical feedback
        is_closed = self._read_feedback_raw(slot)
        
        if expected_commanded == CommandedState.ON:
            if is_closed:
                return VerificationState.VERIFIED_ON
            else:
                return VerificationState.MISMATCH_ON_OFF
                
        elif expected_commanded == CommandedState.OFF:
            if is_closed:
                return VerificationState.MISMATCH_OFF_ON # DANGEROUS STATE
            else:
                return VerificationState.VERIFIED_OFF
                
        return VerificationState.PENDING

    def transition_slot(self, target_slot: str, feedback_configured: bool) -> bool:
        """
        Executes safe Break-Before-Make transition.
        Returns True if successfully verified, False if failed.
        """
        with self._lock:
            current_active = self.state_manager.active_slot
            
            # --- BREAK PHASE ---
            if current_active and current_active != target_slot:
                print(f"[GPIO] Breaking current slot {current_active}")
                self.relays[current_active].off()
                self.state_manager.set_commanded(current_active, CommandedState.OFF)
                
                # Wait for break verification
                start_time = time.time()
                while True:
                    v_state = self.verify_slot(current_active, CommandedState.OFF, feedback_configured)
                    if v_state == VerificationState.VERIFIED_OFF:
                        break
                    if v_state == VerificationState.MISMATCH_OFF_ON:
                        print(f"[FAULT] Slot {current_active} FAILED TO OPEN! Welded contact?")
                        return False # HALT operation immediately
                        
                    if (time.time() - start_time) * 1000 > FEEDBACK_TIMEOUT_MS:
                        if feedback_configured:
                            print(f"[FAULT] Slot {current_active} break timeout.")
                            return False
                        break # If no feedback configured, just proceed after interlock
                        
                # Mandatory Interlock Delay
                time.sleep(INTERLOCK_DELAY_MS / 1000.0)

            # --- MAKE PHASE ---
            print(f"[GPIO] Making target slot {target_slot}")
            self.relays[target_slot].on()
            self.state_manager.set_commanded(target_slot, CommandedState.ON)
            
            # Wait for make verification
            start_time = time.time()
            while True:
                v_state = self.verify_slot(target_slot, CommandedState.ON, feedback_configured)
                if v_state == VerificationState.VERIFIED_ON:
                    self.state_manager.active_slot = target_slot
                    self.state_manager.save_state()
                    return True
                    
                if (time.time() - start_time) * 1000 > FEEDBACK_TIMEOUT_MS:
                    if feedback_configured:
                        print(f"[FAULT] Slot {target_slot} failed to close. Timeout.")
                        self.relays[target_slot].off() # Safety reset
                        self.state_manager.set_commanded(target_slot, CommandedState.OFF)
                        return False
                    # If no feedback configured, assume success after relay command
                    self.state_manager.active_slot = target_slot
                    self.state_manager.save_state()
                    return True
                    
                time.sleep(0.05) # Poll every 50ms

    def _local_monitor_loop(self):
        """
        Fast local monitoring (Point C1). Does not wait for backend sync.
        Detects physical changes independent of web commands.
        """
        while self._running:
            # In a real implementation, you would fetch `feedback_configured` from backend config sync
            # For now, assume True for demonstration
            feedback_configured = True 
            
            for slot, state_obj in self.state_manager.slots.items():
                if state_obj.commanded_state in [CommandedState.ON, CommandedState.OFF]:
                    v_state = self.verify_slot(slot, state_obj.commanded_state, feedback_configured)
                    
                    # Update state manager if physical reality changed unexpectedly
                    if state_obj.commanded_state == CommandedState.ON and v_state == VerificationState.MISMATCH_ON_OFF:
                        print(f"[ALERT] Slot {slot} physically turned OFF unexpectedly!")
                        state_obj.verification_state = v_state
                        # TODO: Trigger immediate backend alert here
                        
                    elif state_obj.commanded_state == CommandedState.OFF and v_state == VerificationState.MISMATCH_OFF_ON:
                        print(f"[ALERT] DANGER: Slot {slot} physically turned ON unexpectedly!")
                        state_obj.verification_state = v_state
                        # TODO: Trigger immediate backend alert here
                        
            time.sleep(LOCAL_MONITOR_INTERVAL_S)