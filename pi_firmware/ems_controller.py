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

from logger import logger, attach_file_sinks, detach_file_sinks, file_sinks_attached, drain_ring
from state import (
    PiStateManager,
    SystemState,
    CommandedState,
    VerificationState,
)
from storage_manager import StorageManager
from lcd_display import LcdDisplay, message_is_live
from storage_health import (
    collect_storage_health,
    SECONDARY_HEALTHY,
    SECONDARY_UNAVAILABLE,
    SECONDARY_UNWRITABLE,
)
from offline_queue import OfflineQueue
from gpio_manager import GPIOManager
from api_client import ApiClient
import ota_manager
import config as _cfg
from smart_health import SmartHealthMonitor

# P0-1 software completion contract (mirrors backend SOFTWARE_RESULTS). A software result is
# never a claim about physical GPIO state; software commands never enter the hardware queue/FSM.
SOFTWARE_RESULT = {
    "set_days": "CONFIG_ACCEPTED",
    "set_reset_day": "CONFIG_ACCEPTED",
    "reset_days": "STATE_RESET",
    "lcd_display": "DISPLAY_UPDATED",
    "restart": "RESTART_SCHEDULED",
    "reboot": "REBOOT_SCHEDULED",
}
REBOOT_REQUEST_FILE = "reboot.request"


# Phase 0.5.1: how often the (cheap, read-only) "is today's telemetry
# recorded?" check runs. The calendar-day CSV itself is the durable identity.
TELEMETRY_CHECK_INTERVAL_S = 3600.0
# Secondary (USB data volume) re-verification cadence for the logging policy.
SECONDARY_RECHECK_S = 60.0
EXIT_SECONDARY_STORAGE = 4

