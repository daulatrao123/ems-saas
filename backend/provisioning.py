"""Pi provisioning package builder (Super Admin). Bundles the EXISTING pi_firmware runtime files,
the existing systemd units and a generated /etc/ems/ems-controller.env into an in-memory ZIP.
The plaintext API key exists only inside the returned ZIP bytes: never logged, never persisted."""
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path

FIRMWARE_DIR = Path(__file__).resolve().parent.parent / "pi_firmware"
# Only what ota_boot.sh / ems_controller.py actually import or exec (no simulators, tests or docs).
FIRMWARE_FILES = (
    "ems_controller.py", "api_client.py", "config.py", "config_hash.py", "gpio_manager.py", "logger.py",
    "memory_manager.py", "offline_queue.py", "ota_manager.py", "resource_guard.py", "state.py",
    "storage_io_manager.py", "storage_manager.py", "ota_boot.sh", "ems-ota-stage.py", "setup_pi.sh",
)
SYSTEMD_FILES = ("ems-controller.service", "ems-reboot.path", "ems-reboot.service")
ROOT = "ems-pi-provisioning"

INSTALL_SH = r"""#!/bin/bash
# EMS Pi provisioning installer. Run from the extracted package directory:  sudo ./install.sh
# Safety: never formats disks. Data storage is prepared ONLY by the existing setup_pi.sh (EMS_DATA_DEVICE, never formatted).
set -euo pipefail
PREFIX="${EMS_INSTALL_PREFIX:-}"            # test hook: install under a prefix instead of /
SKIP_SYSTEMD="${EMS_SKIP_SYSTEMD:-0}"       # test hook: skip systemctl calls
HERE="$(cd "$(dirname "$0")" && pwd)"
FW_DST="$PREFIX/opt/ems/pi_firmware"
ENV_DST="$PREFIX/etc/ems/ems-controller.env"
UNIT_DST="$PREFIX/etc/systemd/system"
DATA_MOUNT="$PREFIX/mnt/ems-data"

[[ "$(uname -s)" == "Linux" ]] || { echo "ERROR: this installer only runs on Linux (Raspberry Pi OS)." >&2; exit 2; }
if [[ -z "$PREFIX" && "$(id -u)" -ne 0 ]]; then echo "ERROR: run as root:  sudo ./install.sh" >&2; exit 2; fi
for f in ems-controller.env firmware/ems_controller.py firmware/ota_boot.sh systemd/ems-controller.service systemd/ems-reboot.path systemd/ems-reboot.service; do
  [[ -f "$HERE/$f" ]] || { echo "ERROR: package is incomplete, missing $f" >&2; exit 3; }
done
DEVICE_ID="$(grep -E '^EMS_DEVICE_ID=' "$HERE/ems-controller.env" | cut -d= -f2-)"
API_URL="$(grep -E '^EMS_API_URL=' "$HERE/ems-controller.env" | cut -d= -f2-)"
grep -qE '^EMS_API_KEY=.{16,}' "$HERE/ems-controller.env" || { echo "ERROR: package has no API key." >&2; exit 3; }

if [[ ! -d "$DATA_MOUNT" ]]; then
  if [[ -n "${EMS_DATA_DEVICE:-}" && "$SKIP_SYSTEMD" != "1" ]]; then
    echo "Preparing data storage with the existing safe setup_pi.sh (device is never formatted)..."
    EMS_DATA_DEVICE="$EMS_DATA_DEVICE" bash "$HERE/firmware/setup_pi.sh"
  else
    echo "ERROR: $DATA_MOUNT does not exist. Prepare the data disk first with the existing safe mechanism:" >&2
    echo "       EMS_DATA_DEVICE=/dev/disk/by-id/<your-ext4-partition> sudo -E ./install.sh" >&2
    echo "       (setup_pi.sh mounts an EXISTING ext4 device; it never formats anything.)" >&2
    exit 4
  fi
fi

echo "Installing firmware to $FW_DST ..."
install -d -m 0755 "$FW_DST" "$(dirname "$ENV_DST")" "$UNIT_DST"
cp "$HERE"/firmware/*.py "$HERE"/firmware/*.sh "$FW_DST"/
chmod 0755 "$FW_DST"/*.sh
if [[ -z "$PREFIX" ]] && getent passwd pi >/dev/null 2>&1; then chown -R root:pi "$FW_DST"; fi

echo "Writing credentials to $ENV_DST (root:root 0600) ..."
install -m 0600 "$HERE/ems-controller.env" "$ENV_DST"
[[ -z "$PREFIX" ]] && chown root:root "$ENV_DST" || true

echo "Installing systemd units ..."
install -m 0644 "$HERE"/systemd/ems-controller.service "$HERE"/systemd/ems-reboot.path "$HERE"/systemd/ems-reboot.service "$UNIT_DST"/
SERVICE_STATE="skipped (test mode)"
if [[ "$SKIP_SYSTEMD" != "1" ]]; then
  systemctl daemon-reload
  systemctl enable ems-reboot.path ems-controller.service >/dev/null
  systemctl restart ems-controller.service
  sleep 2
  SERVICE_STATE="$(systemctl is-active ems-controller.service || true)"
  systemctl --no-pager --lines=5 status ems-controller.service || true
fi

cat <<EOF

========================================
 EMS PI PROVISIONING COMPLETE
========================================
 Device ID:   $DEVICE_ID
 API:         $API_URL
 Credential:  CONFIGURED
 Service:     ${SERVICE_STATE^^}
 Waiting for cloud sync... The device shows ONLINE on the website only after its first authenticated /api/pi/sync.
========================================
EOF
"""

