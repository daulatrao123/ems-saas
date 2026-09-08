"""Pi provisioning package builder (Super Admin). Bundles the EXISTING pi_firmware runtime files,
the existing systemd units and a generated /etc/ems/ems-controller.env into an in-memory ZIP.
The plaintext API key exists only inside the returned ZIP bytes: never logged, never persisted.
"""

import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path


FIRMWARE_DIR = Path(__file__).resolve().parent.parent / "pi_firmware"

# Runtime files required by the EMS controller and OTA/runtime scripts.
#
# IMPORTANT:
# Keep this list synchronized with all local runtime imports/exec dependencies.
# A missing file here creates an incomplete provisioning package.
FIRMWARE_FILES = (
    "ems_controller.py",
    "api_client.py",
    "config.py",
    "config_hash.py",
    "gpio_manager.py",
    "logger.py",
    "memory_manager.py",
    "offline_queue.py",
    "ota_manager.py",
    "resource_guard.py",
    "smart_health.py",
    "state.py",
    "storage_io_manager.py",
    "storage_manager.py",
    "ota_boot.sh",
    "ems-ota-stage.py",
    "setup_pi.sh",
)

SYSTEMD_FILES = (
    "ems-controller.service",
    "ems-reboot.path",
    "ems-reboot.service",
)

ROOT = "ems-pi-provisioning"


