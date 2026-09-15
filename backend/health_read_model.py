"""Bounded Pi health snapshot; no DB calls, accumulation or invented legacy data."""
import math
from datetime import datetime, timezone


def _number(value, lower, upper, integer=False):
    if type(value) not in (int, float) or (integer and type(value) is not int):
        return None
    try:
        return value if math.isfinite(value) and lower <= value <= upper else None
    except (OverflowError, TypeError):
        return None


def _time(value, now):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or (parsed - now).total_seconds() > 30:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def normalize_report(raw, now):
    raw = raw if isinstance(raw, dict) and type(raw.get("version")) is int and raw["version"] == 1 else {}
    sampled_at = _time(raw.get("sampled_at"), now)
    cpu = raw.get("cpu") if sampled_at and isinstance(raw.get("cpu"), dict) else {}
    boot = raw.get("boot") if sampled_at and isinstance(raw.get("boot"), dict) else {}
    watchdog = raw.get("watchdog") if sampled_at and isinstance(raw.get("watchdog"), dict) else {}
    celsius = _number(cpu.get("celsius"), -40, 150) if cpu.get("source") == "LINUX_THERMAL" else None
    since = _time(boot.get("tracking_since"), now)
    count = _number(boot.get("count"), 1, 2147483647, integer=True) if since and boot.get("status") == "OBSERVED" else None
    return {"version": 1, "sampled_at": sampled_at,
            "cpu": {"celsius": celsius, "source": "LINUX_THERMAL" if celsius is not None else "UNKNOWN"},
            "boot": {"count": count, "tracking_since": since if count is not None else None,
                     "status": "OBSERVED" if count is not None else "UNKNOWN"},
            "watchdog": {"service_timeout_us": _number(watchdog.get("service_timeout_us"), 0, 86400000000, integer=True),
                         "service_state": watchdog.get("service_state") if watchdog.get("service_state") in ("ACTIVE", "INACTIVE", "FAILED", "ACTIVATING", "DEACTIVATING", "RELOADING") else "UNKNOWN",
                         "hardware_state": watchdog.get("hardware_state") if watchdog.get("hardware_state") in ("ACTIVE", "INACTIVE") else "UNKNOWN",
                         "hardware_timeout_seconds": _number(watchdog.get("hardware_timeout_seconds"), 0, 86400, integer=True),
                         "recovery": "NOT_VERIFIED"}}


def watchdog_enabled(health):
    watchdog = health["watchdog"]
    if watchdog["hardware_state"] == "ACTIVE":
        return True
    if watchdog["hardware_state"] == "INACTIVE" and watchdog["service_timeout_us"] == 0:
        return False
    return None


def dashboard_view(storage_snapshot, now, max_age):
    raw = storage_snapshot.get("controller_health") if isinstance(storage_snapshot, dict) else None
    out = normalize_report(raw, now)
    age = (now - datetime.fromisoformat(out["sampled_at"])).total_seconds() if out["sampled_at"] else None
    out["status"] = "UNKNOWN" if age is None else "STALE" if age > max_age else "CURRENT"
    out["max_age_seconds"] = max_age
    return out