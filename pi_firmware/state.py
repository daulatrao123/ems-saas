import json
import os
import threading
import time
from enum import Enum
from datetime import datetime
from config import STATE_FILE, BACKUP_STATE_FILE

class CommandedState(str, Enum):
    ON = "ON"
    OFF = "OFF"
    UNKNOWN = "UNKNOWN"

class FeedbackState(str, Enum):
    ON = "ON"
    OFF = "OFF"
    UNKNOWN = "UNKNOWN"
    PENDING = "PENDING"

class VerificationState(str, Enum):
    VERIFIED_ON = "VERIFIED_ON"
    VERIFIED_OFF = "VERIFIED_OFF"
    MISMATCH_ON_OFF = "MISMATCH_ON_OFF"
    MISMATCH_OFF_ON = "MISMATCH_OFF_ON"
    TIMEOUT = "TIMEOUT"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    PENDING = "PENDING"

class SystemState(str, Enum):
    BOOT = "BOOT"
    SELF_TEST = "SELF_TEST"
    HARDWARE_RECONCILIATION = "HARDWARE_RECONCILIATION"
    READY = "READY"
    EXECUTING = "EXECUTING"
    FAULT = "FAULT"
    CLOUD_OFFLINE = "CLOUD_OFFLINE" # Not a system fault, just a state

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
        except Exception:
            pass # Reset to defaults on corrupt data

class PiStateManager:
    def __init__(self):
        self.system_state = SystemState.BOOT
        self.active_slot = None
        self.slots = {code: SlotState(code) for code in ["A", "B", "C", "D"]}
        
        self._dirty = False
        self._save_lock = threading.Lock()
        self._save_thread = threading.Thread(target=self._async_save_loop, daemon=True)
        self._save_thread.start()
        
        self._load_state()

    def _load_state(self):
        """Loads state with backup fallback. Hardware truth will override this later."""
        data = None
        for filepath in [STATE_FILE, BACKUP_STATE_FILE]:
            if os.path.exists(filepath):
                try:
                    with open(filepath, 'r') as f:
                        data = json.load(f)
                    break
                except Exception:
                    continue # Try backup
                    
        if data:
            self.active_slot = data.get("active_slot")
            for slot_code, slot_data in data.get("slots", {}).items():
                if slot_code in self.slots:
                    self.slots[slot_code].from_dict(slot_data)

    def _async_save_loop(self):
        while True:
            time.sleep(60.0)
            if self._dirty:
                self._flush_to_disk()

    def _flush_to_disk(self):
        """Atomic write with fsync to prevent corruption."""
        with self._save_lock:
            data = {
                "system_state": self.system_state.value,
                "active_slot": self.active_slot,
                "slots": {code: s.to_dict() for code, s in self.slots.items()}
            }
            try:
                tmp_file = STATE_FILE + ".tmp"
                # Write to temp
                with open(tmp_file, 'w') as f:
                    json.dump(data, f, indent=4)
                    f.flush()
                    os.fsync(f.fileno())
                
                # Atomic replace primary
                os.replace(tmp_file, STATE_FILE)
                
                # Copy to backup
                os.replace(STATE_FILE, BACKUP_STATE_FILE)
                self._dirty = False
            except Exception as e:
                print(f"CRITICAL: State save failed: {e}")

    def save_state(self, immediate=False):
        self._dirty = True
        if immediate:
            self._flush_to_disk()

    def set_commanded(self, slot_code: str, state: CommandedState, immediate=False):
        if slot_code in self.slots:
            self.slots[slot_code].commanded_state = state
            self.slots[slot_code].last_command_at = datetime.utcnow()
            self.save_state(immediate=immediate)

    def set_feedback(self, slot_code: str, state: FeedbackState, immediate=False):
        if slot_code in self.slots and self.slots[slot_code].feedback_state != state:
            self.slots[slot_code].feedback_state = state
            self.slots[slot_code].feedback_last_changed_at = datetime.utcnow()
            self.save_state(immediate=immediate)

    def set_verification(self, slot_code: str, state: VerificationState, immediate=False):
        if slot_code in self.slots:
            self.slots[slot_code].verification_state = state
            self.save_state(immediate=immediate)