INSTALL_SH = r"""#!/bin/bash
# EMS Pi provisioning installer.
# Run from the extracted package directory:
#   sudo ./install.sh
#
# Safety:
# - Never formats disks.
# - Data storage is prepared ONLY by the existing setup_pi.sh.
# - setup_pi.sh requires an existing ext4 filesystem and never formats it.

set -euo pipefail

PREFIX="${EMS_INSTALL_PREFIX:-}"             # test hook: install under a prefix instead of /
SKIP_SYSTEMD="${EMS_SKIP_SYSTEMD:-0}"       # test hook: skip systemctl calls

HERE="$(cd "$(dirname "$0")" && pwd)"

FW_DST="$PREFIX/opt/ems/pi_firmware"
ENV_DST="$PREFIX/etc/ems/ems-controller.env"
UNIT_DST="$PREFIX/etc/systemd/system"
DATA_MOUNT="$PREFIX/mnt/ems-data"


# ------------------------------------------------------------
# Basic platform / privilege validation
# ------------------------------------------------------------

[[ "$(uname -s)" == "Linux" ]] || {
  echo "ERROR: this installer only runs on Linux (Raspberry Pi OS)." >&2
  exit 2
}

if [[ -z "$PREFIX" && "$(id -u)" -ne 0 ]]; then
  echo "ERROR: run as root: sudo ./install.sh" >&2
  exit 2
fi


# ------------------------------------------------------------
# Provisioning package completeness validation
# ------------------------------------------------------------

REQUIRED_FILES=(
  "ems-controller.env"

  "firmware/ems_controller.py"
  "firmware/api_client.py"
  "firmware/config.py"
  "firmware/config_hash.py"
  "firmware/gpio_manager.py"
  "firmware/logger.py"
  "firmware/memory_manager.py"
  "firmware/offline_queue.py"
  "firmware/ota_manager.py"
  "firmware/resource_guard.py"
  "firmware/smart_health.py"
  "firmware/state.py"
  "firmware/storage_io_manager.py"
  "firmware/storage_manager.py"
  "firmware/ota_boot.sh"
  "firmware/ems-ota-stage.py"
  "firmware/setup_pi.sh"

  "systemd/ems-controller.service"
  "systemd/ems-reboot.path"
  "systemd/ems-reboot.service"
)

for f in "${REQUIRED_FILES[@]}"; do
  if [[ ! -f "$HERE/$f" ]]; then
    echo "ERROR: package is incomplete, missing $f" >&2
    exit 3
  fi
done


# ------------------------------------------------------------
# Credential validation
# ------------------------------------------------------------

DEVICE_ID="$(grep -E '^EMS_DEVICE_ID=' "$HERE/ems-controller.env" | cut -d= -f2-)"
API_URL="$(grep -E '^EMS_API_URL=' "$HERE/ems-controller.env" | cut -d= -f2-)"

if [[ -z "$DEVICE_ID" ]]; then
  echo "ERROR: package has no EMS_DEVICE_ID." >&2
  exit 3
fi

if [[ -z "$API_URL" ]]; then
  echo "ERROR: package has no EMS_API_URL." >&2
  exit 3
fi

if ! grep -qE '^EMS_API_KEY=.{16,}' "$HERE/ems-controller.env"; then
  echo "ERROR: package has no valid API key." >&2
  exit 3
fi


# ------------------------------------------------------------
# Data storage validation / preparation
# ------------------------------------------------------------

if [[ ! -d "$DATA_MOUNT" ]]; then

  if [[ -n "${EMS_DATA_DEVICE:-}" && "$SKIP_SYSTEMD" != "1" ]]; then

    echo "Preparing data storage with the existing safe setup_pi.sh..."
    echo "The device will NOT be formatted."

    EMS_DATA_DEVICE="$EMS_DATA_DEVICE" \
      bash "$HERE/firmware/setup_pi.sh"

  else

    echo "ERROR: $DATA_MOUNT does not exist." >&2
    echo "Prepare the data disk first with the existing safe mechanism:" >&2
    echo >&2
    echo "  EMS_DATA_DEVICE=/dev/disk/by-id/<your-ext4-partition> sudo -E ./install.sh" >&2
    echo >&2
    echo "(setup_pi.sh mounts an EXISTING ext4 device; it never formats anything.)" >&2

    exit 4
  fi
fi


# ------------------------------------------------------------
# Install firmware
# ------------------------------------------------------------

echo "Installing firmware to $FW_DST ..."

install -d -m 0755 \
  "$FW_DST" \
  "$(dirname "$ENV_DST")" \
  "$UNIT_DST"

cp "$HERE"/firmware/*.py "$HERE"/firmware/*.sh "$FW_DST"/

chmod 0755 "$FW_DST"/*.sh

if [[ -z "$PREFIX" ]] && getent passwd pi >/dev/null 2>&1; then
  chown -R root:pi "$FW_DST"
fi


# ------------------------------------------------------------
# Verify installed firmware files
# ------------------------------------------------------------

echo "Verifying installed firmware ..."

for f in "${REQUIRED_FILES[@]}"; do

  case "$f" in
    firmware/*)
      installed_file="$FW_DST/${f#firmware/}"

      if [[ ! -f "$installed_file" ]]; then
        echo "ERROR: firmware installation incomplete, missing $installed_file" >&2
        exit 5
      fi
      ;;
  esac

done

echo "Firmware package validation: OK"


# ------------------------------------------------------------
# Credentials
# ------------------------------------------------------------

echo "Writing credentials to $ENV_DST (root:root 0600) ..."

install -m 0600 \
  "$HERE/ems-controller.env" \
  "$ENV_DST"

if [[ -z "$PREFIX" ]]; then
  chown root:root "$ENV_DST"
fi


# ------------------------------------------------------------
# Systemd units
# ------------------------------------------------------------

echo "Installing systemd units ..."

install -m 0644 \
  "$HERE/systemd/ems-controller.service" \
  "$HERE/systemd/ems-reboot.path" \
  "$HERE/systemd/ems-reboot.service" \
  "$UNIT_DST/"


SERVICE_STATE="skipped (test mode)"


# ------------------------------------------------------------
# Start and verify controller
# ------------------------------------------------------------

if [[ "$SKIP_SYSTEMD" != "1" ]]; then

  echo "Reloading systemd ..."
  systemctl daemon-reload

  echo "Enabling EMS services ..."
  systemctl enable \
    ems-reboot.path \
    ems-controller.service \
    >/dev/null

  echo "Starting EMS controller ..."
  systemctl restart ems-controller.service

  echo "Waiting for a stable EMS controller ..."

  SERVICE_STATE="FAILED"

  for i in {1..10}; do

    sleep 1

    if systemctl is-active --quiet ems-controller.service; then

      RESTARTS="$(systemctl show ems-controller.service -p NRestarts --value)"

      if [[ "$RESTARTS" == "0" ]]; then
        SERVICE_STATE="ACTIVE"
        break
      fi

    fi

  done


  # ----------------------------------------------------------
  # Stable service failure
  # ----------------------------------------------------------

  if [[ "$SERVICE_STATE" != "ACTIVE" ]]; then

    echo
    echo "ERROR: EMS controller failed to reach a stable running state." >&2
    echo >&2

    systemctl --no-pager --full status \
      ems-controller.service \
      >&2 || true

    echo >&2
    echo "Recent EMS controller logs:" >&2

    journalctl \
      -u ems-controller.service \
      -n 50 \
      --no-pager \
      >&2 || true

    exit 6
  fi


  # ----------------------------------------------------------
  # Final service status
  # ----------------------------------------------------------

  echo
  echo "EMS controller reached a stable running state."

  systemctl \
    --no-pager \
    --lines=10 \
    status ems-controller.service \
    || true

fi


# ------------------------------------------------------------
# Final result
# ------------------------------------------------------------

cat <<EOF

========================================
 EMS PI PROVISIONING COMPLETE
========================================
 Device ID:   $DEVICE_ID
 API:         $API_URL
 Credential:  CONFIGURED
 Service:     ${SERVICE_STATE^^}

 The controller must perform its first authenticated
 /api/pi/sync before the device appears ONLINE in EMS Cloud.

 Package validation: PASSED
 Firmware installation: PASSED
========================================

EOF
"""


