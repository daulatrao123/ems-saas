import json
import os
import threading
import time
from enum import Enum
from datetime import datetime
from config import STATE_FILE, BACKUP_STATE_FILE
from logger import logger

class CommandedState(str, Enum):
    ON = "ON"
    OFF = "OFF"
    UNKNOWN = "UNKNOWN"

class GpioOutputState(str, Enum):
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
    GPIO_CONFIRMED = "GPIO_CONFIRMED"      # Relay ON, but feedback unavailable
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
    CLOUD_OFFLINE = "CLOUD_OFFLINE"

class SlotState:
    def __init__(self, slot_code: str):
        self.slot_code = slot_code
        self.commanded_state = CommandedState.UNKNOWN
        self.gpio_output_state = GpioOutputState.UNKNOWN
        self.feedback_state = FeedbackState.UNKNOWN
        self.verification_state = VerificationState.NOT_CONFIGURED
        self.last_command_at = None

    def to_dict(self):
        return {
            "slot": self.slot_code,
            "commanded_state": self.commanded_state.value,
            "gpio_output_state": self.gpio_output_state.value,
            "feedback_state": self.feedback_state.value,
            "verification_state": self.verification_state.value,
            "last_command_at": self.last_command_at.isoformat() if self.last_command_at else None,
        }

    def from_dict(self, data: dict):
        try:
            self.commanded_state = CommandedState(data.get("commanded_state", "UNKNOWN"))
            self.gpio_output_state = GpioOutputState(data.get("gpio_output_state", "UNKNOWN"))
            self.feedback_state = FeedbackState(data.get("feedback_state", "UNKNOWN"))
            self.verification_state = VerificationState(data.get("verification_state", "NOT_CONFIGURED"))
            lca = data.get("last_command_at")
            self.last_command_at = datetime.fromisoformat(lca) if lca else None
        except Exception as e:
            logger.critical(f"Slot {self.slot_code} state corrupted: {e}. Reverting to UNKNOWN.")
            self.commanded_state = CommandedState.UNKNOWN
            self.gpio_output_state = GpioOutputState.UNKNOWN
            self.feedback_state = FeedbackState.UNKNOWN
            self.verification_state = VerificationState.NOT_CONFIGURED

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
        data = None
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
            except Exception as e:
                logger.error(f"Primary state load failed: {e}. Trying backup.")
                
        if data is None and os.path.exists(BACKUP_STATE_FILE):
            try:
                with open(BACKUP_STATE_FILE, 'r') as f:
                    data = json.load(f)
                logger.warning("Loaded from backup_state.json. Primary was missing/corrupt.")
            except Exception as e:
                logger.critical(f"Backup state load failed: {e}. Starting fresh.")
                
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
        with self._save_lock:
            data = {
                "system_state": self.system_state.value,
                "active_slot": self.active_slot,
                "slots": {code: s.to_dict() for code, s in self.slots.items()}
            }
            try:
                tmp_file = STATE_FILE + ".tmp"
                with open(tmp_file, 'w') as f:
                    json.dump(data, f, indent=4)
                    f.flush()
                    os.fsync(f.fileno())
                
                # 1. Backup current to backup_state (if current exists)
                if os.path.exists(STATE_FILE):
                    # os.replace is atomic. If backup exists, it overwrites it.
                    os.replace(STATE_FILE, BACKUP_STATE_FILE)
                
                # 2. Move tmp to current_state
                os.replace(tmp_file, STATE_FILE)
                self._dirty = False
            except Exception as e:
                logger.critical(f"CRITICAL: State save failed: {e}")

    def save_state(self, immediate=False):
        self._dirty = True
        if immediate:
            self._flush_to_disk()

    def set_commanded(self, slot_code: str, state: CommandedState, immediate=False):
        if slot_code in self.slots:
            self.slots[slot_code].commanded_state = state
            self.slots[slot_code].last_command_at = datetime.utcnow()
            self.save_state(immediate=immediate)

    def set_gpio_output(self, slot_code: str, state: GpioOutputState, immediate=False):
        if slot_code in self.slots:
            self.slots[slot_code].gpio_output_state = state
            self.save_state(immediate=immediate)

    def set_feedback(self, slot_code: str, state: FeedbackState, immediate=False):
        if slot_code in self.slots and self.slots[slot_code].feedback_state != state:
            self.slots[slot_code].feedback_state = state
            self.save_state(immediate=immediate)

    def set_verification(self, slot_code: str, state: VerificationState, immediate=False):
        if slot_code in self.slots:
            self.slots[slot_code].verification_state = state
            self.save_state(immediate=immediate)