"""EMS data-storage health (USB pen drive / SD / eMMC / NVMe) — read-only diagnostics.

Non-destructive by design: statvfs, /proc/mounts, sysfs and an optional tiny application-owned probe
file inside the EMS data directory (written, fsynced, read back, removed). Never repairs, formats or erases anything.
SMART comes from smart_health.collect(); UNAVAILABLE is never upgraded to PASSED or GOOD.
"""
import os
import time

WRITE_PROBE_NAME = ".ems_write_probe"
USAGE_WARNING_PERCENT = 90.0
USAGE_FAILED_PERCENT = 98.0

# Secondary (USB data volume) verdict used by the logging policy. Only HEALTHY may hold EMS logs.
SECONDARY_HEALTHY = "SECONDARY_HEALTHY"
SECONDARY_UNAVAILABLE = "SECONDARY_UNAVAILABLE"   # not mounted / mount is the OS root / UUID mismatch or unknown
SECONDARY_UNWRITABLE = "SECONDARY_UNWRITABLE"     # correct volume mounted but the write probe failed
SECONDARY_STATES = (SECONDARY_HEALTHY, SECONDARY_UNAVAILABLE, SECONDARY_UNWRITABLE)
BY_UUID_DIR = "/dev/disk/by-uuid"


def expected_uuid(data_dir, fstab_text=None):
    """Filesystem UUID configured for data_dir: env EMS_DATA_UUID, else the fstab entry setup_pi.sh wrote."""
    env = os.environ.get("EMS_DATA_UUID", "").strip()
    if env:
        return env
    try:
        if fstab_text is None:
            with open("/etc/fstab") as fh:
                fstab_text = fh.read()
    except OSError:
        return None
    for line in fstab_text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and not parts[0].startswith("#") and parts[1] == data_dir and parts[0].upper().startswith("UUID="):
            return parts[0][5:] or None
    return None


def mounted_uuid(dev):
    """UUID of the block device that is actually mounted (by-uuid symlink resolving to dev). Never /dev/sdX assumptions."""
    try:
        target = os.path.realpath(dev)
        for name in os.listdir(BY_UUID_DIR):
            if os.path.realpath(os.path.join(BY_UUID_DIR, name)) == target:
                return name
    except OSError:
        pass
    return None


def classify_secondary(mounted, uuid_ok, writable):
    if not mounted or not uuid_ok:
        return SECONDARY_UNAVAILABLE
    if not writable:
        return SECONDARY_UNWRITABLE
    return SECONDARY_HEALTHY


def _mount_for(path, mounts_text):
    best = None
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        dev, mnt, fs = parts[0], parts[1], parts[2]
        if path == mnt or path.startswith(mnt.rstrip("/") + "/"):
            if best is None or len(mnt) > len(best[1]):
                best = (dev, mnt, fs)
    return best


def device_type(dev, sysfs_reader=None):
    """Classify honestly: usb-removable / sd-emmc / nvme / virtual / unknown. Never assume USB."""
    base = os.path.basename(dev or "")
    if not base:
        return "unknown"
    if base.startswith("mmcblk"):
        return "sd-emmc"
    if base.startswith("nvme"):
        return "nvme"
    if base.startswith("sd"):
        disk = base.rstrip("0123456789")
        read = sysfs_reader or (lambda p: open(p).read())
        try:
            removable = read(f"/sys/block/{disk}/removable").strip() == "1"
        except OSError:
            return "scsi-unknown"
        return "usb-removable" if removable else "scsi-fixed"
    if base.startswith(("loop", "dm-", "zram")):
        return "virtual"
    return "unknown"


