"""Standalone, read-only installer preflight. Never imports controller/GPIO code.

Shipped as tools/preflight.py. Existing OTA layouts are deliberately refused:
the protected boot selector needs a separately coordinated update, not deletion.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys


class PreflightError(ValueError):
    pass


def ota_conflicts(data_mount):
    root = Path(data_mount) / "ota"
    candidates = [root / "active.json", root / "meta.json", root / "slot_a", root / "slot_b"]
    return [p.name for p in candidates if p.exists() or p.is_symlink()]


def check_storage(fstype, actual_uuid, configured_uuid):
    if fstype != "ext4":
        raise PreflightError("Data storage must be a separately mounted ext4 filesystem; prepare it before installing.")
    if not actual_uuid or not configured_uuid or actual_uuid != configured_uuid:
        raise PreflightError("Data mount UUID must match its fstab UUID. No storage will be prepared or changed by this installer.")


def findmnt_value(data_mount, field, *, fstab=False):
    args = ["findmnt", "--noheadings", "--raw", "--output", field, "--mountpoint", str(data_mount)]
    if fstab:
        args.append("--fstab")
    result = subprocess.run(args, capture_output=True, text=True, timeout=5, check=False)
    if result.returncode:
        raise PreflightError("Cannot verify the data mount/fstab; no installation changes made.")
    lines = result.stdout.strip().splitlines()
    if len(lines) != 1:
        raise PreflightError("Ambiguous data mount/fstab entry; resolve it before installation.")
    return lines[0].strip()


def check_installation(package, data_mount, firmware_dest, *, layout_only=False):
    package, data_mount, firmware_dest = map(Path, (package, data_mount, firmware_dest))
    if not data_mount.is_dir() or data_mount.is_symlink():
        raise PreflightError("Prepare and mount the existing data filesystem first; this installer never formats or bootstraps storage.")
    if firmware_dest.is_symlink():
        raise PreflightError("Firmware destination is a symlink; use a reviewed migration instead.")
    if ota_conflicts(data_mount):
        raise PreflightError("Existing OTA layout detected. STOP: coordinate a complete OTA-aware update; do not delete slots or active metadata to bypass this check.")
    info = json.loads((package / "BUILD_INFO.json").read_text())
    if not isinstance(info, dict) or info.get("package_schema") != 2:
        raise PreflightError("Unsupported package schema; use the reviewed current generator.")
    names = info.get("runtime_files")
    if not isinstance(names, list) or not names or any(not isinstance(n, str) or not n for n in names) or len(names) != len(set(names)):
        raise PreflightError("Invalid runtime manifest file list.")
    digest = hashlib.sha256()
    for name in sorted(names):
        part = Path(name)
        if part.is_absolute() or ".." in part.parts:
            raise PreflightError("Invalid runtime manifest path.")
        source = package / "firmware" / part
        if source.is_symlink():
            raise PreflightError("Runtime symlinks are not supported.")
        digest.update(name.encode() + b"\0" + source.read_bytes())
    if digest.hexdigest() != info.get("runtime_sha256"):
        raise PreflightError("Runtime build fingerprint mismatch.")
    if not layout_only:
        check_storage(findmnt_value(data_mount, "FSTYPE"), findmnt_value(data_mount, "UUID"),
                      findmnt_value(data_mount, "UUID", fstab=True))
        missing = [m for m in ("requests", "gpiozero", "lgpio", "minimalmodbus", "serial")
                   if importlib.util.find_spec(m) is None]
        if missing:
            raise PreflightError("Required Python dependencies unavailable: " + ", ".join(missing) + ". Prepare them separately; hardware libraries were not imported.")
        parent = firmware_dest.parent
        while not parent.exists():
            parent = parent.parent
        required = sum((package / "firmware" / n).stat().st_size for n in info["runtime_files"]) + 16 * 1024 * 1024
        if shutil.disk_usage(parent).free < required:
            raise PreflightError("Insufficient space to stage firmware safely; do not delete runtime data to continue.")
    return info["runtime_sha256"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("package")
    parser.add_argument("data_mount")
    parser.add_argument("firmware_dest")
    parser.add_argument("--layout-only", action="store_true")
    args = parser.parse_args()
    try:
        fingerprint = check_installation(args.package, args.data_mount, args.firmware_dest, layout_only=args.layout_only)
    except (PreflightError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        # The error is safe to render only for deliberate preflight messages.
        error = sys.exc_info()[1]
        print("ERROR: " + (str(error) if isinstance(error, PreflightError) else "Unable to verify package/install prerequisites."), file=sys.stderr)
        return 4
    print("Read-only preflight: OK; runtime SHA256 " + fingerprint)
    return 0


if __name__ == "__main__":
    sys.exit(main())