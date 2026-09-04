import threading
import time

from config import (
    SYNC_INTERVAL_S,
    SUPPORTED_SLOTS,
)

from state import (
    PiStateManager,
    SystemState,
    VerificationState,
)

from gpio_manager import GPIOManager
from api_client import ApiClient
from offline_queue import OfflineQueue
from storage_manager import StorageManager

from logger import logger


class EmsController:

    def __init__(self):

        logger.info(
            "Initializing EMS Controller..."
        )

        self.storage = StorageManager()

        self.state_manager = PiStateManager(
            self.storage
        )

        self.queue = OfflineQueue(
            self.storage
        )

        self.api = ApiClient()

        self.device_config = {}
        self.gpio_manager = None

        self._running = True

        self.last_telemetry_day = (
            time.strftime("%Y-%m-%d")
        )

    # ============================================================
    # CONFIG VALIDATION
    # ============================================================

    def validate_config(
        self,
        config,
    ):

        if not isinstance(
            config,
            dict,
        ):
            return False

        if (
            config.get("device_id")
            != self.api.device_id
        ):
            return False

        if config.get(
            "hardware_profile"
        ) not in (
            "EMS-4CH-v1",
        ):
            return False

        if set(
            config.get(
                "slots",
                {},
            ).keys()
        ) != set(
            SUPPORTED_SLOTS
        ):
            return False

        if not config.get(
            "config_version"
        ):
            return False

        feedback_installed = config.get(
            "feedback_hardware_installed"
        )

        if not isinstance(
            feedback_installed,
            bool,
        ):
            return False

        for slot in SUPPORTED_SLOTS:

            cfg = config[
                "slots"
            ].get(slot)

            if not isinstance(
                cfg,
                dict,
            ):
                return False

            display_name = cfg.get(
                "display_name"
            )

            if not isinstance(
                display_name,
                str,
            ):
                return False

            if not (
                1 <= len(
                    display_name.strip()
                ) <= 50
            ):
                return False

            target_days = cfg.get(
                "target_days"
            )

            if not isinstance(
                target_days,
                int,
            ):
                return False

            if not (
                0 <= target_days <= 365
            ):
                return False

            if not isinstance(
                cfg.get(
                    "disabled"
                ),
                bool,
            ):
                return False

            feedback_enabled = cfg.get(
                "feedback_enabled"
            )

            if not isinstance(
                feedback_enabled,
                bool,
            ):
                return False

            if (
                feedback_enabled
                and not feedback_installed
            ):
                logger.critical(
                    "Slot %s requests feedback "
                    "without feedback hardware.",
                    slot,
                )
                return False

        return True

    # ============================================================
    # BOOT
    # ============================================================

    def run_boot_sequence(self):

        self.state_manager.system_state = (
            SystemState.BOOT
        )

        self.state_manager.system_state = (
            SystemState.SELF_TEST
        )

        self.storage.guard.evaluate_state()

        logger.info(
            "Fetching cloud configuration..."
        )

        try:
            self.device_config = (
                self.api.get_config()
            )

        except Exception as exc:

            logger.critical(
                "Cloud configuration fetch failed: %s",
                exc,
            )

            self.state_manager.system_state = (
                SystemState.CLOUD_OFFLINE
            )

            return False

        if not self.validate_config(
            self.device_config
        ):

            logger.critical(
                "Invalid cloud configuration."
            )

            self.state_manager.system_state = (
                SystemState.FAULT
            )

            return False

        self.gpio_manager = GPIOManager(
            self.state_manager,
            self.device_config,
        )

        # Hardware truth comes BEFORE queue recovery.
        if not self.gpio_manager.reconcile_hardware_state():

            logger.critical(
                "Hardware reconciliation failed."
            )

            self.state_manager.system_state = (
                SystemState.FAULT
            )

            return False

        # --------------------------------------------------------
        # Interrupted commands
        # --------------------------------------------------------

        interrupted = (
            self.queue.get_interrupted()
        )

        for (
            cmd_id,
            slot,
            action,
        ) in interrupted:

            logger.critical(
                "Command %s interrupted by reboot.",
                cmd_id,
            )

            self.queue.update_status(
                cmd_id,
                "UNKNOWN_AFTER_REBOOT",
            )

            if slot not in (
                self.state_manager.slots
            ):
                self.queue.update_status(
                    cmd_id,
                    "FAILED",
                    error="INVALID_SLOT_AFTER_REBOOT",
                )
                continue

            slot_state = (
                self.state_manager
                .slots[slot]
            )

            verification = (
                slot_state
                .verification_state
            )

            # ----------------------------------------------------
            # ACTIVATE
            # ----------------------------------------------------

            if action == "ACTIVATE":

                if (
                    verification
                    == VerificationState.VERIFIED_ON
                ):

                    self.queue.update_status(
                        cmd_id,
                        "HARDWARE_VERIFIED",
                        verification.value,
                    )

                    self.queue.update_status(
                        cmd_id,
                        "COMPLETED",
                        verification.value,
                    )

                elif (
                    verification
                    == VerificationState.GPIO_CONFIRMED
                ):

                    self.queue.update_status(
                        cmd_id,
                        "COMPLETED",
                        verification.value,
                    )

                else:

                    self.queue.update_status(
                        cmd_id,
                        "FAILED",
                        verification.value,
                        "HARDWARE_NOT_VERIFIED_AFTER_REBOOT",
                    )

            # ----------------------------------------------------
            # DEACTIVATE
            # ----------------------------------------------------

            elif action == "DEACTIVATE":

                if (
                    verification
                    == VerificationState.VERIFIED_OFF
                ):

                    self.queue.update_status(
                        cmd_id,
                        "HARDWARE_VERIFIED",
                        verification.value,
                    )

                    self.queue.update_status(
                        cmd_id,
                        "COMPLETED",
                        verification.value,
                    )

                elif (
                    verification
                    == VerificationState.GPIO_CONFIRMED
                ):

                    self.queue.update_status(
                        cmd_id,
                        "COMPLETED",
                        verification.value,
                    )

                else:

                    self.queue.update_status(
                        cmd_id,
                        "FAILED",
                        verification.value,
                        "HARDWARE_NOT_VERIFIED_AFTER_REBOOT",
                    )

            else:

                self.queue.update_status(
                    cmd_id,
                    "FAILED",
                    error="UNKNOWN_COMMAND_AFTER_REBOOT",
                )

        self.state_manager.system_state = (
            SystemState.READY
        )

        logger.info(
            "EMS Controller READY."
        )

        return True

    # ============================================================
    # COMMAND EXECUTION
    # ============================================================

    def handle_command(
        self,
        command,
    ):

        cmd_id, target_slot, action = (
            command
        )

        if target_slot not in (
            SUPPORTED_SLOTS
        ):
            self.queue.update_status(
                cmd_id,
                "FAILED",
                error="INVALID_SLOT",
            )
            return

        self.queue.update_status(
            cmd_id,
            "EXECUTING",
        )

        self.state_manager.system_state = (
            SystemState.EXECUTING
        )

        success = False
        verification = (
            VerificationState.NOT_CONFIGURED
        )

        error_msg = None

        try:

            if action == "ACTIVATE":

                success = (
                    self.gpio_manager
                    .transition_slot(
                        target_slot
                    )
                )

                if success:

                    if self.gpio_manager._is_feedback_enabled(
                        target_slot
                    ):
                        verification = (
                            VerificationState.VERIFIED_ON
                        )
                    else:
                        verification = (
                            VerificationState.GPIO_CONFIRMED
                        )

            elif action == "DEACTIVATE":

                success = (
                    self.gpio_manager
                    .deactivate_slot(
                        target_slot
                    )
                )

                if success:

                    if self.gpio_manager._is_feedback_enabled(
                        target_slot
                    ):
                        verification = (
                            VerificationState.VERIFIED_OFF
                        )
                    else:
                        verification = (
                            VerificationState.GPIO_CONFIRMED
                        )

            else:

                error_msg = (
                    "UNSUPPORTED_COMMAND"
                )

        except Exception as exc:

            error_msg = str(exc)

            logger.critical(
                "Command %s failed: %s",
                cmd_id,
                exc,
            )

        if success:

            if verification in (
                VerificationState.VERIFIED_ON,
                VerificationState.VERIFIED_OFF,
            ):

                self.queue.update_status(
                    cmd_id,
                    "HARDWARE_VERIFIED",
                    verification.value,
                )

            self.queue.update_status(
                cmd_id,
                "COMPLETED",
                verification.value,
            )

        else:

            if not error_msg:
                error_msg = (
                    "HARDWARE_TRANSITION_FAILED"
                )

            self.queue.update_status(
                cmd_id,
                "FAILED",
                verification.value,
                error_msg,
            )

            # Keep FAULT if GPIO manager detected a
            # physical safety fault.
            if (
                self.state_manager.system_state
                != SystemState.FAULT
            ):
                self.state_manager.system_state = (
                    SystemState.READY
                )

            return

        self.state_manager.system_state = (
            SystemState.READY
        )

    # ============================================================
    # CLOUD SYNC
    # ============================================================

    def sync_loop(self):

        while self._running:

            time.sleep(
                SYNC_INTERVAL_S
            )

            try:

                current_day = (
                    time.strftime(
                        "%Y-%m-%d"
                    )
                )

                if (
                    current_day
                    != self.last_telemetry_day
                ):

                    self.storage.save_daily_telemetry()

                    self.storage.persist_counters()

                    self.last_telemetry_day = (
                        current_day
                    )

                if (
                    self.state_manager.system_state
                    != SystemState.READY
                ):
                    continue

                snapshot = {
                    "system_state":
                        self.state_manager
                        .system_state
                        .value,

                    "active_slot":
                        self.state_manager
                        .active_slot,

                    "slots": {
                        code:
                            state.to_dict()
                        for code, state
                        in self.state_manager
                        .slots
                        .items()
                    },
                }

                self.api.push_state(
                    snapshot
                )

                commands = (
                    self.api.get_commands()
                )

                for command in commands:

                    self.queue.add_command(
                        command["id"],
                        command["slot"],
                        command["action"],
                        command.get(
                            "created_at"
                        ),
                        command.get(
                            "expires_at"
                        ),
                    )

                unacked = (
                    self.queue.get_unacked()
                )

                for (
                    cmd_id,
                    status,
                    verification,
                ) in unacked:

                    if self.api.push_ack(
                        cmd_id,
                        status,
                        verification,
                    ):

                        self.queue.mark_acked(
                            cmd_id
                        )

                    # Avoid a burst of ACK writes.
                    break

                self.queue.cleanup_acked()

            except Exception as exc:

                logger.error(
                    "Cloud sync cycle failed: %s",
                    exc,
                )

    # ============================================================
    # MAIN LOOP
    # ============================================================

    def main_loop(self):

        if not self.run_boot_sequence():

            logger.critical(
                "Boot sequence failed. "
                "Entering safe fault mode."
            )

            while self._running:

                time.sleep(1)

            return

        sync_thread = threading.Thread(
            target=self.sync_loop,
            name="EMS-CloudSync",
            daemon=True,
        )

        sync_thread.start()

        while self._running:

            try:

                if (
                    self.state_manager.system_state
                    == SystemState.FAULT
                ):
                    time.sleep(1)
                    continue

                command = (
                    self.queue.get_next()
                )

                if command:

                    self.handle_command(
                        command
                    )

                else:

                    # RAM-only idle period.
                    time.sleep(1)

            except Exception as exc:

                logger.critical(
                    "Main controller loop failure: %s",
                    exc,
                )

                time.sleep(1)


if __name__ == "__main__":

    controller = EmsController()

    try:

        controller.main_loop()

    except KeyboardInterrupt:

        logger.info(
            "Shutting down EMS Controller..."
        )

        controller._running = False

        try:
            controller.storage.stop()
        except Exception:
            pass

        try:
            controller.queue.close()
        except Exception:
            pass