def write_probe(data_dir):
    """Tiny controlled write/read/remove under the EMS data dir. Returns (writable, error)."""
    path = os.path.join(data_dir, WRITE_PROBE_NAME)
    payload = f"ems-probe {time.time()}".encode()
    try:
        with open(path, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        with open(path, "rb") as fh:
            ok = fh.read() == payload
        os.remove(path)
        return ok, (None if ok else "read-back mismatch")
    except OSError as exc:
        try:
            os.remove(path)
        except OSError:
            pass
        return False, f"{type(exc).__name__}: {exc}"


def summarize_smart(smart_result):
    """smart_health.collect() -> (smart_state, smart_available). Distinct UNAVAILABLE vs PASSED."""
    if not smart_result:
        return "UNAVAILABLE", False
    if smart_result.get("smart") != "AVAILABLE":
        return "UNAVAILABLE", False
    health = str(smart_result.get("health", "UNKNOWN")).upper()
    return {"OK": "PASSED", "WARNING": "WARNING", "CRITICAL": "FAILED"}.get(health, "UNAVAILABLE"), health in ("OK", "WARNING", "CRITICAL")


def classify(mounted, readable, writable, used_percent, smart_state, fs_error):
    if not mounted or not readable or not writable or fs_error or smart_state == "FAILED":
        return "FAILED"
    if (used_percent is not None and used_percent >= USAGE_FAILED_PERCENT):
        return "FAILED"
    if smart_state in ("UNAVAILABLE", "WARNING") or (used_percent is not None and used_percent >= USAGE_WARNING_PERCENT):
        return "WARNING"  # never GOOD without positive SMART evidence
    return "GOOD"


def collect_storage_health(data_dir, expected_device=None, smart_result=None, *, mounts_text=None, statvfs=None,
                           probe=None, sysfs_reader=None, fs_error=None, fstab_text=None, uuid_resolver=None, access=None):
    """Snapshot dict (snake_case, additive). All I/O injectable for tests. Never raises.
    Order is a safety property: mount -> UUID -> (only then) write probe. The probe never runs on an
    unverified volume, so it can never touch the primary OS disk."""
    out = {"device": expected_device, "device_type": None, "mounted": False, "mount_point": None, "filesystem": None,
           "total_bytes": None, "used_bytes": None, "free_bytes": None, "used_percent": None,
           "readable": False, "writable": False, "smart": "UNAVAILABLE", "smart_available": False,
           "health": "FAILED", "error": None, "checked_at": time.time(),
           "expected_uuid": None, "uuid": None, "secondary_state": SECONDARY_UNAVAILABLE, "secondary_usable": False}
    uuid_ok = False
    try:
        if mounts_text is None:
            with open("/proc/mounts") as fh:
                mounts_text = fh.read()
        m = _mount_for(data_dir, mounts_text)
        if m is None or m[1] != data_dir.rstrip("/"):
            # Not mounted, or only covered by a parent mount (e.g. the OS root): that is the primary disk.
            out["error"] = f"{data_dir} is not a mounted data volume"
        else:
            dev, mnt, fs = m
            out.update({"device": dev, "mount_point": mnt, "filesystem": fs, "mounted": True})
            out["device_type"] = device_type(dev, sysfs_reader)
            if expected_device and os.path.basename(expected_device) != os.path.basename(dev) and not expected_device.startswith("/dev/disk/by-"):
                out["error"] = f"mounted {dev} but EMS_DATA_DEVICE={expected_device}"
            exp = expected_uuid(data_dir, fstab_text)
            got = (uuid_resolver or mounted_uuid)(dev)
            out.update({"expected_uuid": exp, "uuid": got})
            uuid_ok = bool(exp) and got == exp
            sv = (statvfs or os.statvfs)(mnt)
            total, free = sv.f_frsize * sv.f_blocks, sv.f_frsize * sv.f_bavail
            used = total - sv.f_frsize * sv.f_bfree
            out.update({"total_bytes": total, "used_bytes": used, "free_bytes": free,
                        "used_percent": round(used / total * 100.0, 1) if total else None,
                        "readable": (access or os.access)(data_dir, os.R_OK)})
            if not uuid_ok:
                out["error"] = f"UUID verification failed: mounted={got} expected={exp}"
            else:
                writable, werr = (probe or write_probe)(data_dir)
                out["writable"] = bool(writable)
                if werr:
                    out["error"] = werr
        out["smart"], out["smart_available"] = summarize_smart(smart_result)
        out["health"] = classify(out["mounted"], out["readable"], out["writable"], out["used_percent"], out["smart"], fs_error)
        if fs_error:
            out["error"] = fs_error
        out["secondary_state"] = classify_secondary(out["mounted"], uuid_ok, out["writable"] and not fs_error)
    except Exception as exc:  # diagnostics never take the controller down
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["health"] = "FAILED"
        out["secondary_state"] = SECONDARY_UNAVAILABLE
    out["secondary_usable"] = out["secondary_state"] == SECONDARY_HEALTHY
    return out
