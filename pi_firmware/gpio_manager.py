import threading
import time

from config import (
    get_hardware_profile,
    FEEDBACK_TIMEOUT_MS,
    FEEDBACK_DEBOUNCE_MS,
    INTERLOCK_DELAY_MS,
    LOCAL_MONITOR_INTERVAL_S,
    FEEDBACK_ACTIVE_WHEN_PRESSED,
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
        "gpiozero not found. Using mock hardware."
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

        for slot, relay_pin in self.profile[
            "relay_gpio"
        ].items():

            self.relays[slot] = OutputDevice(
                relay_pin
            )

        for slot, fb_pin in self.profile[
            "feedback_gpio"
        ].items():

            self.feedback_inputs[slot] = Button(
                fb_pin,
                pull_up=False,
            )

        self.monitor_thread = threading.Thread(
            target=self._local_monitor_loop,
            name="EMS-GPIO-Monitor",
            daemon=True,
        )

        self.monitor_thread.start()

    # ============================================================
    # CONFIG
    # ============================================================

    def _is_feedback_enabled(
        self,
        slot: str,
    ) -> bool:

        return bool(
            self.device_config
            .get("slots", {})
            .get(slot, {})
            .get("feedback_enabled", False)
        )

    # ============================================================
    # FEEDBACK
    # ============================================================

    def _read_feedback_raw(
        self,
        slot: str,
    ) -> bool:

        btn = self.feedback_inputs[slot]

        first = bool(
            btn.is_pressed()
        )

        time.sleep(
            FEEDBACK_DEBOUNCE_MS / 1000.0
        )

        second = bool(
            btn.is_pressed()
        )

        pressed = first and second

        if FEEDBACK_ACTIVE_WHEN_PRESSED:
            return pressed

        return not pressed

    # ============================================================
    # VERIFICATION
    # ============================================================

    def verify_slot(
        self,
        slot: str,
        expected_commanded: CommandedState,
    ) -> VerificationState:

        if not self._is_feedback_enabled(slot):
            return VerificationState.NOT_CONFIGURED

        is_on = self._read_feedback_raw(slot)

        if expected_commanded == CommandedState.ON:

            if is_on:
                self.state_manager.set_feedback(
                    slot,
                    FeedbackState.ON,
                )

                return VerificationState.VERIFIED_ON

            self.state_manager.set_feedback(
                slot,
                FeedbackState.OFF,
            )

            return VerificationState.MISMATCH_ON_OFF

        if expected_commanded == CommandedState.OFF:

            if not is_on:
                self.state_manager.set_feedback(
                    slot,
                    FeedbackState.OFF,
                )

                return VerificationState.VERIFIED_OFF

            self.state_manager.set_feedback(
                slot,
                FeedbackState.ON,
            )

            return VerificationState.MISMATCH_OFF_ON

        return VerificationState.PENDING

    # ============================================================
    # HARDWARE RECONCILIATION
    # ============================================================

    def reconcile_hardware_state(self):

        self.state_manager.system_state = (
            SystemState.HARDWARE_RECONCILIATION
        )

        logger.info(
            "Starting strict hardware reconciliation."
        )

        active_relays = []
        active_feedbacks = []

        # --------------------------------------------------------
        # Read ALL channels.
        # --------------------------------------------------------

        for slot in self.profile["slots"]:

            relay_on = bool(
                self.relays[slot].is_active
            )

            self.state_manager.set_gpio_output(
                slot,
                (
                    GpioOutputState.ON
                    if relay_on
                    else GpioOutputState.OFF
                ),
            )

            if relay_on:
                active_relays.append(slot)

            if self._is_feedback_enabled(slot):

                feedback_on = (
                    self._read_feedback_raw(slot)
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
                    active_feedbacks.append(slot)

            else:

                self.state_manager.set_feedback(
                    slot,
                    FeedbackState.UNKNOWN,
                )

        # --------------------------------------------------------
        # Interlock violation.
        # --------------------------------------------------------

        if len(active_relays) > 1:

            logger.critical(
                "FAULT: Multiple relay outputs ON: %s",
                active_relays,
            )

            self.state_manager.system_state = (
                SystemState.FAULT
            )

            return False

        if len(active_feedbacks) > 1:

            logger.critical(
                "FAULT: Multiple contactors ON: %s",
                active_feedbacks,
            )

            self.state_manager.system_state = (
                SystemState.FAULT
            )

            return False

        # --------------------------------------------------------
        # Strict relay ↔ feedback agreement.
        # --------------------------------------------------------

        for slot in self.profile["slots"]:

            relay_on = (
                slot in active_relays
            )

            if not self._is_feedback_enabled(slot):
                continue

            feedback_on = (
                slot in active_feedbacks
            )

            if relay_on != feedback_on:

                logger.critical(
                    "FAULT: Slot %s relay/feedback mismatch "
                    "(relay=%s feedback=%s).",
                    slot,
                    relay_on,
                    feedback_on,
                )

                self.state_manager.set_verification(
                    slot,
                    (
                        VerificationState.MISMATCH_ON_OFF
                        if relay_on
                        else VerificationState.MISMATCH_OFF_ON
                    ),
                    immediate=True,
                )

                self.state_manager.system_state = (
                    SystemState.FAULT
                )

                return False

        # --------------------------------------------------------
        # Determine state.
        # --------------------------------------------------------

        if active_relays:

            slot = active_relays[0]

            self.state_manager.active_slot = slot

            self.state_manager.set_commanded(
                slot,
                CommandedState.ON,
            )

            self.state_manager.set_verification(
                slot,
                (
                    VerificationState.VERIFIED_ON
                    if self._is_feedback_enabled(slot)
                    else VerificationState.GPIO_CONFIRMED
                ),
                immediate=True,
            )

            for other in self.profile["slots"]:

                if other == slot:
                    continue

                self.state_manager.set_commanded(
                    other,
                    CommandedState.OFF,
                )

                self.state_manager.set_verification(
                    other,
                    (
                        VerificationState.VERIFIED_OFF
                        if self._is_feedback_enabled(other)
                        else VerificationState.NOT_CONFIGURED
                    ),
                )

        else:

            self.state_manager.active_slot = None

            for slot in self.profile["slots"]:

                self.state_manager.set_commanded(
                    slot,
                    CommandedState.OFF,
                )

                self.state_manager.set_verification(
                    slot,
                    (
                        VerificationState.VERIFIED_OFF
                        if self._is_feedback_enabled(slot)
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

    # ============================================================
    # ACTIVATE
    # ============================================================

    def transition_slot(
        self,
        target_slot: str,
    ) -> bool:

        if target_slot not in self.relays:
            return False

        with self._lock:

            if (
                self.state_manager.system_state
                == SystemState.FAULT
            ):
                return False

            current_active = (
                self.state_manager.active_slot
            )

            # ----------------------------------------------------
            # BREAK BEFORE MAKE
            # ----------------------------------------------------

            if (
                current_active
                and current_active != target_slot
            ):

                self.relays[
                    current_active
                ].off()

                self.state_manager.set_gpio_output(
                    current_active,
                    GpioOutputState.OFF,
                )

                self.state_manager.set_commanded(
                    current_active,
                    CommandedState.OFF,
                )

                if self._is_feedback_enabled(
                    current_active
                ):

                    start = time.monotonic()

                    while True:

                        result = self.verify_slot(
                            current_active,
                            CommandedState.OFF,
                        )

                        if (
                            result
                            == VerificationState.VERIFIED_OFF
                        ):
                            break

                        if result == (
                            VerificationState.MISMATCH_OFF_ON
                        ):

                            logger.critical(
                                "Slot %s appears welded.",
                                current_active,
                            )

                            self.state_manager.system_state = (
                                SystemState.FAULT
                            )

                            return False

                        if (
                            time.monotonic() - start
                        ) * 1000 > FEEDBACK_TIMEOUT_MS:

                            logger.critical(
                                "Slot %s opening timeout.",
                                current_active,
                            )

                            self.state_manager.system_state = (
                                SystemState.FAULT
                            )

                            return False

                        time.sleep(0.05)

                time.sleep(
                    INTERLOCK_DELAY_MS / 1000.0
                )

            # ----------------------------------------------------
            # MAKE
            # ----------------------------------------------------

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

            start = time.monotonic()

            while True:

                if self._is_feedback_enabled(
                    target_slot
                ):

                    result = self.verify_slot(
                        target_slot,
                        CommandedState.ON,
                    )

                    if result == (
                        VerificationState.VERIFIED_ON
                    ):

                        self.state_manager.active_slot = (
                            target_slot
                        )

                        self.state_manager.set_verification(
                            target_slot,
                            VerificationState.VERIFIED_ON,
                            immediate=True,
                        )

                        return True

                else:

                    self.state_manager.active_slot = (
                        target_slot
                    )

                    self.state_manager.set_verification(
                        target_slot,
                        VerificationState.GPIO_CONFIRMED,
                        immediate=True,
                    )

                    return True

                if (
                    time.monotonic() - start
                ) * 1000 > FEEDBACK_TIMEOUT_MS:

                    logger.critical(
                        "Slot %s failed to close.",
                        target_slot,
                    )

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

                    return False

                time.sleep(0.05)

    # ============================================================
    # DEACTIVATE
    # ============================================================

    def deactivate_slot(
        self,
        target_slot: str,
    ) -> bool:

        if target_slot not in self.relays:
            return False

        with self._lock:

            if (
                self.state_manager.system_state
                == SystemState.FAULT
            ):
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

            if not self._is_feedback_enabled(
                target_slot
            ):

                if (
                    self.state_manager.active_slot
                    == target_slot
                ):
                    self.state_manager.active_slot = None

                self.state_manager.set_verification(
                    target_slot,
                    VerificationState.GPIO_CONFIRMED,
                    immediate=True,
                )

                return True

            start = time.monotonic()

            while True:

                result = self.verify_slot(
                    target_slot,
                    CommandedState.OFF,
                )

                if result == (
                    VerificationState.VERIFIED_OFF
                ):

                    if (
                        self.state_manager.active_slot
                        == target_slot
                    ):
                        self.state_manager.active_slot = None

                    self.state_manager.set_verification(
                        target_slot,
                        VerificationState.VERIFIED_OFF,
                        immediate=True,
                    )

                    return True

                if result == (
                    VerificationState.MISMATCH_OFF_ON
                ):

                    logger.critical(
                        "Slot %s welded or stuck ON.",
                        target_slot,
                    )

                    self.state_manager.set_verification(
                        target_slot,
                        result,
                        immediate=True,
                    )

                    self.state_manager.system_state = (
                        SystemState.FAULT
                    )

                    return False

                if (
                    time.monotonic() - start
                ) * 1000 > FEEDBACK_TIMEOUT_MS:

                    logger.critical(
                        "Slot %s deactivate timeout.",
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

                time.sleep(0.05)

    # ============================================================
    # MONITOR
    # ============================================================

    def _local_monitor_loop(self):

        while self._running:

            try:

                if (
                    self.state_manager.system_state
                    == SystemState.READY
                ):

                    for slot, state_obj in (
                        self.state_manager.slots.items()
                    ):

                        if not self._is_feedback_enabled(
                            slot
                        ):
                            continue

                        expected = (
                            state_obj.commanded_state
                        )

                        if expected not in (
                            CommandedState.ON,
                            CommandedState.OFF,
                        ):
                            continue

                        result = self.verify_slot(
                            slot,
                            expected,
                        )

                        if (
                            expected
                            == CommandedState.ON
                            and result
                            == VerificationState.MISMATCH_ON_OFF
                        ):

                            logger.critical(
                                "Slot %s unexpectedly OFF.",
                                slot,
                            )

                            self.state_manager.set_verification(
                                slot,
                                result,
                                immediate=True,
                            )

                            self.state_manager.system_state = (
                                SystemState.FAULT
                            )

                        elif (
                            expected
                            == CommandedState.OFF
                            and result
                            == VerificationState.MISMATCH_OFF_ON
                        ):

                            logger.critical(
                                "DANGER: Slot %s unexpectedly ON.",
                                slot,
                            )

                            self.state_manager.set_verification(
                                slot,
                                result,
                                immediate=True,
                            )

                            self.state_manager.system_state = (
                                SystemState.FAULT
                            )

            except Exception as exc:

                logger.critical(
                    "GPIO monitor failure: %s",
                    exc,
                )

                self.state_manager.system_state = (
                    SystemState.FAULT
                )

            time.sleep(
                LOCAL_MONITOR_INTERVAL_S
            )

    # ============================================================
    # STOP
    # ============================================================

    def stop(self):

        self._running = False

        if (
            self.monitor_thread
            and self.monitor_thread.is_alive()
        ):
            self.monitor_thread.join(
                timeout=3
            )