# Baseline version of THIS artifact. A staged slot reports its own version via
# ota_manager.active_info(); the baseline is used when no OTA slot is active.
FIRMWARE_VERSION = "7.0.0"


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
        self._boot_rejection = None
        self._applied_version = None
        self._applied_hash = None
        self._applied_at = None
        self._config_error = None
        self._restore_applied_config()

        # Signed OTA (A/B slots managed by ota_manager; activation = process restart).
        self.firmware_version = ota_manager.running_version(FIRMWARE_VERSION)
        self._boot_confirmed = False
        self._ota_requested = None
        self._restart_for_ota = False
        self._restart_requested = False   # `restart` command: exit(3) -> systemd restarts us
        self._reboot_requested = False    # `reboot` command: marker -> root path unit reboots OS
        # Best-effort SMART/NAND diagnostics: hourly, read-only, persists only on state change.
        self.smart = SmartHealthMonitor(os.environ.get("EMS_DATA_DEVICE", "/dev/mmcblk0"),
                                        os.path.join(_cfg.HEALTH_DIR, "smart.json"), logger)
        self._ota_status = self._read_ota_status()
        # Storage health snapshot (mount -> UUID -> tiny write probe) refreshed every SECONDARY_RECHECK_S.
        # It drives the logging policy: file sinks exist only while the secondary volume is HEALTHY.
        # Deliberately NOT coupled to SystemState or command gating.
        self._storage_health = None
        self._storage_health_next = 0.0
        self._secondary_state = SECONDARY_HEALTHY  # preflight_secondary_storage() verified this before __init__
        # Website -> LCD message: cached on disk so it survives reboot / cloud outage until expiry.
        self._lcd_message_path = os.path.join(_cfg.HEALTH_DIR, "lcd_message.json")
        self.lcd_message = self._load_lcd_message()
        # LCD is display-only: it gets a read-only view dict, never a reference to gpio/queue/state.
        self.lcd = LcdDisplay(self.lcd_view, logger,
                              rotation_interval_s=float(os.environ.get("LCD_ROTATION_INTERVAL_S", "5")),
                              enabled=os.environ.get("EMS_LCD_ENABLED", "1") != "0")
        self.lcd.start()

        self._last_sync = 0.0
        self._auth_rejected_logged = False
        self._last_usage_day = self.state.last_usage_date
        self._last_telemetry = 0.0
        self._telemetry_done_day = None  # RAM memo only; disk CSV is the source of truth
        self._last_queue_cleanup = 0.0

        # Phase 0.5: storage-state transition events awaiting cloud delivery.
        # RAM only, bounded, deduplicated by eventId (backend ON CONFLICT DO NOTHING).
        self._pending_events = []
        self._pending_events_max = 20
        self._events_in_flight = 0
        self._ack_backoff_s = 0.0
        self._ack_backoff_until = 0.0
        self._ack_last_flush = -1e9

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

        if self.gpio.hardware_fault:
            # GPIO layer unavailable: no relay exists to drive, stay alive to report.
            self._emit_event("hardware_fault", self.gpio.hardware_fault)

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
        self._clear_stale_reboot_request()

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
        # T8: exactly one boot_config_rejected event per boot (metadata only, no config contents).
        self._boot_rejection = {"code": code, "stored_version": stored.get("version")}
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
        self._collect_log_events()

        events = list(self._pending_events)
        self._events_in_flight = len(events)

        return {
            "deviceId": DEVICE_ID,
            "firmwareVersion": self.firmware_version,
            # Signed OTA state (RAM cache refreshed on OTA events only).
            "otaState": self._ota_status.get("state"),
            "otaVersion": self._ota_status.get("version"),
            "otaError": self._ota_status.get("error"),
            "otaAttempts": self._ota_status.get("attempts", 0),
            "lastGoodFirmwareVersion": self._ota_status.get("last_good"),
            "flashBytesToday": int(
                resource_status.get("storage", {}).get("logical_writes", 0) or 0
            ),
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
            # Physical toggle inputs (HIGH = ON). null = GPIO unavailable. Separate from contactor
            # feedback (slots[*].physical_toggle) and from logical slot enable (cloud config).
            "toggle_input": self.gpio.toggle_inputs(),
            "hardware_fault": self.gpio.hardware_fault,
            "storage_health": self._storage_health_snapshot(),
            "lcd": {"available": self.lcd.available, "error": self.lcd.init_error,
                    "message_id": (self.lcd_message or {}).get("id") if message_is_live(self.lcd_message) else None},
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

        if self._boot_rejection:
            rej = self._boot_rejection
            self._boot_rejection = None  # emitted once; resent only until a sync succeeds
            self._pending_events.append(
                {
                    "eventId": str(uuid.uuid4()),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "type": "boot_config_rejected",
                    "message": (
                        f"BOOT_CONFIG_REJECTED code={rej['code']} "
                        f"stored_version={rej['stored_version']} firmware={self.firmware_version}"
                    ),
                }
            )

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

    def _collect_log_events(self):
        """ERROR/CRITICAL ring records -> events[] ONLY while the secondary volume cannot hold the
        file logs. Fills only the free room so log lines never evict storage/toggle events.
        While HEALTHY the ring is discarded (those lines are already in critical.log)."""
        if self._secondary_state == SECONDARY_HEALTHY:
            drain_ring()
            return
        room = self._pending_events_max - len(self._pending_events)
        for rec in drain_ring(max(room, 0)):
            self._pending_events.append(
                {
                    "eventId": str(uuid.uuid4()),
                    "timestamp": rec["ts"],
                    "type": "log",
                    "message": f"[{rec['level']}] {rec['message']}"[:300],
                }
            )
        self._events_in_flight = 0

    # ============================================================
    # SIGNED OTA (verify -> stage inactive slot -> activate -> restart -> health check)
    # ============================================================

    def _read_ota_status(self):
        pending = ota_manager.pending_info()
        active = ota_manager.active_info()
        state = pending.get("state")
        if active and active.get("boot_confirmed") is False:
            state = "HEALTH_CHECK"  # running the new slot, not yet confirmed by boot()+sync
        elif not state:
            state = "ROLLED_BACK" if active.get("rolled_back_from") else "ACTIVE"
        return {
            "state": state,
            "version": pending.get("version") or active.get("rolled_back_from") or self.firmware_version,
            "error": pending.get("error"),
            "attempts": int(pending.get("attempts", 0) or 0),
            "last_good": active.get("previous_version") or self.firmware_version,
        }

    def _consider_firmware(self, response: dict):
        desired = response.get("firmware_desired_version")
        if not isinstance(desired, str) or not desired.strip():
            return
        desired = desired.strip()
        if desired == self.firmware_version or self._restart_for_ota:
            return
        if ota_manager.exhausted(desired):
            return  # bounded: MAX_ATTEMPTS_PER_VERSION failures -> wait for a new release
        if not self._boot_confirmed:
            return  # never chain an OTA onto an unconfirmed boot
        self._ota_requested = desired

    def _run_ota_if_requested(self):
        """Runs from the main loop only when no command is executing. One attempt per call."""
        version = self._ota_requested
        if not version:
            return
        self._ota_requested = None
        if self.state.system_state != SystemState.READY:
            return
        try:
            manifest = self.api.download_firmware(version)
            if manifest is None:
                raise ota_manager.OTAVerificationError("DOWNLOAD_FAILED")
            ota_manager.stage_signed_firmware(manifest, self.firmware_version)
            if not ota_manager.activate_staged(self.firmware_version):
                raise ota_manager.OTAVerificationError("ACTIVATION_FAILED")
        except ota_manager.OTAVerificationError as exc:
            ota_manager.record_failure(version, str(exc))
            self._ota_status = self._read_ota_status()
            logger.critical("OTA %s rejected: %s (attempt %s/%s)", version, exc,
                            ota_manager.attempts_for(version), ota_manager.MAX_ATTEMPTS_PER_VERSION)
            return
        except Exception as exc:
            ota_manager.record_failure(version, "STAGING_FAILED")
            self._ota_status = self._read_ota_status()
            logger.critical("OTA %s staging failed: %s", version, exc)
            return
        self._ota_status = self._read_ota_status()
        logger.critical("OTA %s staged and activated; restarting for health check.", version)
        self._restart_for_ota = True
        self.running = False

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

        if bool(self.device_config.get("feedback_hardware_installed", False)):
            is_on = self.state.slots[slot].feedback_state.value == "ON"
        else:
            is_on = self.gpio.toggle_inputs().get(slot) is True

        return (
            int(cfg.get("target_days", 0)) > 0
            and is_on
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

        # The attempt itself schedules the next one: a failing cloud must never turn the 60 s
        # sync cadence into a 0.25 s retry storm.
        self._last_sync = time.monotonic()

        if response is None:
            if getattr(self.api, "last_sync_http", None) in (401, 403):
                # Permanent credential problem (revoked/rotated key, unassigned device). Retrying
                # faster cannot fix it: hold for AUTH_REJECT_HOLD_S, keep local operation, log once.
                self._last_sync += self.AUTH_REJECT_HOLD_S - SYNC_INTERVAL_S
                if not self._auth_rejected_logged:
                    self._auth_rejected_logged = True
                    logger.critical("Cloud rejected device credentials (HTTP %s). Re-provision this Pi "
                                    "(download a NEW ZIP and install it) — next attempt in %ds.",
                                    getattr(self.api, "last_sync_http", None), self.AUTH_REJECT_HOLD_S)
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
        if "lcd_message" in response:
            self._store_lcd_message(response.get("lcd_message"))

        # OTA health check = boot() succeeded AND cloud sync succeeded on this firmware.
        if not self._boot_confirmed:
            self._boot_confirmed = True
            if ota_manager.confirm_boot():
                self.firmware_version = ota_manager.running_version(FIRMWARE_VERSION)
                self._ota_status = self._read_ota_status()
                logger.info("OTA firmware %s confirmed healthy.", self.firmware_version)

        self._consider_firmware(response)

        self._ack_sent_events()
        self._auth_rejected_logged = False

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

    def _validate_software_command(self, command, slot, params):
        """Deterministic Pi-side validation. Returns an error code or None (= accepted)."""
        params = params if isinstance(params, dict) else {}
        if command == "set_days":
            if slot not in SUPPORTED_SLOTS:
                return "INVALID_SLOT"
            try:
                days = int(params.get("days"))
            except (TypeError, ValueError):
                return "INVALID_TARGET_DAYS"
            return None if 1 <= days <= 31 else "INVALID_TARGET_DAYS"
        if command == "set_reset_day":
            try:
                day = int(params.get("day"))
            except (TypeError, ValueError):
                return "INVALID_RESET_DAY"
            return None if 1 <= day <= 28 else "INVALID_RESET_DAY"
        if command == "lcd_display":
            # This firmware build has no display driver: never claim DISPLAY_UPDATED.
            return "DISPLAY_UNAVAILABLE"
        if command in ("reset_days", "restart", "reboot"):
            return None
        return "UNSUPPORTED_COMMAND"

    def _run_software_command(self, command_id, command, slot, params, attempt):
        if not self.api.push_ack(command_id, "EXECUTING", "PENDING", attempt=attempt):
            return  # nothing mutated; the cloud re-delivers or expires the command
        error = self._validate_software_command(command, slot, params)
        if error is None and command == "reset_days":
            # Real Pi-side mutation, persisted immediately (one state write). Re-delivery within the
            # 5-minute command expiry re-zeroes counters that are already zero -> harmless.
            self.state.reset_days(immediate=False)
            if not self.state.save_state(immediate=True):
                error = "CONFIG_PERSIST_FAILED"
        elif error is None and command in ("restart", "reboot"):
            error = self._restart_precondition_error()
        if error:
            self.api.push_ack(command_id, "FAILED", "NOT_AVAILABLE", error, attempt=attempt)
            return
        result = SOFTWARE_RESULT[command]
        if self.api.push_ack(command_id, "COMPLETED", result, attempt=attempt) and \
                self.api.push_ack(command_id, "ACKED", result, attempt=attempt):
            # Flag only once the cloud holds the terminal state: it can never re-deliver this
            # command, so a restart/reboot can never loop.
            if command == "restart":
                self._restart_requested = True
            elif command == "reboot":
                self._reboot_requested = True

    def _restart_precondition_error(self):
        """RESTART = controller process exit(3) -> systemd Restart -> ota_boot.sh (existing OTA path).
        REBOOT  = marker file consumed by the root-owned ems-reboot.path unit (fixed `systemctl reboot`).
        Both are refused while an OTA boot is unconfirmed (a restart there would be read as a
        failed health check and trigger rollback), and both wait for the hardware queue to be
        fully ACKed before acting (same rule as OTA activation)."""
        info = ota_manager.active_info()
        if info and info.get("boot_confirmed") is False:
            return "OTA_HEALTH_CHECK_IN_PROGRESS"
        return None

    def _reboot_request_path(self):
        return os.path.join(_cfg.DATA_DIR, REBOOT_REQUEST_FILE)

    def _clear_stale_reboot_request(self):
        """A marker that survived a boot must never trigger a second reboot."""
        try:
            os.unlink(self._reboot_request_path())
            logger.warning("Removed stale reboot request marker at boot.")
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.critical("Could not remove stale reboot marker: %s", exc)

    def write_reboot_request(self):
        """Durable, content-free marker (one small write); the root path unit performs the reboot."""
        path = self._reboot_request_path()
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
            try:
                os.write(fd, b"reboot\n")
                os.fsync(fd)
            finally:
                os.close(fd)
            return True
        except OSError as exc:
            logger.critical("Reboot request could not be persisted: %s", exc)
            return False

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

        if command in SOFTWARE_RESULT:
            # Software operations never touch GPIO and never enter the hardware queue/FSM: validate,
            # apply the (idempotent, bounded-by-command-expiry) Pi-side effect if any, then report the
            # explicit software result. Config commits happen cloud-side on COMPLETED (T7 converges).
            self._run_software_command(command_id, command, slot, response.get("params") or {},
                                       response.get("attempt"))
            return

        normalized = {
            "set_active_slot": "ACTIVATE",
            "off_slot": "DEACTIVATE",
            "off_all": "DEACTIVATE_ALL",
        }.get(command)

        if normalized is None:
            logger.warning("Command %s is unsupported on Pi: %s", command_id, command)
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
    # LCD (display-only view) + STORAGE HEALTH (read-only diagnostics)
    # ============================================================

    def lcd_view(self):
        """Plain-data view for the LCD. Facts only; the LCD cannot act on anything."""
        toggles = self.gpio.toggle_inputs()
        slots = {}
        for slot in SUPPORTED_SLOTS:
            cfg = self.device_config.get("slots", {}).get(slot, {})
            st = self.state.slots.get(slot)
            fb = st.feedback_state.value if st else "UNKNOWN"
            slots[slot] = {
                "disabled": bool(cfg.get("disabled", True)),
                "used_days": int(st.used_days) if st else None,
                "target_days": cfg.get("target_days"),
                "contactor": fb if fb in ("ON", "OFF") else "UNKNOWN",
                "toggle": toggles.get(slot),
            }
        return {
            "system_state": self.state.system_state.value,
            "active_slot": self.state.active_slot,
            "hardware_fault": self.gpio.hardware_fault,
            "slots": slots,
            "message": self.lcd_message if message_is_live(self.lcd_message) else None,
        }

    def _load_lcd_message(self):
        try:
            with open(self._lcd_message_path) as fh:
                msg = json.load(fh)
            return msg if isinstance(msg, dict) and message_is_live(msg) else None
        except (OSError, ValueError):
            return None

    def _store_lcd_message(self, msg):
        if msg is not None and not (isinstance(msg, dict) and isinstance(msg.get("message"), str)
                                    and 0 < len(msg["message"].strip()) <= 80):
            logger.warning("Ignoring invalid LCD message from cloud.")
            return
        self.lcd_message = msg
        if self._secondary_state != SECONDARY_HEALTHY or not self.storage.is_write_allowed("diagnostics"):
            return  # RAM only: the cache must never become a primary-disk fallback
        try:
            tmp = self._lcd_message_path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(msg, fh)
            os.replace(tmp, self._lcd_message_path)
        except OSError as exc:
            logger.warning("LCD message cache not persisted: %s", exc)

    def _storage_health_snapshot(self):
        return self._refresh_storage_health()

    def _refresh_storage_health(self, force=False):
        """Re-verify the secondary volume (~every SECONDARY_RECHECK_S) and apply the logging policy."""
        now = time.monotonic()
        if force or self._storage_health is None or now >= self._storage_health_next:
            self._storage_health_next = now + SECONDARY_RECHECK_S
            try:
                self._storage_health = collect_storage_health(
                    _cfg.DATA_DIR, os.environ.get("EMS_DATA_DEVICE"), self.smart.last_result)
            except Exception as exc:
                self._storage_health = {"health": "FAILED", "error": f"{type(exc).__name__}: {exc}", "smart": "UNAVAILABLE",
                                        "smart_available": False, "secondary_state": SECONDARY_UNAVAILABLE, "secondary_usable": False}
            self._apply_secondary_policy(self._storage_health)
        return self._storage_health

    def _apply_secondary_policy(self, health):
        """HEALTHY -> file sinks attached; anything else -> detached (stdout + RAM ring only).
        One event per transition. Never touches SystemState, relays or command gating."""
        state = health.get("secondary_state", SECONDARY_UNAVAILABLE)
        if state == SECONDARY_HEALTHY and not file_sinks_attached() and not attach_file_sinks():
            state = SECONDARY_UNWRITABLE
            health.update({"secondary_state": state, "secondary_usable": False,
                           "error": health.get("error") or "log file sinks could not be opened"})
        if state != SECONDARY_HEALTHY and file_sinks_attached():
            detach_file_sinks()
        if state != self._secondary_state:
            previous, self._secondary_state = self._secondary_state, state
            message = (f"SECONDARY_STORAGE {previous} -> {state} "
                       f"file_sinks={'ATTACHED' if file_sinks_attached() else 'DETACHED'} error={health.get('error')}")
            self._emit_event("secondary_storage", message)
            logger.warning(message)  # the event carries it to the cloud; WARNING avoids a duplicate ring/log entry

    # ============================================================
    # PHYSICAL TOGGLES (local requests through the same gate as cloud commands)
    # ============================================================

    def _emit_event(self, event_type, message):
        self._pending_events.append(
            {
                "eventId": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": event_type,
                "message": message,
            }
        )
        if len(self._pending_events) > self._pending_events_max:
            self._pending_events = self._pending_events[-self._pending_events_max:]

    def process_toggle_events(self):
        """Drain debounced toggle edges (never levels). ON -> ACTIVATE slot (break-before-make in
        GPIOManager); OFF -> DEACTIVATE only if that slot is active (otherwise silent no-op).
        Same gate as cloud commands: FAULT / non-READY / logically disabled slot -> toggle_rejected.
        Returns number of hardware actions."""
        actions = 0
        for ev in self.gpio.pop_toggle_events():
            slot, on = ev["slot"], bool(ev["on"])
            if slot not in self.state.slots:
                continue

            def rejected(reason):
                logger.warning("Toggle %s GPIO%s %s rejected: %s", slot, ev.get("gpio"), "ON" if on else "OFF", reason)
                self._emit_event("toggle_rejected", self._toggle_message(ev, "REJECTED", reason=reason))

            if self.state.system_state == SystemState.FAULT:
                rejected("FAULT")
                continue

            if bool(self.device_config.get("slots", {}).get(slot, {}).get("disabled", True)):
                rejected("SLOT_DISABLED")
                continue

            if not on and self.state.active_slot != slot:
                continue

            if self.state.system_state not in (SystemState.READY, SystemState.CLOUD_OFFLINE):
                rejected(f"SYSTEM_{self.state.system_state.value}")
                continue

            prior_state = self.state.system_state
            self.state.system_state = SystemState.EXECUTING
            if not self.state.save_state(immediate=True):
                self.state.system_state = SystemState.FAULT
                rejected("STATE_PERSIST_FAILED")
                continue

            success = False
            try:
                success = self.gpio.transition_slot(slot) if on else self.gpio.deactivate_slot(slot)
                if success:
                    self.state.increment_clicks(slot, immediate=False)
            except Exception as exc:
                logger.critical("Toggle execution exception: %s", exc)
                self.state.system_state = SystemState.FAULT
            finally:
                if self.state.system_state != SystemState.FAULT:
                    self.state.system_state = prior_state
                self.state.save_state(immediate=True)

            self._emit_event("toggle", self._toggle_message(ev, "OK" if success else "FAILED"))
            actions += 1
        return actions

    def _toggle_message(self, ev, result, reason=None):
        slot = ev["slot"]
        fmt = lambda v: "UNKNOWN" if v is None else ("ON" if v else "OFF")  # noqa: E731
        edge_ts = datetime.fromtimestamp(float(ev.get("ts", time.time())), timezone.utc).isoformat()
        parts = [
            f"TOGGLE {slot}", f"gpio={ev.get('gpio')}", f"edge={fmt(ev.get('old'))}->{fmt(ev.get('on'))}",
            f"edge_ts={edge_ts}", f"result={result}",
        ]
        if reason:
            parts.append(f"reason={reason}")
        parts += [
            f"system={self.state.system_state.value}", f"active={self.state.active_slot or 'NONE'}",
            f"verification={self.state.slots[slot].verification_state.value}",
        ]
        return " ".join(parts)

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
    # ACK (bounded: 409 = never retried, 429/network = exponential backoff)
    # ============================================================

    AUTH_REJECT_HOLD_S = 300.0
    ACK_BACKOFF_MIN_S = 2.0
    ACK_BACKOFF_MAX_S = 300.0
    # Pacing even when the cloud is healthy: one queued command (<= 2 POSTs) per pass, one pass per
    # ACK_FLUSH_INTERVAL_S -> <= 80 req/min, under the backend's 120/min ACK limit with sync headroom.
    ACK_FLUSH_INTERVAL_S = 1.5
    ACK_ROWS_PER_FLUSH = 1

    def _ack_defer(self, retry_after=None):
        self._ack_backoff_s = min(
            self.ACK_BACKOFF_MAX_S,
            max(self.ACK_BACKOFF_MIN_S, self._ack_backoff_s * 2 if self._ack_backoff_s else self.ACK_BACKOFF_MIN_S),
        )
        wait = self._ack_backoff_s
        if retry_after:
            wait = min(self.ACK_BACKOFF_MAX_S, max(wait, float(retry_after)))
        self._ack_backoff_until = time.monotonic() + wait
        logger.warning("ACK delivery deferred %.0fs (HTTP %s).", wait, self.api.last_ack_http)

    def flush_acks(self):
        now = time.monotonic()
        if now < self._ack_backoff_until or now - self._ack_last_flush < self.ACK_FLUSH_INTERVAL_S:
            return
        self._ack_last_flush = now

        for command_id, status, verification, error in self.queue.get_unacked()[: self.ACK_ROWS_PER_FLUSH]:
            try:
                attempt = self.queue.get_cloud_attempt(command_id)
                self.queue.count_ack_attempt(command_id)
                ok = self.api.push_ack(command_id, status, verification or "UNKNOWN", error, attempt=attempt)
                if ok:
                    ok = self.api.push_ack(command_id, "ACKED", verification or "UNKNOWN", error, attempt=attempt)
                if ok:
                    self.queue.mark_acked(command_id)
                    self._ack_backoff_s = 0.0
                    continue

                if self.api.last_ack_http == 409:
                    # Genuine conflict: the cloud will never accept this ACK. Retrying is pure load.
                    code = self.api.last_ack_code or "CONFLICT"
                    logger.critical("ACK %s rejected by cloud (%s); not retrying.", command_id, code)
                    if not self.queue.mark_ack_rejected(command_id, code):
                        # Could not persist REJECTED (e.g. read-only storage): still never hammer the cloud.
                        self._ack_defer()
                        return
                    self._emit_event("ack_rejected", f"command={command_id} status={status} code={code}")
                    continue

                # 429 / 5xx / network: back off everything, resume later.
                self._ack_defer(self.api.last_retry_after)
                return
            except Exception as exc:
                logger.warning("ACK retry failed: %s", exc)
                self._ack_defer()
                return

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
                self.process_toggle_events()
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

                if self._ota_requested and not self.queue.get_unacked():
                    self._run_ota_if_requested()

                self.smart.maybe_collect()
                self._refresh_storage_health()

                if (self._restart_requested or self._reboot_requested) and not self.queue.get_unacked():
                    # Command is terminal AND acknowledged by the cloud -> no re-delivery, no loop.
                    logger.critical("%s requested by cloud command; stopping controller.",
                                    "Reboot" if self._reboot_requested else "Restart")
                    self.running = False

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
            self.lcd.stop()
        except Exception:
            pass

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


def preflight_secondary_storage():
    """Boot gate, runs BEFORE EMSController() (i.e. before GPIO, storage, queue, state):
    mount -> UUID -> write probe. HEALTHY: create the data tree and attach the file sinks.
    Anything else: one critical line on stdout/journald and the caller refuses to start.
    No directory is ever created on the primary OS disk."""
    health = collect_storage_health(_cfg.DATA_DIR, os.environ.get("EMS_DATA_DEVICE"))
    if health.get("secondary_state") == SECONDARY_HEALTHY:
        _cfg.ensure_data_dirs()
        if attach_file_sinks():
            return health
        health.update({"secondary_state": SECONDARY_UNWRITABLE, "secondary_usable": False,
                       "error": health.get("error") or "log file sinks could not be opened"})
    logger.critical(
        "%s at %s (device=%s uuid=%s expected=%s): %s. Refusing to start the EMS controller; "
        "the primary OS disk is never used as a fallback.",
        health.get("secondary_state"), _cfg.DATA_DIR, health.get("device"), health.get("uuid"),
        health.get("expected_uuid"), health.get("error"),
    )
    return health


def main():
    if not preflight_secondary_storage().get("secondary_usable"):
        raise SystemExit(EXIT_SECONDARY_STORAGE)
    controller = EMSController()
    controller.run()
    if controller._reboot_requested:
        # Marker is consumed by ems-reboot.path (root) which runs a fixed `systemctl reboot`.
        # We still exit(3) so the controller is back under systemd if the reboot never happens.
        controller.write_reboot_request()
        raise SystemExit(3)
    if controller._restart_for_ota or controller._restart_requested:
        # Non-zero exit -> systemd restarts the service -> ota_boot.sh execs the
        # newly activated slot; an unconfirmed boot there is rolled back automatically.
        raise SystemExit(3)


if __name__ == "__main__":
    main()