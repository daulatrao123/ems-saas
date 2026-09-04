import json
import os
import threading
import time
from enum import Enum
from datetime import datetime

# Import configuration constants
try:
    from config import STATE_FILE
except ImportError:
    # Fallback for isolated testing if config.py isn't present
    STATE_FILE = os.path.join(os.path.dirname(__file__), 'pi_state.json')

# --- Enums ---

class CommandedState(str, Enum):
    ON = "ON"
    OFF = "OFF"
    UNKNOWN = "UNKNOWN"

class FeedbackState(str, Enum):
    ON = "ON"
    OFF = "OFF"
    UNKNOWN = "UNKNOWN"      # Hardware not installed, not configured, or fault
    PENDING = "PENDING"      # Command sent, waiting for physical closure

class VerificationState(str, Enum):
    VERIFIED_ON = "VERIFIED_ON"
    VERIFIED_OFF = "VERIFIED_OFF"
    MISMATCH_ON_OFF = "MISMATCH_ON_OFF"   # Commanded ON, but feedback OFF (Failed to close)
    MISMATCH_OFF_ON = "MISMATCH_OFF_ON"   # Commanded OFF, but feedback ON (WELDED/DANGEROUS)
    TIMEOUT = "TIMEOUT"                    # Feedback never arrived
    NOT_CONFIGURED = "NOT_CONFIGURED"      # Super admin did not enable feedback
    NOT_AVAILABLE = "NOT_AVAILABLE"        # Pi hardware doesn't have feedback inputs
    PENDING = "PENDING"                    # Awaiting verification


# --- Data Classes ---

class SlotState:
    def __init__(self, slot_code: str):
        self.slot_code = slot_code
        self.commanded_state = CommandedState.UNKNOWN
        self.feedback_state = FeedbackState.UNKNOWN
        self.verification_state = VerificationState.NOT_CONFIGURED
        self.feedback_last_changed_at = None
        self.last_command_at = None

    def to_dict(self):
        return {
            "slot": self.slot_code,
            "commanded_state": self.commanded_state.value,
            "feedback_state": self.feedback_state.value,
            "verification_state": self.verification_state.value,
            "feedback_last_changed_at": self.feedback_last_changed_at.isoformat() if self.feedback_last_changed_at else None,
            "last_command_at": self.last_command_at.isoformat() if self.last_command_at else None,
        }

    def from_dict(self, data: dict):
        try:
            self.commanded_state = CommandedState(data.get("commanded_state", "UNKNOWN"))
            self.feedback_state = FeedbackState(data.get("feedback_state", "UNKNOWN"))
            self.verification_state = VerificationState(data.get("verification_state", "NOT_CONFIGURED"))
            
            fcd = data.get("feedback_last_changed_at")
            self.feedback_last_changed_at = datetime.fromisoformat(fcd) if fcd else None
            
            lca = data.get("last_command_at")
            self.last_command_at = datetime.fromisoformat(lca) if lca else None
        except Exception as e:
            print(f"Error parsing slot state for {self.slot_code}: {e}. Resetting to defaults.")


# --- State Manager ---

class PiStateManager:
    def __init__(self):
        self.active_slot = None
        self.slots = {
            "A": SlotState("A"),
            "B": SlotState("B"),
            "C": SlotState("C"),
            "D": SlotState("D")
        }
        
        # Async disk write properties to protect flash endurance
        self._dirty = False
        self._save_lock = threading.Lock()
        self._save_thread = threading.Thread(target=self._async_save_loop, daemon=True)
        self._save_thread.start()
        
        # Load last known state from disk
        self._load_state()

    def _load_state(self):
        """Loads state from JSON file. Does not crash if file is missing/corrupt."""
        if not os.path.exists(STATE_FILE):
            return
            
        try:
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
                self.active_slot = data.get("active_slot")
                
                for slot_code, slot_data in data.get("slots", {}).items():
                    if slot_code in self.slots:
                        self.slots[slot_code].from_dict(slot_data)
        except Exception as e:
            print(f"Error loading state file: {e}. Starting fresh.")

    def _async_save_loop(self):
        """Background thread that flushes state to disk every 60 seconds if dirty."""
        while True:
            time.sleep(60.0)
            if self._dirty:
                self._flush_to_disk()

    def _flush_to_disk(self):
        """The actual file write operation. Thread-safe."""
        with self._save_lock:
            data = {
                "active_slot": self.active_slot,
                "slots": {code: s.to_dict() for code, s in self.slots.items()}
            }
            try:
                # Write to temp file first, then replace, to prevent corruption on power loss
                tmp_file = STATE_FILE + ".tmp"
                with open(tmp_file, 'w') as f:
                    json.dump(data, f, indent=4)
                os.replace(tmp_file, STATE_FILE)
                
                self._dirty = False
            except Exception as e:
                print(f"CRITICAL: Failed to save state to disk: {e}")

    def save_state(self, immediate=False):
        """
        Marks state as dirty. 
        If immediate=True (e.g., active slot changed), flushes immediately.
        Otherwise, writes are batched to avoid killing the USB drive.
        """
        self._dirty = True
        if immediate:
            self._flush_to_disk()

    def set_commanded(self, slot_code: str, state: CommandedState, immediate=False):
        """Updates the commanded state and timestamps."""
        if slot_code in self.slots:
            self.slots[slot_code].commanded_state = state
            self.slots[slot_code].last_command_at = datetime.utcnow()
            self.save_state(immediate=immediate)

    def set_feedback(self, slot_code: str, state: FeedbackState, immediate=False):
        """Updates the physical feedback state and timestamps."""
        if slot_code in self.slots:
            # Only update timestamp if state actually changed
            if self.slots[slot_code].feedback_state != state:
                self.slots[slot_code].feedback_state = state
                self.slots[slot_code].feedback_last_changed_at = datetime.utcnow()
                self.save_state(immediate=immediate)

    def set_verification(self, slot_code: str, state: VerificationState, immediate=False):
        """Updates the verification state."""
        if slot_code in self.slots:
            self.slots[slot_code].verification_state = state
            self.save_state(immediate=immediate)

    def get_state_snapshot(self) -> dict:
        """Returns a dictionary representation of all states for backend syncing."""
        return {
            "active_slot": self.active_slot,
            "slots": {code: s.to_dict() for code, s in self.slots.items()}
        }