import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from enum import Enum

from config import (
    STATE_FILE,
    BACKUP_STATE_FILE,
    RECOVERY_STATE_FILE,
    STATE_VERSION,
    SUPPORTED_SLOTS,
)

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
    GPIO_CONFIRMED = "GPIO_CONFIRMED"
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
    def __init__(self, slot_code):
        self.slot_code = slot_code

        self.commanded_state = (
            CommandedState.UNKNOWN
        )

        self.gpio_output_state = (
            GpioOutputState.UNKNOWN
        )

        self.feedback_state = (
            FeedbackState.UNKNOWN
        )

        self.verification_state = (
            VerificationState.NOT_CONFIGURED
        )

        self.last_command_at = None

    def to_dict(self):
        return {
            "slot": self.slot_code,
            "commanded_state":
                self.commanded_state.value,
            "gpio_output_state":
                self.gpio_output_state.value,
            "feedback_state":
                self.feedback_state.value,
            "verification_state":
                self.verification_state.value,
            "last_command_at":
                (
                    self.last_command_at.isoformat()
                    if self.last_command_at
                    else None
                ),
        }

    def from_dict(self, data):
        try:
            self.commanded_state = CommandedState(
                data.get(
                    "commanded_state",
                    "UNKNOWN",
                )
            )

            self.gpio_output_state = GpioOutputState(
                data.get(
                    "gpio_output_state",
                    "UNKNOWN",
                )
            )

            self.feedback_state = FeedbackState(
                data.get(
                    "feedback_state",
                    "UNKNOWN",
                )
            )

            self.verification_state = VerificationState(
                data.get(
                    "verification_state",
                    "NOT_CONFIGURED",
                )
            )

            timestamp = data.get(
                "last_command_at"
            )

            self.last_command_at = (
                datetime.fromisoformat(timestamp)
                if timestamp
                else None
            )

            return True

        except Exception as exc:
            logger.critical(
                "Slot %s state invalid: %s",
                self.slot_code,
                exc,
            )

            self.commanded_state = (
                CommandedState.UNKNOWN
            )

            self.gpio_output_state = (
                GpioOutputState.UNKNOWN
            )

            self.feedback_state = (
                FeedbackState.UNKNOWN
            )

            self.verification_state = (
                VerificationState.NOT_CONFIGURED
            )

            return False


