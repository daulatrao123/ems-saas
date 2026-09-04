import signal
import threading
import time
from datetime import datetime, timezone, timedelta

from config import (
    DEVICE_ID,
    SUPPORTED_SLOTS,
    SYNC_INTERVAL_S,
)

from logger import logger
from state import (
    PiStateManager,
    SystemState,
    CommandedState,
    VerificationState,
)
from storage_manager import StorageManager
from offline_queue import OfflineQueue
from gpio_manager import GPIOManager
from api_client import ApiClient


class EMSController:
    """
    Single Raspberry Pi runtime entry point.

    Responsibilities:

    - initialize persistent storage
    - restore local state
    - reconcile physical GPIO state
    - synchronize with cloud
    - durably queue commands
    - execute ONE hardware command at a time
    - verify physical feedback
    - acknowledge commands
    - recover interrupted commands after reboot

    IMPORTANT:
    This is the only runtime controller.
    """

    def __init__(self):
        self.running = True
        self._lock = threading.RLock()

        self.storage = StorageManager()

        self.state = PiStateManager(
            self.storage
        )

        self.api = ApiClient()

        self.device_config = {
            "hardware_profile": "EMS-4CH-v1",
            "feedback_hardware_installed": False,
            "slots": {
                slot: {
                    "target_days": 0,
                    "disabled": True,
                    "display_name": f"Slot {slot}",
                    "feedback_enabled": False,
                }
                for slot in SUPPORTED_SLOTS
            },
        }

        self.gpio = GPIOManager(
            self.state,
            self.device_config,
        )

        self.queue = OfflineQueue(
            self.storage
        )

        self._last_sync = 0.0
        self._last_telemetry = 0.0
        self._last_queue_cleanup = 0.0

        self._install_signal_handlers()

    # ============================================================
    # SIGNALS
    # ============================================================

    def _install_signal_handlers(self):
        signal.signal(
            signal.SIGTERM,
            self._signal_handler,
        )
        signal.signal(
            signal.SIGINT,
            self._signal_handler,
        )

    def _signal_handler(
        self,
        signum,
        frame,
    ):
        logger.info(
            "Shutdown signal received: %s",
            signum,
        )

        self.running = False

    # ============================================================
    # BOOT
    # ============================================================

    def boot(self):
        logger.info(
            "EMS controller booting. device=%s",
            DEVICE_ID,
        )

        self.state.system_state = (
            SystemState.SELF_TEST
        )

        self.state.save_state(
            immediate=True
        )

        if not self.gpio.reconcile_hardware_state():
            logger.critical(
                "Hardware reconciliation failed."
            )
            self.state.system_state = (
                SystemState.FAULT
            )
            self.state.save_state(
                immediate=True
            )
            return False

        self._recover_interrupted_commands()

        if self.state.system_state != SystemState.FAULT:
            self.state.system_state = (
                SystemState.READY
            )
            self.state.save_state(
                immediate=True
            )

        logger.info(
            "EMS controller boot complete."
        )

        return (
            self.state.system_state
            != SystemState.FAULT
        )

    # ============================================================
    # CONFIG
    # ============================================================

    def _apply_cloud_config(
        self,
        response: dict,
    ):
        profile = response.get(
            "hardware_profile"
        )

        if profile:
            self.device_config[
                "hardware_profile"
            ] = profile

        self.device_config[
            "feedback_hardware_installed"
        ] = bool(
            response.get(
                "feedback_hardware_installed",
                False,
            )
        )

        slots = response.get(
            "slots",
            {},
        )

        if not isinstance(slots, dict):
            return

        for slot in SUPPORTED_SLOTS:
            cloud_slot = slots.get(
                slot,
                {},
            )

            if not isinstance(
                cloud_slot,
                dict,
            ):
                continue

            self.device_config[
                "slots"
            ][slot] = {
                "target_days": max(
                    0,
                    int(
                        cloud_slot.get(
                            "target_days",
                            0,
                        )
                    ),
                ),
                "disabled": bool(
                    cloud_slot.get(
                        "disabled",
                        True,
                    )
                ),
                "display_name": str(
                    cloud_slot.get(
                        "display_name",
                        f"Slot {slot}",
                    )
                ),
                "feedback_enabled": bool(
                    cloud_slot.get(
                        "feedback_enabled",
                        False,
                    )
                ),
            }

    # ============================================================
    # SNAPSHOT
    # ============================================================

    def _build_snapshot(self):
        slots = {}

        for slot in SUPPORTED_SLOTS:
            slot_state = self.state.slots[
                slot
            ]

            feedback = (
                slot_state.feedback_state.value
            )

            if feedback == "ON":
                physical = "ON"
            elif feedback == "OFF":
                physical = "OFF"
            else:
                physical = "UNKNOWN"

            slots[slot] = {
                "physical_toggle": physical,
                "used_days": 0,
                "clicks": 0,
            }

        resource_status = (
            self.storage.get_status()
        )

        memory = resource_status.get(
            "memory",
            {},
        )

        storage = resource_status.get(
            "storage",
            {},
        )

        return {
            "deviceId": DEVICE_ID,
            "firmwareVersion": "7.0.0",
            "active_slot": self.state.active_slot,
            "resetDay": 15,
            "emergencyStop": (
                self.state.system_state
                == SystemState.FAULT
            ),
            "uptimeSeconds": int(
                time.monotonic()
            ),
            "cpuTemp": 0.0,
            "diskFreeMB": float(
                storage.get(
                    "free_mb",
                    0,
                )
            ),
            "bootCount": 0,
            "watchdogEnabled": True,
            "clockSource": "system",
            "slots": slots,
            "memory": memory,
        }

    # ============================================================
    # CLOUD SYNC
    # ============================================================

    def sync_cloud(self):
        snapshot = (
            self._build_snapshot()
        )

        response = self.api.sync(
            snapshot
        )

        if response is None:
            if (
                self.state.system_state
                != SystemState.FAULT
            ):
                self.state.system_state = (
                    SystemState.CLOUD_OFFLINE
                )
            return False

        self._apply_cloud_config(
            response
        )

        self._last_sync = time.monotonic()

        if self.state.system_state == (
            SystemState.CLOUD_OFFLINE
        ):
            self.state.system_state = (
                SystemState.READY
            )

        command = response.get(
            "command"
        )

        command_id = response.get(
            "command_id"
        )

        if command and command_id:
            self._accept_cloud_command(
                response
            )

        return True

    # ============================================================
    # COMMAND ACCEPTANCE
    # ============================================================

    def _accept_cloud_command(
        self,
        response: dict,
    ):
        command_id = str(
            response.get(
                "command_id"
            )
        )

        command = str(
            response.get(
                "command",
                "",
            )
        )

        slot = str(
            response.get(
                "slot",
                "",
            )
        )

        if slot and slot not in SUPPORTED_SLOTS:
            logger.critical(
                "Rejected command with invalid slot: %s",
                slot,
            )
            return

        normalized = {
            "set_active_slot":
                "ACTIVATE",
            "off_slot":
                "DEACTIVATE",
            "off_all":
                "DEACTIVATE_ALL",
        }.get(command)

        if normalized is None:
            logger.warning(
                "Command %s is non-hardware or unsupported on Pi: %s",
                command_id,
                command,
            )

            # Configuration commands are already delivered
            # through the sync configuration.
            if command in {
                "set_days",
                "set_reset_day",
                "reset_days",
                "lcd_display",
            }:
                self.api.push_ack(
                    command_id,
                    "COMPLETED",
                    "NOT_AVAILABLE",
                )

            return

        created_at = datetime.now(
            timezone.utc
        ).isoformat()

        expires_at = (
            datetime.now(
                timezone.utc
            )
            + timedelta(minutes=5)
        ).isoformat()

        self.queue.add_command(
            command_id,
            slot,
            normalized,
            created_at,
            expires_at,
            response.get(
                "config_version"
            ),
        )

    # ============================================================
    # COMMAND EXECUTION
    # ============================================================

    def process_one_command(self):
        claimed = (
            self.queue.claim_next()
        )

        if not claimed:
            return False

        command_id, slot, action = claimed

        logger.info(
            "Executing command %s action=%s slot=%s",
            command_id,
            action,
            slot,
        )

        self.state.system_state = (
            SystemState.EXECUTING
        )

        self.state.save_state(
            immediate=True
        )

        success = False
        verification = (
            VerificationState.PENDING.value
        )
        error = None

        try:
            if action == "ACTIVATE":
                success = (
                    self.gpio.transition_slot(
                        slot
                    )
                )

            elif action == "DEACTIVATE":
                success = (
                    self.gpio.deactivate_slot(
                        slot
                    )
                )

            elif action == "DEACTIVATE_ALL":
                success = (
                    self._deactivate_all()
                )

            else:
                error = (
                    f"Unsupported action: {action}"
                )

            if success:
                slot_obj = (
                    self.state.slots.get(slot)
                    if slot
                    else None
                )

                if slot_obj:
                    verification = (
                        slot_obj
                        .verification_state
                        .value
                    )

                self.queue.update_status(
                    command_id,
                    "HARDWARE_VERIFIED",
                    verification,
                )

                self.queue.update_status(
                    command_id,
                    "COMPLETED",
                    verification,
                )

            else:
                if error is None:
                    error = (
                        "Hardware command failed"
                    )

                verification = (
                    self.state.slots.get(
                        slot
                    ).verification_state.value
                    if slot
                    and slot in self.state.slots
                    else "UNKNOWN"
                )

                self.queue.update_status(
                    command_id,
                    "FAILED",
                    verification,
                    error,
                )

        except Exception as exc:
            error = str(exc)

            logger.critical(
                "Command execution exception: %s",
                exc,
            )

            self.queue.update_status(
                command_id,
                "FAILED",
                verification,
                error,
            )

        finally:
            if (
                self.state.system_state
                != SystemState.FAULT
            ):
                self.state.system_state = (
                    SystemState.READY
                )

            self.state.save_state(
                immediate=True
            )

        return True

    # ============================================================
    # DEACTIVATE ALL
    # ============================================================

    def _deactivate_all(self):
        success = True

        for slot in SUPPORTED_SLOTS:
            try:
                if not self.gpio.deactivate_slot(
                    slot
                ):
                    success = False
            except Exception as exc:
                logger.critical(
                    "Failed to deactivate %s: %s",
                    slot,
                    exc,
                )
                success = False

        return success

    # ============================================================
    # INTERRUPTED COMMAND RECOVERY
    # ============================================================

    def _recover_interrupted_commands(self):
        interrupted = (
            self.queue.get_interrupted()
        )

        if not interrupted:
            return

        logger.critical(
            "Recovering %d interrupted command(s).",
            len(interrupted),
        )

        if not self.gpio.reconcile_hardware_state():
            self.state.system_state = (
                SystemState.FAULT
            )
            return

        for command_id, slot, action in interrupted:
            try:
                hardware_active = (
                    self.state.active_slot
                )

                if action == "ACTIVATE":
                    if hardware_active == slot:
                        verification = (
                            VerificationState
                            .VERIFIED_ON
                            .value
                        )

                        self.queue.update_status(
                            command_id,
                            "HARDWARE_VERIFIED",
                            verification,
                        )

                        self.queue.update_status(
                            command_id,
                            "COMPLETED",
                            verification,
                        )
                    else:
                        self.queue.update_status(
                            command_id,
                            "UNKNOWN_AFTER_REBOOT",
                            "UNKNOWN",
                            "COMMAND_INTERRUPTED_BY_REBOOT",
                        )

                elif action in {
                    "DEACTIVATE",
                    "DEACTIVATE_ALL",
                }:
                    if hardware_active is None:
                        self.queue.update_status(
                            command_id,
                            "HARDWARE_VERIFIED",
                            VerificationState
                            .VERIFIED_OFF
                            .value,
                        )

                        self.queue.update_status(
                            command_id,
                            "COMPLETED",
                            VerificationState
                            .VERIFIED_OFF
                            .value,
                        )
                    else:
                        self.queue.update_status(
                            command_id,
                            "UNKNOWN_AFTER_REBOOT",
                            "UNKNOWN",
                            "COMMAND_INTERRUPTED_BY_REBOOT",
                        )

            except Exception as exc:
                logger.critical(
                    "Interrupted command recovery failed: %s",
                    exc,
                )

    # ============================================================
    # ACK
    # ============================================================

    def flush_acks(self):
        rows = (
            self.queue.get_unacked()
        )

        for (
            command_id,
            status,
            verification,
            error,
        ) in rows:
            try:
                if self.api.push_ack(
                    command_id,
                    status,
                    verification or "UNKNOWN",
                    error,
                ):
                    self.queue.mark_acked(
                        command_id
                    )
            except Exception as exc:
                logger.warning(
                    "ACK retry failed: %s",
                    exc,
                )

    # ============================================================
    # MAIN LOOP
    # ============================================================

    def run(self):
        if not self.boot():
            logger.critical(
                "EMS controller entered FAULT."
            )

        while self.running:
            try:
                now = time.monotonic()

                if (
                    now - self._last_sync
                    >= SYNC_INTERVAL_S
                ):
                    self.sync_cloud()

                self.process_one_command()

                self.flush_acks()

                if (
                    now - self._last_queue_cleanup
                    >= 3600
                ):
                    self.queue.cleanup_acked()
                    self._last_queue_cleanup = now

                time.sleep(0.25)

            except Exception as exc:
                logger.critical(
                    "Main controller loop failure: %s",
                    exc,
                )
                time.sleep(2)

        self.shutdown()

    # ============================================================
    # SHUTDOWN
    # ============================================================

    def shutdown(self):
        logger.info(
            "EMS controller shutting down."
        )

        try:
            self.queue.close()
        except Exception:
            pass

        try:
            self.api.close()
        except Exception:
            pass

        try:
            self.gpio.stop()
        except Exception:
            pass

        try:
            self.storage.stop()
        except Exception:
            pass


def main():
    controller = EMSController()
    controller.run()


if __name__ == "__main__":
    main()