"""T7 canonical device configuration + hash (FROZEN SCHEMA v1).

The backend (backend/main.py) carries a byte-identical copy of this module's
logic. Any change here is a protocol change and must be mirrored + re-tested
(test_reports/t7_firmware_test.py checks parity against the backend copy).

Canonical form (all four slots always present, explicit defaults):

    {
      "feedback_hardware_installed": bool,
      "hardware_profile": str,
      "reset_day": int,
      "slots": {
        "A": {"disabled": bool, "display_name": str, "feedback_enabled": bool, "target_days": int},
        "B": {...}, "C": {...}, "D": {...}
      }
    }

Normalisation rules:
  * slots missing from the input -> {"display_name": "Slot X", "target_days": 0,
    "disabled": True, "feedback_enabled": False}
  * feedback_enabled is CLAMPED: feedback_enabled AND feedback_hardware_installed
    (effective configuration, never the raw request)
  * ints are ints, bools are bools, strings are str; None -> default
  * hash = sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"))
                  .encode("utf-8")).hexdigest()
"""
import hashlib
import json

CANONICAL_SLOTS = ("A", "B", "C", "D")
DEFAULT_HARDWARE_PROFILE = "EMS-4CH-v1"
DEFAULT_RESET_DAY = 15
MAX_DISPLAY_NAME = 64
MAX_TARGET_DAYS = 366


class ConfigError(ValueError):
    """Bounded error code (never a raw exception string)."""

    CODES = (
        "INVALID_HARDWARE_PROFILE",
        "INVALID_SLOT",
        "INVALID_TARGET_DAYS",
        "INVALID_RESET_DAY",
        "INVALID_FEEDBACK_CONFIGURATION",
        "INVALID_DISPLAY_NAME",
        "CONFIG_PERSIST_FAILED",
        "CONFIG_HASH_FAILED",
        "UNKNOWN_CONFIG_ERROR",
    )

    def __init__(self, code):
        if code not in self.CODES:
            code = "UNKNOWN_CONFIG_ERROR"
        super().__init__(code)
        self.code = code


def _as_bool(value, code):
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise ConfigError(code)


def _as_int(value, code):
    if isinstance(value, bool):
        raise ConfigError(code)
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    raise ConfigError(code)


def canonical_device_config(
    hardware_profile,
    feedback_hardware_installed,
    reset_day,
    slots,
    known_profiles=None,
):
    """Validate + normalise into the frozen canonical form. Raises ConfigError."""
    profile = hardware_profile if hardware_profile not in (None, "") else DEFAULT_HARDWARE_PROFILE
    if not isinstance(profile, str) or not profile or len(profile) > MAX_DISPLAY_NAME:
        raise ConfigError("INVALID_HARDWARE_PROFILE")
    if known_profiles is not None and profile not in known_profiles:
        raise ConfigError("INVALID_HARDWARE_PROFILE")

    installed = _as_bool(feedback_hardware_installed, "INVALID_FEEDBACK_CONFIGURATION")
    installed = False if installed is None else installed

    day = _as_int(reset_day, "INVALID_RESET_DAY")
    day = DEFAULT_RESET_DAY if day is None else day
    if not 1 <= day <= 28:
        raise ConfigError("INVALID_RESET_DAY")

    slots = {} if slots is None else slots
    if not isinstance(slots, dict):
        raise ConfigError("INVALID_SLOT")
    for key in slots:
        if key not in CANONICAL_SLOTS:
            raise ConfigError("INVALID_SLOT")

    out_slots = {}
    for code in CANONICAL_SLOTS:
        raw = slots.get(code) or {}
        if not isinstance(raw, dict):
            raise ConfigError("INVALID_SLOT")

        target = _as_int(raw.get("target_days"), "INVALID_TARGET_DAYS")
        target = 0 if target is None else target
        if not 0 <= target <= MAX_TARGET_DAYS:
            raise ConfigError("INVALID_TARGET_DAYS")

        disabled = _as_bool(raw.get("disabled"), "INVALID_SLOT")
        disabled = True if disabled is None else disabled

        name = raw.get("display_name")
        name = f"Slot {code}" if name in (None, "") else name
        if not isinstance(name, str) or len(name) > MAX_DISPLAY_NAME:
            raise ConfigError("INVALID_DISPLAY_NAME")

        fb = _as_bool(raw.get("feedback_enabled"), "INVALID_FEEDBACK_CONFIGURATION")
        fb = False if fb is None else fb

        out_slots[code] = {
            "disabled": disabled,
            "display_name": name,
            "feedback_enabled": bool(fb and installed),  # capability clamp BEFORE hashing
            "target_days": target,
        }

    return {
        "feedback_hardware_installed": installed,
        "hardware_profile": profile,
        "reset_day": day,
        "slots": out_slots,
    }


def canonical_json(canonical):
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def config_hash(canonical):
    return hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
