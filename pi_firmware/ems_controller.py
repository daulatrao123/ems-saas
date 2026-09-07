import json
import os
import signal
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta

from config import (
    DEVICE_ID,
    HARDWARE_PROFILES,
    SUPPORTED_SLOTS,
    SYNC_INTERVAL_S,
    TELEMETRY_DIR,
)
from config_hash import (
    ConfigError,
    canonical_device_config,
    canonical_json,
    config_hash,
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


# Phase 0.5.1: how often the (cheap, read-only) "is today's telemetry
# recorded?" check runs. The calendar-day CSV itself is the durable identity.
TELEMETRY_CHECK_INTERVAL_S = 3600.0


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
            "reset_day": 15,
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

        # T7 applied-configuration identity (RAM mirror of execution_meta).
        self._applied_version = None
        self._applied_hash = None
        self._applied_at = None
        self._config_error = None
        self._restore_applied_config()

        self._last_sync = 0.0
        self._last_usage_day = self.state.last_usage_date
        self._last_telemetry = 0.0
        self._telemetry_done_day = None  # RAM memo only; disk CSV is the source of truth
        self._last_queue_cleanup = 0.0

        # Phase 0.5: storage-state transition events awaiting cloud delivery.
        # RAM only, bounded, deduplicated by eventId (backend ON CONFLICT DO NOTHING).
        self._pending_events = []
        self._pending_events_max = 20
        self._events_in_flight = 0

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
    # CONFIG (T7: validate -> canonical -> hash -> persist -> then applied)
    # ============================================================

    def _restore_applied_config(self):
        """Boot: restore the last durably applied configuration as DATA only.

        Safety boundary: GPIOManager() has already driven every relay output to
        its safe OFF startup state before this runs, and nothing in this path
        touches hardware. Restored data only shapes later decisions (slot
        visibility, feedback verification, reconciliation); relays are energised
        exclusively through the command path (transition_slot) after boot().

        The stored blob is re-validated through the same canonicaliser as cloud
        config (schema, ranges, known hardware profile, feedback clamp), must
        re-hash to the stored hash, and must match the profile the GPIO layer
        was initialised with. Any failure -> keep safe defaults, record ONE
        bounded error code (reported in sync, cleared by the next good apply).
        """
        stored = self.queue.get_applied_config()
        if not stored:
            return

        try:
            loaded = json.loads(stored["json"])
            if not isinstance(loaded, dict):
                raise ConfigError("UNKNOWN_CONFIG_ERROR")
            canonical = canonical_device_config(
                loaded.get("hardware_profile"),
                loaded.get("feedback_hardware_installed"),
                loaded.get("reset_day"),
                loaded.get("slots"),
                known_profiles=HARDWARE_PROFILES,
            )
            if config_hash(canonical) != stored["hash"]:
                raise ConfigError("CONFIG_HASH_FAILED")
            self._require_gpio_profile(canonical)
        except ConfigError as exc:
            code = exc.code
        except (ValueError, TypeError):
            code = "UNKNOWN_CONFIG_ERROR"
        else:
            self._install_effective_config(canonical)
            self._applied_version = stored["version"]
            self._applied_hash = stored["hash"]
            self._applied_at = stored["at"]
            return

        # Rejected: hardware stays in the safe startup state, defaults remain
        # active, cloud sync continues and can re-apply the desired revision.
        self._config_error = code
        logger.critical(
            "Stored applied config rejected at boot (%s); running safe defaults until cloud sync.",
            code,
        )

    def _require_gpio_profile(self, canonical):
        """A configuration may only be installed for the hardware profile the
        GPIO layer was initialised with (pins are bound at construction)."""
        if HARDWARE_PROFILES.get(canonical["hardware_profile"]) != self.gpio.profile:
            raise ConfigError("INVALID_HARDWARE_PROFILE")

    def _install_effective_config(self, canonical):
        # Mutate in place: GPIOManager holds a reference to this dict.
        self.device_config.clear()
        self.device_config.update(
            {
                "hardware_profile": canonical["hardware_profile"],
                "feedback_hardware_installed": canonical["feedback_hardware_installed"],
                "reset_day": canonical["reset_day"],
                "slots": {
                    slot: dict(canonical["slots"][slot])
                    for slot in SUPPORTED_SLOTS
                },
            }
        )

    def _apply_cloud_config(
        self,
        response: dict,
    ):
        """Returns True when the effective configuration is durably applied
        (or already was). Any rejection leaves the running configuration and
        the applied identity untouched and sets a bounded error code."""
        try:
            version = response.get("config_version")
            if isinstance(version, bool) or not isinstance(version, int) or version < 0:
                raise ConfigError("UNKNOWN_CONFIG_ERROR")

            canonical = canonical_device_config(
                response.get("hardware_profile"),
                response.get("feedback_hardware_installed"),
                response.get("resetDay"),
                response.get("slots"),
                known_profiles=HARDWARE_PROFILES,
            )
            self._require_gpio_profile(canonical)
            new_hash = config_hash(canonical)
        except ConfigError as exc:
            self._config_error = exc.code
            logger.error("Cloud configuration rejected: %s", exc.code)
            return False
        except Exception as exc:  # canonicaliser/hash must never take the controller down
            self._config_error = "CONFIG_HASH_FAILED"
            logger.error("Cloud configuration hashing failed: %s", exc)
            return False

        if new_hash == self._applied_hash and version == self._applied_version:
            self._config_error = None
            return True  # steady state: zero writes

        applied_at = datetime.now(timezone.utc).isoformat()
        if not self.queue.persist_applied_config(
            canonical_json(canonical), version, new_hash, applied_at
        ):
            self._config_error = "CONFIG_PERSIST_FAILED"
            return False  # not durable -> not applied -> keep running old config

        self._install_effective_config(canonical)
        self._applied_version = version
        self._applied_hash = new_hash
        self._applied_at = applied_at
        self._config_error = None
        logger.info("Configuration applied version=%s hash=%s", version, new_hash[:12])
        return True

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
                "used_days": int(slot_state.used_days),
                "clicks": int(slot_state.clicks),
            }

        resource_status = (
            self.storage.get_status()
        )

        memory = resource_status.get(
            "memory",
            {},
        )

        storage_ok = bool(
            resource_status.get(
                "storage_ok",
                False,
            )
        )

        storage_state = str(
            resource_status.get(
                "storage_state",
                "STORAGE_FAILED",
            )
        )

        # Explicit sentinel: -1.0 means statvfs failed (never a silent 0).
        disk_free_mb = (
            round(
                float(
                    resource_status.get(
                        "free_mb",
                        0.0,
                    )
                ),
                1,
            )
            if storage_ok
            else -1.0
        )

        self._collect_storage_events()

        events = list(self._pending_events)
        self._events_in_flight = len(events)

        return {
            "deviceId": DEVICE_ID,
            "firmwareVersion": "7.0.0",
            "active_slot": self.state.active_slot,
            "resetDay": int(self.device_config.get("reset_day", 15)),
            "emergencyStop": (
                self.state.system_state
                == SystemState.FAULT
            ),
            "uptimeSeconds": int(
                time.monotonic()
            ),
            "cpuTemp": 0.0,
            "diskFreeMB": disk_free_mb,
            "storageState": storage_state,
            "storageUsedPercent": round(
                float(
                    resource_status.get(
                        "used_percent",
                        100.0,
                    )
                ),
                2,
            ),
            "bootCount": 0,
            "watchdogEnabled": True,
            "clockSource": "system",
            "slots": slots,
            "memory": memory,
            "events": events,
            # T7 applied configuration identity (RAM; persisted on change only).
            "applied_config_version": self._applied_version,
            "applied_config_hash": self._applied_hash,
            "applied_config_at": self._applied_at,
            "config_apply_error": self._config_error,
            # T6 execution identity: lets the cloud reconcile instead of re-delivering.
            "last_executed_sequence": self.queue.get_last_executed_sequence(),
            "executed_command_ids": self.queue.get_executed_command_ids(),
        }

    # ============================================================
    # STORAGE EVENTS (Phase 0.5)
    # ============================================================

    def _collect_storage_events(self):
        """Turn storage-band transitions into one cloud event each.
        Staying in the same band produces zero events."""

        for tr in self.storage.pop_storage_transitions():
            self._pending_events.append(
                {
                    "eventId": str(uuid.uuid4()),
                    "timestamp": tr["timestamp"],
                    "type": "storage",
                    "message": (
                        f"STORAGE_STATE {tr['from']} -> {tr['to']} "
                        f"used={tr['used_percent']}% free={tr['free_mb']}MB"
                    ),
                }
            )

        if len(self._pending_events) > self._pending_events_max:
            self._pending_events = (
                self._pending_events[-self._pending_events_max:]
            )

    def _ack_sent_events(self):
        # Cloud accepted the snapshot: drop exactly the events it received.
        self._pending_events = (
            self._pending_events[self._events_in_flight:]
        )
        self._events_in_flight = 0

    # ============================================================
    # DAILY TELEMETRY (Phase 0.5.1)
    # ============================================================

    @staticmethod
    def _daily_telemetry_recorded(day):
        """True if daily_<day>.csv holds at least one data row after the header."""
        path = os.path.join(TELEMETRY_DIR, f"daily_{day}.csv")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                next(fh)  # header
                for line in fh:
                    if line.strip():
                        return True
        except (OSError, StopIteration):
            pass
        return False

    def _record_daily_telemetry_if_needed(self):
        """At most one telemetry row per calendar day; never retroactive.
        Failure/denial just leaves it for the next hourly opportunity."""
        day = datetime.now().strftime("%Y-%m-%d")

        if self._telemetry_done_day == day:
            return False

        if self._daily_telemetry_recorded(day):
            self._telemetry_done_day = day
            return False

        if self.storage.save_daily_telemetry():
            self._telemetry_done_day = day
            return True

        return False

    # ============================================================
    # DAILY USAGE / MONTHLY RESET
    # ============================================================

    def _slot_visible(self, slot):
        if slot not in self.device_config.get("slots", {}):
            return False
        cfg = self.device_config["slots"][slot]
        physical = self.state.slots[slot].feedback_state.value
        return (
            int(cfg.get("target_days", 0)) > 0
            and physical == "ON"
            and not bool(cfg.get("disabled", False))
        )

    def _update_daily_usage(self):
        now = datetime.now().astimezone()
        marker = now.date().isoformat()
        if self._last_usage_day == marker:
            return

        reset_day = max(1, min(28, int(self.device_config.get("reset_day", 15))))
        current_period = f"{now.year:04d}-{now.month:02d}"
        last_reset_period = self.state.last_reset_period

        if now.day >= reset_day and last_reset_period != current_period:
            legacy_has_usage = any(slot.used_days > 0 for slot in self.state.slots.values())
            legacy_period = str(self.state.last_usage_date)[:7] if self.state.last_usage_date else None
            should_reset = (
                last_reset_period is not None
                or not legacy_has_usage
                or (legacy_period is not None and legacy_period < current_period)
            )
            if should_reset:
                self.state.reset_days(immediate=False)
            self.state.set_last_reset_period(current_period, immediate=False)

        active = self.state.active_slot
        if active and self._slot_visible(active):
            self.state.increment_used_day(active, immediate=False)

        self.state.set_last_usage_date(marker, immediate=False)
        if self.state.save_state(immediate=True):
            self._last_usage_day = marker

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
            self._events_in_flight = 0
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

        self._ack_sent_events()

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
        command_id = str(response.get("command_id"))
        command = str(response.get("command", ""))
        slot = str(response.get("slot", ""))

        if slot and slot not in SUPPORTED_SLOTS:
            logger.critical("Rejected command with invalid slot: %s", slot)
            self.api.push_ack(command_id, "EXECUTING", "NOT_AVAILABLE")
            self.api.push_ack(command_id, "FAILED", "NOT_AVAILABLE", "INVALID_SLOT")
            return

        normalized = {
            "set_active_slot": "ACTIVATE",
            "off_slot": "DEACTIVATE",
            "off_all": "DEACTIVATE_ALL",
        }.get(command)

        if normalized is None:
            logger.warning("Command %s is non-hardware or unsupported on Pi: %s", command_id, command)
            if command in {"set_days", "set_reset_day", "reset_days", "lcd_display"}:
                if self.api.push_ack(command_id, "EXECUTING", "NOT_AVAILABLE"):
                    if self.api.push_ack(command_id, "COMPLETED", "NOT_AVAILABLE"):
                        self.api.push_ack(command_id, "ACKED", "NOT_AVAILABLE")
            else:
                self.api.push_ack(command_id, "EXECUTING", "NOT_AVAILABLE")
                self.api.push_ack(command_id, "FAILED", "NOT_AVAILABLE", "UNSUPPORTED_COMMAND")
            return

        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(minutes=5)).isoformat()
        cloud_expires = response.get("expires_at")
        if cloud_expires:
            # Absolute cloud expiry wins; never extend it locally.
            try:
                cloud_dt = datetime.fromisoformat(str(cloud_expires).replace("Z", "+00:00"))
                if cloud_dt.tzinfo is None:
                    cloud_dt = cloud_dt.replace(tzinfo=timezone.utc)
                if cloud_dt <= now:
                    logger.warning("Rejecting already-expired command %s", command_id)
                    self.api.push_ack(command_id, "EXPIRED", "NOT_AVAILABLE", "COMMAND_EXPIRED",
                                      attempt=response.get("attempt"))
                    return
                expires_at = min(cloud_dt, now + timedelta(minutes=5)).isoformat()
            except ValueError:
                pass
        if not self.queue.add_command(
            command_id, slot, normalized, now.isoformat(), expires_at, response.get("config_version"),
            sequence_no=response.get("sequence_no"), cloud_attempt=response.get("attempt"),
        ):
            logger.critical("Unable to durably persist cloud command %s", command_id)

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

        if not self.state.save_state(immediate=True):
            logger.critical(
                "Refusing hardware execution for %s: EXECUTING state could not be persisted.",
                command_id,
            )
            self.state.system_state = SystemState.FAULT
            return True

        success = False
        verification = (
            VerificationState.PENDING.value
        )
        error = None

        # Best-effort cloud lifecycle notification. Local durable state remains
        # authoritative when the cloud is unavailable.
        self.api.push_ack(command_id, "EXECUTING", "PENDING", attempt=self.queue.get_cloud_attempt(command_id))

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
                if slot and slot in self.state.slots:
                    self.state.increment_clicks(slot, immediate=False)

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

        for command_id, slot, action, prior_status in interrupted:
            try:
                hardware_active = self.state.active_slot

                if action == "ACTIVATE":
                    if hardware_active == slot:
                        verification = (
                            VerificationState
                            .VERIFIED_ON
                            .value
                        )

                        if prior_status != "HARDWARE_VERIFIED":
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
                        if prior_status != "HARDWARE_VERIFIED":
                            self.queue.update_status(
                                command_id,
                                "HARDWARE_VERIFIED",
                                VerificationState.VERIFIED_OFF.value,
                            )

                        self.queue.update_status(
                            command_id,
                            "COMPLETED",
                            VerificationState.VERIFIED_OFF.value,
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
                attempt = self.queue.get_cloud_attempt(command_id)
                terminal_sent = self.api.push_ack(
                    command_id,
                    status,
                    verification or "UNKNOWN",
                    error,
                    attempt=attempt,
                )
                if terminal_sent and self.api.push_ack(
                    command_id,
                    "ACKED",
                    verification or "UNKNOWN",
                    error,
                    attempt=attempt,
                ):
                    self.queue.mark_acked(command_id)
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

                self._update_daily_usage()
                self.process_one_command()

                self.flush_acks()

                if (
                    now - self._last_queue_cleanup
                    >= 3600
                ):
                    self.queue.cleanup_acked()
                    self._last_queue_cleanup = now

                if (
                    now - self._last_telemetry
                    >= TELEMETRY_CHECK_INTERVAL_S
                ):
                    self._record_daily_telemetry_if_needed()
                    self._last_telemetry = now

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