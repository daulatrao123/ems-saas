"""Phase 0.5 endurance-simulation harness.

Drives the REAL firmware modules (OfflineQueue, StorageManager, ResourceGuard,
PiStateManager, logger) against a temp data directory with:
  * a fake wall clock + monotonic clock (accelerated time)
  * a fake statvfs (controllable disk fill / failure)
  * a byte meter on every Python-level file write, plus /proc/self/io for the
    whole process (captures SQLite, which writes from C).

No firmware source is modified by the harness; only module-level names are
patched (config paths, datetime, time.monotonic/time.time, os.statvfs).
"""
import builtins
import io
import json
import os
os.environ.setdefault("EMS_LCD_ENABLED", "0")  # simulation harness has no LCD hardware
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

os.environ.setdefault("EMS_DEVICE_ID", "phase05-sim-device")
os.environ.setdefault("EMS_API_KEY", "phase05-sim-key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pi_firmware"))  # repo-relative (CI / fresh clone)

TMP = tempfile.mkdtemp(prefix="phase05_")
REAL_OPEN = builtins.open
REAL_STATVFS = os.statvfs
REAL_MONOTONIC = time.monotonic
REAL_TIME = time.time

# ---------------------------------------------------------------- clock
SIM_START = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.t = SIM_START
        self._mono0 = 10_000.0

    def advance(self, seconds):
        self.t += timedelta(seconds=seconds)

    def monotonic(self):
        return self._mono0 + (self.t - SIM_START).total_seconds()

    def epoch(self):
        return self.t.timestamp()


CLOCK = Clock()


class FakeDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return CLOCK.t.replace(tzinfo=None)
        return CLOCK.t.astimezone(tz)


time.monotonic = CLOCK.monotonic
time.time = CLOCK.epoch

# ---------------------------------------------------------------- disk
DISK = {"total_bytes": 8 * 1024 ** 3, "used_percent": 40.0, "fail": False}


class _FakeStat:
    f_frsize = 4096

    def __init__(self):
        self.f_blocks = DISK["total_bytes"] // 4096
        free = int(self.f_blocks * (1 - DISK["used_percent"] / 100.0))
        self.f_bavail = free
        self.f_bfree = free


def fake_statvfs(path):
    if DISK["fail"]:
        raise OSError(5, "Input/output error (simulated)")
    return _FakeStat()


os.statvfs = fake_statvfs


def set_disk(used_percent=None, fail=None):
    if used_percent is not None:
        DISK["used_percent"] = float(used_percent)
    if fail is not None:
        DISK["fail"] = bool(fail)


# ---------------------------------------------------------------- byte meter
PY_WRITES = {}          # category -> bytes written through Python open()
PY_WRITE_CALLS = {}     # category -> number of write() calls


def _category(path):
    p = str(path)
    if "/logs/critical.log" in p:
        return "critical_log"
    if "/logs/" in p:
        return "normal_log"
    if "/state/" in p:
        return "state"
    if "/telemetry/" in p:
        return "telemetry_json"
    if "/health/" in p:
        return "health"
    if "/diagnostics/" in p:
        return "diagnostics"
    if "/queue/" in p:
        return "queue_py"
    return "other"


class _CountingFile:
    def __init__(self, fh, cat):
        self._fh, self._cat = fh, cat

    def write(self, data):
        n = self._fh.write(data)
        size = len(data.encode("utf-8")) if isinstance(data, str) else len(data)
        PY_WRITES[self._cat] = PY_WRITES.get(self._cat, 0) + size
        PY_WRITE_CALLS[self._cat] = PY_WRITE_CALLS.get(self._cat, 0) + 1
        return n

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return self._fh.__exit__(*a)

    def __getattr__(self, name):
        return getattr(self._fh, name)


def counting_open(file, mode="r", *args, **kwargs):
    fh = REAL_OPEN(file, mode, *args, **kwargs)
    if isinstance(file, (str, bytes, os.PathLike)) and str(file).startswith(TMP) and any(m in mode for m in "wax+"):
        return _CountingFile(fh, _category(file))
    return fh


builtins.open = counting_open
io.open = counting_open


def proc_io():
    out = {}
    with REAL_OPEN("/proc/self/io") as fh:
        for line in fh:
            k, v = line.split(":")
            out[k.strip()] = int(v)
    return out


