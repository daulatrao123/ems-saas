"""Versioned device hardware capabilities. Not the GPIO profile.

`hardware_profile` remains the immutable firmware GPIO map name (EMS-4CH-v1).
This document only records what optional hardware is installed and enabled.
It never carries GPIO numbers and never selects an allocation mode.

capability_version 1. Unknown keys are ignored and are not applied.
A future firmware may accept a higher version; this release rejects it
instead of guessing. LCD installed=null is the legacy "not yet commissioned"
value: the controller keeps its current display behavior.
"""
import hashlib
import json

WINGS = ("A", "B", "C", "D")
CAPABILITY_VERSION = 1
METER_BY_WING = {"A": "M2", "B": "M3", "C": "M4", "D": "M5"}
WING_BY_METER = {"M1": None, "M2": "A", "M3": "B", "M4": "C", "M5": "D"}


class CapabilityError(ValueError):
    CODES = (
        "INVALID_CAPABILITY_VERSION",
        "INVALID_CAPABILITY",
        "CAPABILITY_NOT_INSTALLED",
        "CONFIRMATION_REQUIRED",
    )

    def __init__(self, code):
        if code not in self.CODES:
            code = "INVALID_CAPABILITY"
        super().__init__(code)
        self.code = code


def _bool(value, code="INVALID_CAPABILITY"):
    if isinstance(value, bool):
        return value
    raise CapabilityError(code)


def _component(raw, *, allow_enabled_when_missing=False):
    if not isinstance(raw, dict):
        raise CapabilityError("INVALID_CAPABILITY")
    if "installed" not in raw or raw["installed"] is None:
        return None  # caller decides whether unspecified is legal
    installed = _bool(raw["installed"])
    if "enabled" not in raw or raw["enabled"] is None:
        enabled = False
    else:
        enabled = _bool(raw["enabled"])
    if enabled and not installed:
        raise CapabilityError("CAPABILITY_NOT_INSTALLED")
    if not installed:
        enabled = False
    return {"installed": installed, "enabled": enabled}


def canonical_capabilities(raw, *, require_explicit_lcd=False):
    """Return the version-1 document. Raises CapabilityError. Does not copy unknown keys."""
    if not isinstance(raw, dict):
        raise CapabilityError("INVALID_CAPABILITY")
    version = raw.get("capability_version", raw.get("hardware_profile_version"))
    if isinstance(version, bool) or not isinstance(version, int) or version != CAPABILITY_VERSION:
        raise CapabilityError("INVALID_CAPABILITY_VERSION")

    feedback = raw.get("contactor_feedback")
    if not isinstance(feedback, dict) or "installed" not in feedback:
        raise CapabilityError("INVALID_CAPABILITY")
    installed = _bool(feedback["installed"])
    channels_in = feedback.get("channels") if isinstance(feedback.get("channels"), dict) else {}
    channels = {}
    for wing in WINGS:
        entry = channels_in.get(wing, {})
        if isinstance(entry, bool):
            enabled = entry
        elif isinstance(entry, dict):
            enabled = False if entry.get("enabled") is None else _bool(entry.get("enabled"))
        elif entry is None:
            enabled = False
        else:
            raise CapabilityError("INVALID_CAPABILITY")
        if enabled and not installed:
            raise CapabilityError("CAPABILITY_NOT_INSTALLED")
        channels[wing] = {"enabled": bool(enabled and installed)}

    generation = _component(raw.get("generation_meter") or {"installed": False, "enabled": False})
    if generation is None:
        raise CapabilityError("INVALID_CAPABILITY")
    consumption_in = raw.get("consumption_meters") if isinstance(raw.get("consumption_meters"), dict) else {}
    consumption = {}
    for wing in WINGS:
        item = consumption_in.get(wing)
        if item is None:
            item = {"installed": False, "enabled": False}
        parsed = _component(item)
        if parsed is None:
            raise CapabilityError("INVALID_CAPABILITY")
        consumption[wing] = parsed

    lcd_raw = raw.get("lcd") if isinstance(raw.get("lcd"), dict) else {}
    if lcd_raw.get("installed") is None:
        if require_explicit_lcd:
            raise CapabilityError("INVALID_CAPABILITY")
        lcd = {"installed": None, "enabled": False}
    else:
        lcd = _component(lcd_raw)
        if lcd is None:
            raise CapabilityError("INVALID_CAPABILITY")

    return {
        "capability_version": CAPABILITY_VERSION,
        "contactor_feedback": {"installed": installed, "channels": channels},
        "generation_meter": generation,
        "consumption_meters": consumption,
        "lcd": lcd,
    }


def capability_json(doc):
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def capability_hash(doc):
    return hashlib.sha256(capability_json(doc).encode("utf-8")).hexdigest()