README = """EMS Pi provisioning package
===========================
Generated: {generated}
Device:    {name}
Device ID: {device_id}
Key ID:    {key_id}
API URL:   {api_url}

This package contains a LIVE device secret (ems-controller.env). Treat the ZIP as confidential,
copy it only to the target Raspberry Pi and delete it afterwards. Downloading a new package from
the EMS Cloud rotates the key: this package then stops working.

Install (on the Pi):
  1. unzip ems-pi-provisioning-*.zip && cd ems-pi-provisioning
  2. Data disk must be mounted at /mnt/ems-data (existing ext4 device, never formatted):
       EMS_DATA_DEVICE=/dev/disk/by-id/<partition> sudo -E ./install.sh
     or, if /mnt/ems-data is already prepared:  sudo ./install.sh
  3. The installer prints Device ID / API / Credential: CONFIGURED / Service state.
     The website shows ONLINE only after the first authenticated sync.

Files: install.sh, ems-controller.env, firmware/ (existing EMS firmware), systemd/ (existing units).
"""


def build_provisioning_zip(device_id: str, device_name: str, key_id: str, api_key: str, api_url: str) -> bytes:
    env = f"EMS_API_URL={api_url}\nEMS_DEVICE_ID={device_id}\nEMS_API_KEY={api_key}\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        def add(arcname: str, data: bytes, mode: int = 0o644):
            info = zipfile.ZipInfo(f"{ROOT}/{arcname}", date_time=datetime.now(timezone.utc).timetuple()[:6])
            info.external_attr = (0o100000 | mode) << 16
            z.writestr(info, data)
        add("install.sh", INSTALL_SH.encode(), 0o755)
        add("ems-controller.env", env.encode(), 0o600)
        add("README.txt", README.format(generated=datetime.now(timezone.utc).isoformat(timespec="seconds"), name=device_name,
                                        device_id=device_id, key_id=key_id, api_url=api_url).encode())
        for f in FIRMWARE_FILES:
            add(f"firmware/{f}", (FIRMWARE_DIR / f).read_bytes(), 0o755 if f.endswith(".sh") else 0o644)
        for f in SYSTEMD_FILES:
            add(f"systemd/{f}", (FIRMWARE_DIR / f).read_bytes())
    return buf.getvalue()