class IOMeter:
    """Snapshot of process-level write counters (wchar = bytes handed to
    write(2) by this process incl. SQLite; write_bytes = bytes sent to the
    block layer)."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.start = proc_io()
        self.py_start = dict(PY_WRITES)

    def delta(self):
        now = proc_io()
        py_now = dict(PY_WRITES)
        py = {k: py_now.get(k, 0) - self.py_start.get(k, 0) for k in set(py_now) | set(self.py_start)}
        py_total = sum(py.values())
        wchar = now["wchar"] - self.start["wchar"]
        return {
            "wchar_bytes": wchar,
            "write_bytes_block": now["write_bytes"] - self.start["write_bytes"],
            "python_bytes": py,
            "python_total": py_total,
            "sqlite_bytes_est": max(0, wchar - py_total),
        }


# ---------------------------------------------------------------- firmware import (paths patched first)
import config  # noqa: E402

for _name in (
    "DATA_DIR", "STATE_DIR", "LOG_DIR", "QUEUE_DIR", "TELEMETRY_DIR", "DIAGNOSTICS_DIR",
    "HEALTH_DIR", "STATE_FILE", "BACKUP_STATE_FILE", "RECOVERY_STATE_FILE", "DB_FILE",
    "STORAGE_COUNTER_FILE",
):
    setattr(config, _name, getattr(config, _name).replace("/mnt/ems-data", TMP))
for _d in (config.DATA_DIR, config.STATE_DIR, config.LOG_DIR, config.QUEUE_DIR,
           config.TELEMETRY_DIR, config.DIAGNOSTICS_DIR, config.HEALTH_DIR):
    os.makedirs(_d, exist_ok=True)

import logger as logger_mod  # noqa: E402
import storage_health as _storage_health  # noqa: E402

# Logging policy: file sinks are attached by the controller only after the secondary volume is
# verified HEALTHY. The harness plays that role for TMP so byte metering keeps seeing the log files.
logger_mod.attach_file_sinks()

# Present TMP (and the canonical /mnt/ems-data used by unit tests) as a UUID-verified mounted USB
# volume unless a test injects its own mounts/fstab/uuid view. Patched before ems_controller imports.
SIM_UUID = "SIM-USB-UUID"
_SIM_MOUNTS = f"/dev/mmcblk0p2 / ext4 rw 0 0\n/dev/sda1 /mnt/ems-data ext4 rw 0 0\n/dev/sdz1 {TMP} ext4 rw 0 0\n"
_SIM_FSTAB = f"UUID={SIM_UUID} /mnt/ems-data ext4 defaults 0 2\nUUID={SIM_UUID} {TMP} ext4 defaults 0 2\n"
_REAL_COLLECT = _storage_health.collect_storage_health


def sim_collect_storage_health(*args, **kwargs):
    defaults = {"mounts_text": _SIM_MOUNTS, "fstab_text": _SIM_FSTAB, "access": lambda p, m: True,
                "uuid_resolver": lambda dev: SIM_UUID if dev in ("/dev/sda1", "/dev/sdz1") else None}
    return _REAL_COLLECT(*args, **{**defaults, **kwargs})


_storage_health.collect_storage_health = sim_collect_storage_health
import storage_io_manager  # noqa: E402
import memory_manager  # noqa: E402
import resource_guard  # noqa: E402
import storage_manager  # noqa: E402
import offline_queue  # noqa: E402
import state as state_mod  # noqa: E402

for _m in (logger_mod, storage_io_manager, memory_manager, storage_manager, offline_queue, state_mod):
    _m.datetime = FakeDatetime

# The monitor thread would race the deterministic driver; disable it and
# call monitor_once() from the simulation instead.
storage_manager.StorageManager._monitor_loop = lambda self: None

logger = logger_mod.logger
import logging as _logging  # noqa: E402

# Console echo would inflate /proc/self/io wchar; keep only the file handlers.
for _h in list(logger.handlers):
    if type(_h) is _logging.StreamHandler:
        logger.removeHandler(_h)
ResourceGuard = resource_guard.ResourceGuard


def build_firmware():
    sm = storage_manager.StorageManager()
    st = state_mod.PiStateManager(sm)
    q = offline_queue.OfflineQueue(sm)
    return sm, st, q


# ---------------------------------------------------------------- device model
class Device:
    """Minimal stand-in for EMSController's persistence-relevant behaviour."""

    def __init__(self):
        self.storage, self.state, self.queue = build_firmware()
        self.seq = 0
        self.pending_events = []
        self.events_sent = []
        self.sync_count = 0
        self.last_cleanup_mono = CLOCK.monotonic()
        self.last_usage_day = None

    # --- sync = READS ONLY + RAM event bookkeeping (mirrors _build_snapshot)
    def sync(self, online=True):
        self.sync_count += 1
        status = self.storage.get_status()
        for tr in self.storage.pop_storage_transitions():
            self.pending_events.append(tr)
        self.pending_events = self.pending_events[-20:]
        self.queue.get_last_executed_sequence()
        self.queue.get_executed_command_ids()
        snapshot = {
            "diskFreeMB": round(status["free_mb"], 1) if status["storage_ok"] else -1.0,
            "storageState": status["storage_state"],
            "events": list(self.pending_events),
        }
        if online:
            self.events_sent.extend(self.pending_events)
            self.pending_events = []
            self.flush_acks()
        return snapshot

    def flush_acks(self):
        for cmd_id, *_ in self.queue.get_unacked():
            self.queue.mark_acked(cmd_id)

    # --- one full command lifecycle (mirrors controller: 2 immediate state flushes, 2 info logs)
    def run_command(self, slot="A", action="ACTIVATE"):
        self.seq += 1
        cmd_id = f"cmd-{self.seq:07d}"
        now = FakeDatetime.now(timezone.utc)
        ok = self.queue.add_command(cmd_id, slot, action, now.isoformat(),
                                    (now + timedelta(seconds=300)).isoformat(),
                                    sequence_no=self.seq, cloud_attempt=1)
        assert ok, "queue_db write must always be attempted/succeed"
        logger.info("Command received %s", cmd_id)
        claimed = self.queue.claim_next()
        assert claimed and claimed[0] == cmd_id
        self.state.set_commanded(slot, state_mod.CommandedState.ON, immediate=True)
        self.queue.update_status(cmd_id, "HARDWARE_VERIFIED", "VERIFIED_ON")
        self.queue.update_status(cmd_id, "COMPLETED", "VERIFIED_ON")
        self.state.save_state(immediate=True)
        logger.info("Command executed %s", cmd_id)
        return cmd_id

    def daily_usage(self):
        day = FakeDatetime.now().date().isoformat()
        if self.last_usage_day == day:
            return
        self.last_usage_day = day
        for s in ("A", "B", "C"):
            self.state.set_last_usage_date(day, immediate=False)
        self.state.save_state(immediate=True)

    def hourly_cleanup(self):
        if CLOCK.monotonic() - self.last_cleanup_mono >= 3600:
            self.last_cleanup_mono = CLOCK.monotonic()
            return self.queue.cleanup_acked()
        return 0

    def minute_tick(self, online=True):
        """One 60-second controller cadence: monitor + sync + housekeeping."""
        self.storage.monitor_once()
        snap = self.sync(online=online)
        self.daily_usage()
        deleted = self.hourly_cleanup()
        return snap, deleted


# ---------------------------------------------------------------- reporting
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    return bool(cond)


def summary(title, extra):
    passed = sum(1 for _, c, _ in RESULTS if c)
    lines = [f"\n=== {title} ===", f"{passed}/{len(RESULTS)} checks passed"]
    for n, c, d in RESULTS:
        lines.append(("PASS " if c else "FAIL ") + n + (f"  [{d}]" if d else ""))
    lines.append("\n" + json.dumps(extra, indent=2, default=str))
    lines.append(
        "\nNOTE: these figures are SOFTWARE-GENERATED write budgets and projections. "
        "They do not prove NAND endurance or 10-year hardware life; real wear depends on "
        "the flash controller/FTL write amplification, power-loss behaviour and SMART telemetry."
    )
    return "\n".join(lines), passed == len(RESULTS)


def file_sizes():
    out = {}
    for root, _, files in os.walk(TMP):
        for f in files:
            p = os.path.join(root, f)
            out[os.path.relpath(p, TMP)] = os.path.getsize(p)
    return out
