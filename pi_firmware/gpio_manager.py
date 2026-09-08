import collections
import threading
import time

from config import (
    get_hardware_profile,
    FEEDBACK_TIMEOUT_MS,
    FEEDBACK_DEBOUNCE_MS,
    INTERLOCK_DELAY_MS,
    LOCAL_MONITOR_INTERVAL_S,
    TOGGLE_DEBOUNCE_S,
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
        """Mock with gpiozero semantics: `is_active` is logical state, polarity explicit."""

        def __init__(self, pin, active_high=True, initial_value=False):
            self.pin = pin
            self.active_high = active_high
            self.is_active = bool(initial_value)

        def on(self):
            self.is_active = True

        def off(self):
            self.is_active = False

        def close(self):
            pass

        @property
        def pin_level_high(self):
            return self.is_active if self.active_high else not self.is_active

    class Button:
        """Mock: `is_pressed` is a property (as in gpiozero) meaning *logically active*
        (LOW with pull_up=True, HIGH with pull_up=False). `_set(active)` / `_set_level(high)`
        drive it and fire callbacks on change."""

        def __init__(self, pin, pull_up=False, bounce_time=None):
            self.pin = pin
            self.pull_up = pull_up
            self.bounce_time = bounce_time
            self._state = False
            self.when_pressed = None
            self.when_released = None

        @property
        def is_pressed(self):
            return self._state

        @property
        def level_high(self):
            return (not self._state) if self.pull_up else self._state

        def _set(self, pressed):
            pressed = bool(pressed)
            changed = pressed != self._state
            self._state = pressed
            if changed:
                cb = self.when_pressed if pressed else self.when_released
                if cb:
                    cb()

        def _set_level(self, high):
            self._set((not high) if self.pull_up else bool(high))

        def close(self):
            pass


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
        self.toggles = {}
        self.hardware_fault = None

        self._lock = threading.RLock()
        self._running = True

        self._toggle_events = collections.deque(maxlen=64)
        self._toggle_lock = threading.Lock()
        self._toggle_last = {}

        relay_active_low = bool(self.profile["relay_active_low"])
        self._toggle_active_low = bool(self.profile["toggle_active_low"])
        self._detect_active_low = bool(self.profile["detect_active_low"])

        try:
            for slot, ch in self.profile["channels"].items():

                # Relays are ACTIVE-LOW on this hardware: explicit polarity, always start OFF.
                self.relays[slot] = OutputDevice(
                    ch["relay_gpio"],
                    active_high=not relay_active_low,
                    initial_value=False,
                )

                # pull_up selects the idle level: True -> active LOW, False -> pull-down, active HIGH.
                self.feedback_inputs[slot] = Button(
                    ch["detect_gpio"],
                    pull_up=self._detect_active_low,
                    bounce_time=FEEDBACK_DEBOUNCE_MS / 1000.0,
                )

                toggle = Button(
                    ch["toggle_gpio"],
                    pull_up=self._toggle_active_low,
                    bounce_time=TOGGLE_DEBOUNCE_S,
                )
                # Level present at init is reported, never treated as an edge.
                self._toggle_last[slot] = bool(toggle.is_pressed)
                toggle.when_pressed = self._toggle_callback(slot, ch["toggle_gpio"], on=True)
                toggle.when_released = self._toggle_callback(slot, ch["toggle_gpio"], on=False)
                self.toggles[slot] = toggle
        except Exception as exc:
            # Fail SAFE, not fast: no relay was ever driven ON (initial_value=False) and the
            # controller stays alive in FAULT to report the fault; systemd must not crash-loop.
            self.hardware_fault = f"GPIO_INIT_FAILED: {type(exc).__name__}: {exc}"
            logger.critical("%s -- controller will run in FAULT with no GPIO.", self.hardware_fault)
            self._release_devices()
            self.monitor_thread = None
            return

        self.monitor_thread = threading.Thread(
            target=self._local_monitor_loop,
            name="EMS-GPIO-Monitor",
            daemon=True,
        )

        self.monitor_thread.start()

    def _release_devices(self):
        for dev in (*self.relays.values(), *self.feedback_inputs.values(), *self.toggles.values()):
            try:
                dev.close()
            except Exception:
                pass
        self.relays.clear()
        self.feedback_inputs.clear()
        self.toggles.clear()

    # ============================================================
    # TOGGLE INPUTS (events only: never drive relays from here)
    # ============================================================

    def _toggle_callback(self, slot, gpio, on):
        def _cb():
            with self._toggle_lock:
                old = self._toggle_last.get(slot)
                self._toggle_last[slot] = on
                self._toggle_events.append(
                    {
                        "slot": slot,
                        "gpio": gpio,
                        "old": old,
                        "on": on,
                        "ts": time.time(),
                        "monotonic": time.monotonic(),
                    }
                )

        return _cb

    def pop_toggle_events(self):
        with self._toggle_lock:
            events = list(self._toggle_events)
            self._toggle_events.clear()
        return events

    def toggle_inputs(self) -> dict:
        """{slot: True/False, or None when the GPIO is unavailable}. Telemetry only."""
        out = {}
        for slot in self.profile["slots"]:
            btn = self.toggles.get(slot)
            out[slot] = bool(btn.is_pressed) if btn is not None else None
        return out

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

        # gpiozero `is_pressed` is a property (pin LOW with pull-up). Two reads FEEDBACK_DEBOUNCE_MS
        # apart must agree so a bouncing contact never counts as a stable ON.
        first = bool(
            btn.is_pressed
        )

        time.sleep(
            FEEDBACK_DEBOUNCE_MS / 1000.0
        )

        second = bool(
            btn.is_pressed
        )

        pressed = first and second

        # `is_pressed` already means logically active for the configured pull (see config).
        return pressed

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

        if self.hardware_fault:
            logger.critical("Reconciliation refused: %s", self.hardware_fault)
            self.state_manager.system_state = SystemState.FAULT
            self.state_manager.save_state(immediate=True)
            return False

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

                # Hold the transition lock: never sample feedback mid-transition (contactor in flight).
                with self._lock:
                    # Safety monitoring must run offline too: only FAULT/boot phases pause it.
                    if self.state_manager.system_state in (
                        SystemState.READY,
                        SystemState.CLOUD_OFFLINE,
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