def default_capabilities():
    """Safe legacy default: nothing is claimed installed. LCD is unspecified."""
    return canonical_capabilities({
        "capability_version": CAPABILITY_VERSION,
        "contactor_feedback": {"installed": False, "channels": {w: {"enabled": False} for w in WINGS}},
        "generation_meter": {"installed": False, "enabled": False},
        "consumption_meters": {w: {"installed": False, "enabled": False} for w in WINGS},
        "lcd": {"installed": None, "enabled": False},
    })


def meter_component(doc, meter_id):
    if meter_id == "M1":
        return doc["generation_meter"]
    wing = WING_BY_METER.get(meter_id)
    if wing is None:
        raise CapabilityError("INVALID_CAPABILITY")
    return doc["consumption_meters"][wing]


def commit_capabilities(stored, incoming):
    """Validate incoming. On failure the stored document is unchanged.

    Returns (document, hash, changed, error_code).
    """
    if incoming is None:
        if stored is None:
            return None, None, False, None
        try:
            current = canonical_capabilities(stored)
        except CapabilityError:
            return None, None, False, "INVALID_CAPABILITY"
        return current, capability_hash(current), False, None
    try:
        doc = canonical_capabilities(incoming)
    except CapabilityError as exc:
        return stored, None, False, exc.code
    new_hash = capability_hash(doc)
    if isinstance(stored, dict):
        try:
            if capability_hash(canonical_capabilities(stored)) == new_hash:
                return canonical_capabilities(stored), new_hash, False, None
        except CapabilityError:
            pass
    return doc, new_hash, True, None


def clamp_feedback(canonical_installed, slot_enabled, capabilities):
    """Verification follows the stricter of canonical config and capabilities.

    Capabilities can turn verification off. They cannot turn it on when the
    canonical feedback flag is false, and they never change relay outputs.
    A missing document is legacy and keeps the canonical flag. A document
    that cannot be read does not revive that flag.
    """
    enabled = {wing: bool((slot_enabled or {}).get(wing)) for wing in WINGS}
    if not isinstance(capabilities, dict):
        return bool(canonical_installed), enabled
    try:
        doc = canonical_capabilities(capabilities)
    except CapabilityError:
        return False, {wing: False for wing in WINGS}
    installed = bool(canonical_installed) and doc["contactor_feedback"]["installed"]
    if not installed:
        return False, {wing: False for wing in WINGS}
    return True, {
        wing: enabled[wing] and doc["contactor_feedback"]["channels"][wing]["enabled"]
        for wing in WINGS
    }


def classify_feedback(installed, hardware_fault):
    if not installed:
        return {"expected": "NOT_INSTALLED", "runtime": "NOT_APPLICABLE"}
    if hardware_fault:
        return {"expected": "INSTALLED", "runtime": "FAULT"}
    return {"expected": "INSTALLED", "runtime": "HEALTHY"}


def classify_meter(installed, enabled, comm_status):
    if not installed:
        return {"expected": "NOT_INSTALLED", "runtime": "NOT_APPLICABLE"}
    if not enabled:
        return {"expected": "INSTALLED_DISABLED", "runtime": "NOT_APPLICABLE"}
    status = str(comm_status or "UNKNOWN").upper()
    if status == "ONLINE":
        runtime = "HEALTHY"
    elif status == "OFFLINE":
        runtime = "OFFLINE"
    elif status in ("FAULT", "ERROR"):
        runtime = "FAULT"
    else:
        runtime = "UNKNOWN"
    return {"expected": "INSTALLED_ENABLED", "runtime": runtime}


def classify_lcd(installed, available):
    if installed is None:
        return {"expected": "UNSPECIFIED", "runtime": "NOT_APPLICABLE"}
    if not installed:
        return {"expected": "NOT_INSTALLED", "runtime": "NOT_APPLICABLE"}
    if available is True:
        return {"expected": "INSTALLED", "runtime": "HEALTHY"}
    if available is False:
        return {"expected": "INSTALLED", "runtime": "FAULT"}
    return {"expected": "INSTALLED", "runtime": "UNKNOWN"}


def recover_stored_capabilities(raw_text, stored_hash):
    """Load a persisted document. Corruption does not invent installed hardware."""
    try:
        loaded = json.loads(raw_text) if not isinstance(raw_text, dict) else raw_text
        doc = canonical_capabilities(loaded)
        digest = capability_hash(doc)
        if stored_hash and stored_hash != digest:
            raise CapabilityError("INVALID_CAPABILITY")
        return doc, digest, None
    except (CapabilityError, ValueError, TypeError, json.JSONDecodeError):
        return None, None, "CAPABILITY_STATE_INVALID"


def lcd_runtime_enabled(installed, env_enabled, current_enabled):
    """null keeps the current display. false stops writes. true follows the env gate."""
    if installed is False:
        return False
    if installed is True:
        return bool(env_enabled)
    return bool(current_enabled)


def bundle_needs_write(applied_config_hash, applied_version, new_config_hash, new_version, applied_cap_hash, new_cap_hash):
    """True when either the frozen config identity or the capability hash changed."""
    config_same = applied_config_hash == new_config_hash and applied_version == new_version
    return not (config_same and applied_cap_hash == new_cap_hash)