class PiStateManager:
    def __init__(self, storage_manager):
        self.storage = storage_manager

        self.system_state = SystemState.BOOT
        self.active_slot = None

        self.slots = {
            code: SlotState(code)
            for code in SUPPORTED_SLOTS
        }

        self._save_lock = threading.Lock()

        self.generation = 0
        self.dirty = False

        self.state_loaded = self._load_state()

    # ------------------------------------------------------------
    # SERIALIZATION
    # ------------------------------------------------------------

    def _build_document(self):
        self.generation += 1

        body = {
            "version": STATE_VERSION,
            "generation": self.generation,
            "timestamp": datetime.now(
                timezone.utc
            ).isoformat(),

            "system_state":
                self.system_state.value,

            "active_slot":
                self.active_slot,

            "slots": {
                code: state.to_dict()
                for code, state
                in self.slots.items()
            },
        }

        canonical = json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
        )

        body["sha256"] = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()

        return body

    @staticmethod
    def _verify_document(data):
        if not isinstance(data, dict):
            return False

        if data.get("version") != STATE_VERSION:
            return False

        expected = data.get("sha256")

        if not expected:
            return False

        body = dict(data)
        body.pop("sha256", None)

        canonical = json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
        )

        actual = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()

        return actual == expected

    # ------------------------------------------------------------
    # LOAD
    # ------------------------------------------------------------

    def _load_state(self):
        candidates = [
            STATE_FILE,
            BACKUP_STATE_FILE,
            RECOVERY_STATE_FILE,
        ]

        for path in candidates:
            if not os.path.exists(path):
                continue

            try:
                with open(
                    path,
                    "r",
                    encoding="utf-8",
                ) as fh:
                    data = json.load(fh)

                if not self._verify_document(
                    data
                ):
                    continue

                self.active_slot = data.get(
                    "active_slot"
                )

                system = data.get(
                    "system_state",
                    "BOOT",
                )

                try:
                    self.system_state = (
                        SystemState(system)
                    )
                except ValueError:
                    self.system_state = (
                        SystemState.BOOT
                    )

                valid = True

                for slot_code in SUPPORTED_SLOTS:
                    slot_data = (
                        data.get(
                            "slots",
                            {},
                        ).get(
                            slot_code
                        )
                    )

                    if slot_data is None:
                        valid = False
                        continue

                    if not self.slots[
                        slot_code
                    ].from_dict(
                        slot_data
                    ):
                        valid = False

                self.generation = int(
                    data.get(
                        "generation",
                        0,
                    )
                )

                if path != STATE_FILE:
                    logger.critical(
                        "State recovered from %s",
                        path,
                    )

                return valid

            except Exception as exc:
                logger.error(
                    "Unable to load state %s: %s",
                    path,
                    exc,
                )

        return False

    # ------------------------------------------------------------
    # DURABLE SAVE
    # ------------------------------------------------------------

    def _flush_to_disk(self):
        if not self.storage.is_write_allowed(
            "state"
        ):
            logger.critical(
                "State persistence blocked by "
                "resource protection."
            )
            return False

        with self._save_lock:
            data = self._build_document()

            temp_path = STATE_FILE + ".tmp"

            try:
                with open(
                    temp_path,
                    "w",
                    encoding="utf-8",
                ) as fh:
                    json.dump(
                        data,
                        fh,
                        separators=(",", ":"),
                    )

                    fh.flush()
                    os.fsync(
                        fh.fileno()
                    )

                # Current -> backup.
                if os.path.exists(
                    STATE_FILE
                ):
                    os.replace(
                        STATE_FILE,
                        BACKUP_STATE_FILE,
                    )

                # New current.
                os.replace(
                    temp_path,
                    STATE_FILE,
                )

                directory = os.path.dirname(
                    STATE_FILE
                )

                dir_fd = os.open(
                    directory,
                    os.O_DIRECTORY,
                )

                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)

                self.storage.io_meter.record_ems_write(
                    "state",
                    len(
                        json.dumps(
                            data,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ),
                )

                self.dirty = False

                return True

            except Exception as exc:
                logger.critical(
                    "CRITICAL STATE SAVE FAILURE: %s",
                    exc,
                )

                return False

    # ------------------------------------------------------------
    # PUBLIC MUTATIONS
    # ------------------------------------------------------------

    def save_state(self, immediate=False):
        self.dirty = True

        if immediate:
            return self._flush_to_disk()

        return True

    def set_commanded(
        self,
        slot_code,
        state,
        immediate=False,
    ):
        if slot_code not in self.slots:
            return

        self.slots[
            slot_code
        ].commanded_state = state

        self.slots[
            slot_code
        ].last_command_at = datetime.now(
            timezone.utc
        )

        self.save_state(
            immediate=immediate
        )

    def set_gpio_output(
        self,
        slot_code,
        state,
        immediate=False,
    ):
        if slot_code not in self.slots:
            return

        current = self.slots[
            slot_code
        ].gpio_output_state

        if current == state:
            return

        self.slots[
            slot_code
        ].gpio_output_state = state

        self.save_state(
            immediate=immediate
        )

    def set_feedback(
        self,
        slot_code,
        state,
        immediate=False,
    ):
        if slot_code not in self.slots:
            return

        if (
            self.slots[
                slot_code
            ].feedback_state
            == state
        ):
            return

        self.slots[
            slot_code
        ].feedback_state = state

        self.save_state(
            immediate=immediate
        )

    def set_verification(
        self,
        slot_code,
        state,
        immediate=False,
    ):
        if slot_code not in self.slots:
            return

        if (
            self.slots[
                slot_code
            ].verification_state
            == state
        ):
            return

        self.slots[
            slot_code
        ].verification_state = state

        self.save_state(
            immediate=immediate
        )