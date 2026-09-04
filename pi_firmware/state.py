import json
import os
from enum import Enum
from datetime import datetime
from config import STATE_FILE

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

class PiStateManager:
    def __init__(self):
        self.active_slot = None
        self.slots = {
            "A": SlotState("A"),
            "B": SlotState("B"),
            "C": SlotState("C"),
            "D": SlotState("D")
        }
        self._load_state()

    def _load_state(self):
        if not os.path.exists(STATE_FILE):
            return
            
        try:
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
                self.active_slot = data.get("active_slot")
                for slot_code, slot_data in data.get("slots", {}).items():
                    if slot_code in self.slots:
                        s = self.slots[slot_code]
                        s.commanded_state = CommandedState(slot_data.get("commanded_state", "UNKNOWN"))
                        s.feedback_state = FeedbackState(slot_data.get("feedback_state", "UNKNOWN"))
                        s.verification_state = VerificationState(slot_data.get("verification_state", "NOT_CONFIGURED"))
        except Exception as e:
            print(f"Error loading state: {e}. Starting fresh.")

    def save_state(self):
        data = {
            "active_slot": self.active_slot,
            "slots": {code: s.to_dict() for code, s in self.slots.items()}
        }
        with open(STATE_FILE, 'w') as f:
            json.dump(data, f, indent=4)

    def set_commanded(self, slot_code: str, state: CommandedState):
        if slot_code in self.slots:
            self.slots[slot_code].commanded_state = state
            self.slots[slot_code].last_command_at = datetime.utcnow()
            self.save_state()