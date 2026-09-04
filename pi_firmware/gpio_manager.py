import threading
import time

from config import (
    get_hardware_profile,
    FEEDBACK_TIMEOUT_MS,
    FEEDBACK_DEBOUNCE_MS,
    INTERLOCK_DELAY_MS,
    LOCAL_MONITOR_INTERVAL_S,
)

from state import (
    PiStateManager,
    CommandedState,
    GpioOutputState,
    FeedbackState,
    VerificationState,
    SystemState,
)

from logger import logger


try:
    from gpiozero import OutputDevice, Button

except ImportError:

    logger.warning(
        "gpiozero unavailable. "
        "Using mock GPIO hardware."
    )

    class OutputDevice:
        def __init__(self, pin):
            self.pin = pin
            self.is_active = False

        def on(self):
            self.is_active = True

        def off(self):
            self.is_active = False

    class Button:
        def __init__(
            self,
            pin,
            pull_up=False,
        ):
            self.pin = pin
            self._state = False

        @property
        def is_pressed(self):
            return self._state


class GPIOManager:

    def __init__(
        self,
        state_manager: PiStateManager,
        device_config: dict,
    ):
        self.profile = get_hardware_profile(
            device_config.get(
                "hardware_profile",
                "EMS-4CH-v1",
            )
        )

        self.state_manager = state_manager
        self.device_config = device_config

        self.relays = {}
        self.feedback_inputs = {}

        self._lock = threading.RLock()
        self._running = True

        for slot, pin in self.profile[
            "relay_gpio"
        ].items():
            self.relays[slot] = OutputDevice(
                pin
            )

        for slot, pin in self.profile[
            "feedback_gpio"
        ].items():
            self.feedback_inputs[slot] = (
                Button(
                    pin,
                    pull_up=False,
                )
            )

        self.monitor_thread = threading.Thread(
            target=self._local_monitor_loop,
            name="EMS-GPIO-Monitor",
            daemon=True,
        )

        self.monitor_thread.start()

    # ------------------------------------------------------------
    # CONFIG
    # ------------------------------------------------------------

    def _is_feedback_enabled(
        self,
        slot,
    ):
        return bool(
            self.device_config
            .get("slots", {})
            .get(slot, {})
            .get("feedback_enabled", False)
        )

    def _read_feedback_raw(
        self,
        slot,
    ):
        button = self.feedback_inputs[
            slot
        ]

        first = bool(
            button.is_pressed
        )

        time.sleep(
            FEEDBACK_DEBOUNCE_MS
            / 1000.0
        )

        second = bool(
            button.is_pressed
        )

        return first and second

    # ------------------------------------------------------------
    # VERIFICATION
    # ------------------------------------------------------------

    def verify_slot(
        self,
        slot,
        expected_commanded,
    ):
        if not self._is_feedback_enabled(
            slot
        ):
            return (
                VerificationState.NOT_CONFIGURED
            )

        physical_closed = (
            self._read_feedback_raw(
                slot
            )
        )

        # IMPORTANT:
        # Feedback TRUE means contactor CLOSED / ON.
        #
        # Therefore:
        #
        # expected ON + TRUE  = VERIFIED_ON
        # expected OFF + FALSE = VERIFIED_OFF
        #
        # This fixes the previous reversed OFF logic.

        if (
            expected_commanded
            == CommandedState.ON
        ):
            if physical_closed:
                return (
                    VerificationState.VERIFIED_ON
                )

            return (
                VerificationState.MISMATCH_ON_OFF
            )

        if (
            expected_commanded
            == CommandedState.OFF
        ):
            if not physical_closed:
                return (
                    VerificationState.VERIFIED_OFF
                )

            return (
                VerificationState.MISMATCH_OFF_ON
            )

        return VerificationState.PENDING

    # ------------------------------------------------------------
    # BOOT RECONCILIATION
    # ------------------------------------------------------------

    def reconcile_hardware_state(self):

        with self._lock:
            self.state_manager.system_state = (
                SystemState.HARDWARE_RECONCILIATION
            )

            active_relays = []
            active_feedbacks = []

            for slot in self.profile[
                "slots"
            ]:

                relay_on = bool(
                    self.relays[
                        slot
                    ].is_active
                )

                if relay_on:
                    active_relays.append(
                        slot
                    )

                    self.state_manager.set_gpio_output(
                        slot,
                        GpioOutputState.ON,
                    )

                else:
                    self.state_manager.set_gpio_output(
                        slot,
                        GpioOutputState.OFF,
                    )

                if self._is_feedback_enabled(
                    slot
                ):
                    feedback_on = (
                        self._read_feedback_raw(
                            slot
                        )
                    )

                    self.state_manager.set_feedback(
                        slot,
                        (
                            FeedbackState.ON
                            if feedback_on
                            else FeedbackState.OFF
                        ),
                    )

                    if feedback_on:
                        active_feedbacks.append(
                            slot
                        )

                else:
                    self.state_manager.set_feedback(
                        slot,
                        FeedbackState.UNKNOWN,
                    )

            # Multiple physical ON states are unsafe.
            if (
                len(active_relays) > 1
                or len(active_feedbacks) > 1
            ):
                logger.critical(
                    "CRITICAL HARDWARE FAULT: "
                    "multiple contactors detected ON."
                )

                self.state_manager.system_state = (
                    SystemState.FAULT
                )

                return False

            # Feedback has priority over relay inference.
            if len(active_feedbacks) == 1:

                slot = active_feedbacks[0]

                self.state_manager.active_slot = (
                    slot
                )

                self.state_manager.set_commanded(
                    slot,
                    CommandedState.ON,
                )

                self.state_manager.set_verification(
                    slot,
                    VerificationState.VERIFIED_ON,
                    immediate=True,
                )

            elif len(active_relays) == 1:

                slot = active_relays[0]

                if self._is_feedback_enabled(
                    slot
                ):
                    logger.critical(
                        "Slot %s relay ON but "
                        "feedback OFF during boot.",
                        slot,
                    )

                    self.state_manager.set_verification(
                        slot,
                        VerificationState.MISMATCH_ON_OFF,
                        immediate=True,
                    )

                    self.state_manager.system_state = (
                        SystemState.FAULT
                    )

                    return False

                self.state_manager.active_slot = (
                    slot
                )

                self.state_manager.set_commanded(
                    slot,
                    CommandedState.ON,
                )

                self.state_manager.set_verification(
                    slot,
                    VerificationState.GPIO_CONFIRMED,
                    immediate=True,
                )

            else:

                self.state_manager.active_slot = None

                for slot in self.profile[
                    "slots"
                ]:

                    self.state_manager.set_commanded(
                        slot,
                        CommandedState.OFF,
                    )

                    self.state_manager.set_verification(
                        slot,
                        (
                            VerificationState.VERIFIED_OFF
                            if self._is_feedback_enabled(
                                slot
                            )
                            else VerificationState.NOT_CONFIGURED
                        ),
                    )

            self.state_manager.system_state = (
                SystemState.READY
            )

            logger.info(
                "Hardware reconciliation complete."
            )

            return True

    # ------------------------------------------------------------
    # ACTIVATE / TRANSITION
    # ------------------------------------------------------------

    def transition_slot(
        self,
        target_slot,
    ):
        with self._lock:

            if (
                self.state_manager.system_state
                == SystemState.FAULT
            ):
                return False

            if target_slot not in self.relays:
                return False

            current = (
                self.state_manager.active_slot
            )

            # Break-before-make.
            if (
                current
                and current != target_slot
            ):
                if not self._turn_off_and_verify(
                    current
                ):
                    return False

                time.sleep(
                    INTERLOCK_DELAY_MS
                    / 1000.0
                )

            # Target ON.
            self.relays[
                target_slot
            ].on()

            self.state_manager.set_gpio_output(
                target_slot,
                GpioOutputState.ON,
            )

            self.state_manager.set_commanded(
                target_slot,
                CommandedState.ON,
            )

            deadline = (
                time.monotonic()
                + FEEDBACK_TIMEOUT_MS
                / 1000.0
            )

            feedback_enabled = (
                self._is_feedback_enabled(
                    target_slot
                )
            )

            while True:

                if feedback_enabled:

                    verification = (
                        self.verify_slot(
                            target_slot,
                            CommandedState.ON,
                        )
                    )

                    if (
                        verification
                        == VerificationState.VERIFIED_ON
                    ):
                        self.state_manager.set_feedback(
                            target_slot,
                            FeedbackState.ON,
                        )

                        self.state_manager.set_verification(
                            target_slot,
                            verification,
                            immediate=True,
                        )

                        self.state_manager.active_slot = (
                            target_slot
                        )

                        return True

                    if (
                        time.monotonic()
                        >= deadline
                    ):
                        self.relays[
                            target_slot
                        ].off()

                        self.state_manager.set_gpio_output(
                            target_slot,
                            GpioOutputState.OFF,
                        )

                        self.state_manager.set_commanded(
                            target_slot,
                            CommandedState.OFF,
                        )

                        self.state_manager.set_verification(
                            target_slot,
                            VerificationState.TIMEOUT,
                            immediate=True,
                        )

                        self.state_manager.system_state = (
                            SystemState.FAULT
                        )

                        logger.critical(
                            "Slot %s failed ON verification.",
                            target_slot,
                        )

                        return False

                else:

                    self.state_manager.set_verification(
                        target_slot,
                        VerificationState.GPIO_CONFIRMED,
                        immediate=True,
                    )

                    self.state_manager.active_slot = (
                        target_slot
                    )

                    return True

                time.sleep(0.05)

    # ------------------------------------------------------------
    # DEACTIVATE
    # ------------------------------------------------------------

    def deactivate_slot(
        self,
        target_slot,
    ):
        with self._lock:

            if (
                self.state_manager.system_state
                == SystemState.FAULT
            ):
                return False

            if target_slot not in self.relays:
                return False

            self.relays[
                target_slot
            ].off()

            self.state_manager.set_gpio_output(
                target_slot,
                GpioOutputState.OFF,
            )

            self.state_manager.set_commanded(
                target_slot,
                CommandedState.OFF,
            )

            feedback_enabled = (
                self._is_feedback_enabled(
                    target_slot
                )
            )

            if not feedback_enabled:

                if (
                    self.state_manager.active_slot
                    == target_slot
                ):
                    self.state_manager.active_slot = (
                        None
                    )

                self.state_manager.set_verification(
                    target_slot,
                    VerificationState.GPIO_CONFIRMED,
                    immediate=True,
                )

                return True

            deadline = (
                time.monotonic()
                + FEEDBACK_TIMEOUT_MS
                / 1000.0
            )

            while time.monotonic() < deadline:

                verification = (
                    self.verify_slot(
                        target_slot,
                        CommandedState.OFF,
                    )
                )

                if (
                    verification
                    == VerificationState.VERIFIED_OFF
                ):
                    self.state_manager.set_feedback(
                        target_slot,
                        FeedbackState.OFF,
                    )

                    if (
                        self.state_manager.active_slot
                        == target_slot
                    ):
                        self.state_manager.active_slot = (
                            None
                        )

                    self.state_manager.set_verification(
                        target_slot,
                        VerificationState.VERIFIED_OFF,
                        immediate=True,
                    )

                    return True

                if (
                    verification
                    == VerificationState.MISMATCH_OFF_ON
                ):
                    logger.critical(
                        "Slot %s contactor remains ON "
                        "after DEACTIVATE.",
                        target_slot,
                    )

                    self.state_manager.set_verification(
                        target_slot,
                        verification,
                        immediate=True,
                    )

                    self.state_manager.system_state = (
                        SystemState.FAULT
                    )

                    return False

                time.sleep(0.05)

            logger.critical(
                "Slot %s OFF verification timeout.",
                target_slot,
            )

            self.state_manager.set_verification(
                target_slot,
                VerificationState.TIMEOUT,
                immediate=True,
            )

            self.state_manager.system_state = (
                SystemState.FAULT
            )

            return False

    # ------------------------------------------------------------
    # HELPER
    # ------------------------------------------------------------

    def _turn_off_and_verify(
        self,
        slot,
    ):
        return self.deactivate_slot(
            slot
        )

    # ------------------------------------------------------------
    # LOCAL MONITOR
    # ------------------------------------------------------------

    def _local_monitor_loop(self):

        while self._running:

            try:

                if (
                    self.state_manager.system_state
                    == SystemState.READY
                ):

                    active_feedbacks = []

                    for slot in self.profile[
                        "slots"
                    ]:

                        if not self._is_feedback_enabled(
                            slot
                        ):
                            continue

                        expected = (
                            self.state_manager
                            .slots[slot]
                            .commanded_state
                        )

                        if expected not in (
                            CommandedState.ON,
                            CommandedState.OFF,
                        ):
                            continue

                        verification = (
                            self.verify_slot(
                                slot,
                                expected,
                            )
                        )

                        if expected == CommandedState.ON:
                            if (
                                verification
                                == VerificationState.VERIFIED_ON
                            ):
                                self.state_manager.set_feedback(
                                    slot,
                                    FeedbackState.ON,
                                )

                            elif (
                                verification
                                == VerificationState.MISMATCH_ON_OFF
                            ):
                                logger.critical(
                                    "Slot %s unexpectedly OFF.",
                                    slot,
                                )

                                self.state_manager.set_verification(
                                    slot,
                                    verification,
                                    immediate=True,
                                )

                                self.state_manager.system_state = (
                                    SystemState.FAULT
                                )

                        elif expected == CommandedState.OFF:

                            if (
                                verification
                                == VerificationState.VERIFIED_OFF
                            ):
                                self.state_manager.set_feedback(
                                    slot,
                                    FeedbackState.OFF,
                                )

                            elif (
                                verification
                                == VerificationState.MISMATCH_OFF_ON
                            ):
                                logger.critical(
                                    "DANGER: Slot %s "
                                    "unexpectedly ON.",
                                    slot,
                                )

                                self.state_manager.set_verification(
                                    slot,
                                    verification,
                                    immediate=True,
                                )

                                self.state_manager.system_state = (
                                    SystemState.FAULT
                                )

                time.sleep(
                    LOCAL_MONITOR_INTERVAL_S
                )

            except Exception as exc:

                logger.critical(
                    "GPIO monitor failure: %s",
                    exc,
                )

                time.sleep(1)

    def stop(self):
        self._running = False