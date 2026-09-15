"""Read-only, bounded health sampling. No device opens, writes, logs or timers.

Boot metadata piggybacks on the existing guarded state commit. Sampling never
calls save_state and never arms/pets a watchdog or changes systemd policy.
"""
import math
import re
import subprocess
import time
from datetime import datetime, timezone
from uuid import UUID


def read_text(path):
    with open(path, "r", encoding="ascii") as stream:
        value = stream.read(257)
    if len(value) > 256:
        raise ValueError("oversized health reading")
    return value.strip()


def service_watchdog():
    result = subprocess.run(
        ["systemctl", "show", "ems-controller.service", "--property=WatchdogUSec,ActiveState", "--no-pager"],
        capture_output=True, text=True, timeout=1, check=False,
    )
    if result.returncode or len(result.stdout) > 1024:
        raise ValueError("service watchdog unavailable")
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def timeout_us(text):
    """Parse bounded systemd time spans; unknown formats never imply enabled."""
    if not isinstance(text, str) or len(text) > 64:
        return None
    units = {"": 1, "us": 1, "µs": 1, "ms": 1000, "s": 1000000, "min": 60000000, "h": 3600000000, "d": 86400000000}
    parts = [re.fullmatch(r"(\d+(?:\.\d+)?)(us|µs|ms|min|s|h|d)?", part) for part in text.split()]
    if not parts or not all(parts):
        return None
    total = sum(float(part[1]) * units[part[2] or ""] for part in parts)
    return int(total) if math.isfinite(total) and 0 <= total <= 86400000000 else None


class HealthTelemetry:
    def __init__(self, state, interval, reader=read_text, service_reader=service_watchdog, clock=time.monotonic, now=None):
        self.state, self.interval, self.read, self.service, self.clock = state, interval, reader, service_reader, clock
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.cached, self.sampled_mono, self.boot_error = None, None, None
        self._observe_boot()

    def _observe_boot(self):
        try:
            if not hasattr(self.state, "health_boot"):
                raise ValueError("STATE_TRACKING_UNAVAILABLE")
            boot_id = str(UUID(self.read("/proc/sys/kernel/random/boot_id")))
            old = self.state.health_boot
            if old is None:
                # A corrupt/unrestored state must not silently restart a counter.
                if getattr(self.state, "health_state_unavailable", False):
                    raise ValueError("STATE_TRACKING_UNAVAILABLE")
                record = {"boot_id": boot_id, "count": 1, "tracking_since": self.now().isoformat()}
            else:
                if not isinstance(old, dict) or type(old.get("count")) is not int or not 1 <= old["count"] < 2147483647:
                    raise ValueError("INVALID_BOOT_RECORD")
                if str(UUID(old["boot_id"])) != old["boot_id"] or datetime.fromisoformat(old["tracking_since"]).tzinfo is None:
                    raise ValueError("INVALID_BOOT_RECORD")
                record = {"boot_id": boot_id, "count": old["count"] + int(old["boot_id"] != boot_id), "tracking_since": old["tracking_since"]}
            # RAM only. The already-existing boot/state writes persist this.
            self.state.health_boot = record
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.boot_error = "BOOT_TRACKING_UNAVAILABLE"

    def _sample(self):
        sampled_at = self.now().isoformat()
        cpu = None
        try:
            value = int(self.read("/sys/class/thermal/thermal_zone0/temp")) / 1000
            if -40 <= value <= 150:
                cpu = round(value, 1)
        except (OSError, ValueError, TypeError):
            pass
        service_timeout, service_state = None, "UNKNOWN"
        try:
            props = self.service()
            service_timeout = timeout_us(props.get("WatchdogUSec"))
            if props.get("ActiveState") in ("active", "inactive", "failed", "activating", "deactivating", "reloading"):
                service_state = props["ActiveState"].upper()
        except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
            pass
        hardware_state, hardware_timeout = "UNKNOWN", None
        try:
            value = self.read("/sys/class/watchdog/watchdog0/state").upper()
            hardware_state = value if value in ("ACTIVE", "INACTIVE") else "UNKNOWN"
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        try:
            value = int(self.read("/sys/class/watchdog/watchdog0/timeout"))
            hardware_timeout = value if 0 <= value <= 86400 else None
        except (OSError, ValueError, TypeError):
            pass
        self.cached = {"version": 1, "sampled_at": sampled_at,
                       "cpu": {"celsius": cpu, "source": "LINUX_THERMAL" if cpu is not None else "UNKNOWN"},
                       "watchdog": {"service_timeout_us": service_timeout, "service_state": service_state,
                                    "hardware_state": hardware_state, "hardware_timeout_seconds": hardware_timeout,
                                    "recovery": "NOT_VERIFIED"}}
        self.sampled_mono = self.clock()

    def snapshot(self):
        if self.cached is None or self.clock() - self.sampled_mono >= self.interval:
            self._sample()
        record = getattr(self.state, "health_boot", None)
        committed = getattr(self.state, "persisted_health_boot", None)
        known = not self.boot_error and record is not None and record == committed
        boot = {"count": record["count"] if known else None, "tracking_since": record["tracking_since"] if known else None,
                "status": "OBSERVED" if known else "UNKNOWN", "reason": None if known else self.boot_error or "AWAITING_EXISTING_STATE_COMMIT"}
        return {**self.cached, "boot": boot}