README = """EMS Pi provisioning package
============================

Generated: {generated}
Device:    {name}
Device ID: {device_id}
Key ID:    {key_id}
API URL:   {api_url}

IMPORTANT SECURITY INFORMATION
------------------------------

This package contains a LIVE device secret in:

  ems-controller.env

Treat the ZIP as confidential.

Copy it only to the target Raspberry Pi and delete it afterwards.

Downloading a new provisioning package from EMS Cloud rotates the
device credential. The previous package will then stop working.

INSTALLATION
------------

1. Copy the ZIP to the target Raspberry Pi.

2. Extract it:

     unzip ems-pi-provisioning-*.zip

3. Enter the package directory:

     cd ems-pi-provisioning

4. The data directory must already exist at:

     /mnt/ems-data

   OR prepare an existing ext4 device using:

     EMS_DATA_DEVICE=/dev/disk/by-id/<partition> sudo -E ./install.sh

   The storage preparation mechanism NEVER formats a disk.

5. Install:

     sudo ./install.sh

6. The installer validates the complete firmware package before
   starting the controller.

7. The installer waits for the EMS controller to reach a stable
   running state. A temporary "active" state during a crash/restart
   loop is NOT considered successful.

8. The website shows ONLINE only after the first authenticated:

     /api/pi/sync

PACKAGE CONTENTS
----------------

install.sh
ems-controller.env
README.txt

firmware/
  ems_controller.py
  api_client.py
  config.py
  config_hash.py
  gpio_manager.py
  logger.py
  memory_manager.py
  offline_queue.py
  ota_manager.py
  resource_guard.py
  smart_health.py
  state.py
  storage_io_manager.py
  storage_manager.py
  ota_boot.sh
  ems-ota-stage.py
  setup_pi.sh

systemd/
  ems-controller.service
  ems-reboot.path
  ems-reboot.service
"""


def _validate_source_files() -> None:
    """Fail package generation if any required source artifact is missing."""

    missing = []

    for filename in FIRMWARE_FILES:
        path = FIRMWARE_DIR / filename

        if not path.is_file():
            missing.append(f"pi_firmware/{filename}")

    for filename in SYSTEMD_FILES:
        path = FIRMWARE_DIR / filename

        if not path.is_file():
            missing.append(f"pi_firmware/{filename}")

    if missing:
        raise FileNotFoundError(
            "Cannot build Pi provisioning package; missing source files:\n"
            + "\n".join(f"  - {item}" for item in missing)
        )


def build_provisioning_zip(
    device_id: str,
    device_name: str,
    key_id: str,
    api_key: str,
    api_url: str,
) -> bytes:
    """
    Build the complete Pi provisioning ZIP in memory.

    The plaintext API key is included only in the returned ZIP bytes.
    It is never logged or persisted by this function.
    """

    _validate_source_files()

    env = (
        f"EMS_API_URL={api_url}\n"
        f"EMS_DEVICE_ID={device_id}\n"
        f"EMS_API_KEY={api_key}\n"
    )

    buf = io.BytesIO()

    with zipfile.ZipFile(
        buf,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as z:

        def add(
            arcname: str,
            data: bytes,
            mode: int = 0o644,
        ) -> None:

            info = zipfile.ZipInfo(
                f"{ROOT}/{arcname}",
                date_time=datetime.now(timezone.utc).timetuple()[:6],
            )

            info.external_attr = (
                0o100000 | mode
            ) << 16

            z.writestr(
                info,
                data,
            )

        # ----------------------------------------------------
        # Top-level package files
        # ----------------------------------------------------

        add(
            "install.sh",
            INSTALL_SH.encode(),
            0o755,
        )

        add(
            "ems-controller.env",
            env.encode(),
            0o600,
        )

        add(
            "README.txt",
            README.format(
                generated=datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                ),
                name=device_name,
                device_id=device_id,
                key_id=key_id,
                api_url=api_url,
            ).encode(),
        )

        # ----------------------------------------------------
        # Firmware
        # ----------------------------------------------------

        for filename in FIRMWARE_FILES:

            source = FIRMWARE_DIR / filename

            add(
                f"firmware/{filename}",
                source.read_bytes(),
                0o755 if filename.endswith(".sh") else 0o644,
            )

        # ----------------------------------------------------
        # Systemd
        # ----------------------------------------------------

        for filename in SYSTEMD_FILES:

            source = FIRMWARE_DIR / filename

            add(
                f"systemd/{filename}",
                source.read_bytes(),
            )

    return buf.getvalue()
