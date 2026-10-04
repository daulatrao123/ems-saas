"""Expected prerequisites for a new Pi, taken from the current installer.

This does not inspect a Pi, rotate a credential, or build a provisioning ZIP.
Cloud sync fields are not treated as proof of OS, storage, Python, paths, or OTA layout.
"""
import inspect
import re

try:
    from provisioning import FIRMWARE_FILES, INSTALL_SH, REQUIRED_SERVICE_LINES, SYSTEMD_FILES
    from provisioning_preflight import REQUIRED_PYTHON_MODULES, ota_conflicts
except ImportError:
    from backend.provisioning import FIRMWARE_FILES, INSTALL_SH, REQUIRED_SERVICE_LINES, SYSTEMD_FILES
    from backend.provisioning_preflight import REQUIRED_PYTHON_MODULES, ota_conflicts

EXPECTED = "EXPECTED PREREQUISITE"
VERIFIED = "VERIFIED BY DEVICE"
UNVERIFIED = "NOT VERIFIED"

DATA_MOUNT = "/mnt/ems-data"
FIRMWARE_DEST = "/opt/ems/pi_firmware"
ENV_PATH = "/etc/ems/ems-controller.env"
UNIT_DEST = "/etc/systemd/system"
PYTHON = "/usr/bin/python3"
STAGING_HEADROOM = "16 MiB"


def ota_conflict_names():
    """Names tools/preflight.py already refuses. Parsed from that function, not a second list."""
    names = tuple(re.findall(r'root / "([^"]+)"', inspect.getsource(ota_conflicts)))
    if not names:
        raise RuntimeError("OTA conflict names could not be read from the installer preflight")
    return names


def _item(item_id, title, detail, source, **extra):
    row = {
        "id": item_id,
        "title": title,
        "expectation": EXPECTED,
        "verification": UNVERIFIED,
        "detail": detail,
        "source": source,
    }
    row.update(extra)
    return row


def new_pi_preflight_report(device_record=None):
    """Return the installer checklist. device_record cannot mark a Pi verified."""
    modules = tuple(REQUIRED_PYTHON_MODULES)
    conflicts = ota_conflict_names()
    runtime = tuple(FIRMWARE_FILES)
    units = tuple(SYSTEMD_FILES)
    record = device_record if isinstance(device_record, dict) else None
    items = [
        _item(
            "raspberry-pi-os",
            "Raspberry Pi OS",
            "install.sh exits unless uname -s is Linux, and its error says it only runs on "
            "Raspberry Pi OS. The install must be started as root with sudo ./install.sh. "
            f"The read-only preflight is {PYTHON} tools/preflight.py. "
            "The target operating system has not been inspected.",
            "install.sh",
        ),
        _item(
            "data-mount",
            "Data storage mount",
            f"The existing data filesystem must already be mounted at {DATA_MOUNT}. "
            "The installer requires that path to be a real directory and refuses a symlink. "
            f"ems-controller.service must keep {REQUIRED_SERVICE_LINES[0]}. "
            "install.sh does not format a disk, create the mount, or edit fstab.",
            "install.sh and tools/preflight.py",
            path=DATA_MOUNT,
        ),
        _item(
            "filesystem",
            "ext4 filesystem and UUID",
            f"{DATA_MOUNT} must be a separately mounted ext4 filesystem, and the UUID reported "
            "by findmnt for that mount must equal the UUID in fstab. A directory by itself is not enough. "
            "Cloud storage_state is a disk-free flag from sync. It is not this ext4 or UUID check.",
            "tools/preflight.py check_storage",
            filesystem="ext4",
        ),
        _item(
            "python-modules",
            "Python modules",
            f"{PYTHON} must already be able to find these modules, without the installer importing "
            "GPIO or meter libraries or installing packages: " + ", ".join(modules) + ".",
            "tools/preflight.py REQUIRED_PYTHON_MODULES",
            modules=list(modules),
        ),
        _item(
            "application-paths",
            "Application paths",
            f"Firmware is installed at {FIRMWARE_DEST}, which must not be a symlink. "
            f"The device env file is installed at {ENV_PATH}. "
            f"Units {', '.join(units)} are installed under {UNIT_DEST}. "
            "None of these paths have been listed from the target Pi.",
            "install.sh",
            paths=[FIRMWARE_DEST, ENV_PATH, UNIT_DEST],
            units=list(units),
        ),
        _item(
            "ota-layout",
            "OTA layout conflict",
            f"install.sh refuses the install when {DATA_MOUNT}/ota contains "
            + ", ".join(conflicts) + ". "
            "Those entries must not be deleted to bypass the check. An OTA-aware update is a separate step. "
            "Cloud ota_state is not a listing of that directory.",
            "tools/preflight.py ota_conflicts",
            conflicts=list(conflicts),
        ),
        _item(
            "firmware-runtime",
            "Firmware and runtime tree",
            f"The current package must contain {len(runtime)} runtime files, the systemd units, "
            "package_schema 2, and a matching runtime SHA256. "
            f"The Pi also needs free space for that tree plus {STAGING_HEADROOM}. "
            "This screen has not checked those files on a Pi.",
            "install.sh and tools/preflight.py",
            runtime_files=list(runtime),
        ),
        _item(
            "credential",
            "Credential and package prerequisites",
            "The ZIP must include ems-controller.env with EMS_DEVICE_ID, EMS_API_URL, and an "
            "EMS_API_KEY of at least 16 characters, plus MANIFEST.sha256 and BUILD_INFO.json. "
            "Downloading the provisioning ZIP rotates the cloud credential. "
            f"Whether {ENV_PATH} exists on the Pi is not known. No credential value is included here.",
            "install.sh",
        ),
    ]
    if any(item["verification"] != UNVERIFIED or item["expectation"] != EXPECTED for item in items):
        raise RuntimeError("new-Pi preflight must not claim device verification")
    return {
        "scope": "new-pi-provisioning-preflight",
        "device_inspected": False,
        "compliant": False,
        "summary": (
            "No physical Pi was inspected. These are the prerequisites already enforced by "
            "install.sh and tools/preflight.py. ONLINE, storage_state, and ota_state do not "
            "make a Pi compliant."
        ),
        "labels": {"expected": EXPECTED, "verified": VERIFIED, "unverified": UNVERIFIED},
        "items": items,
        "cloud_record": {
            "used_as_verification": False,
            "present": record is not None,
            "note": (
                "A device record can show a credential, last sync, storage_state, or ota_state. "
                "Those fields are not a filesystem, OS, Python, path, or OTA-directory inspection."
            ),
        },
    }
