#!/bin/bash
set -euo pipefail

# EMS Industrial Pi storage preparation.
# Safety rule: EMS_DATA_DEVICE is mandatory and is NEVER formatted.
DATA_MOUNT="/mnt/ems-data"
FSTAB="/etc/fstab"
JOURNAL_CONF="/etc/systemd/journald.conf.d/ems.conf"
DEVICE="${EMS_DATA_DEVICE:-}"

if [[ -z "$DEVICE" ]]; then
  echo "ERROR: set EMS_DATA_DEVICE to an existing ext4 device/by-id path." >&2
  echo "Example: EMS_DATA_DEVICE=/dev/disk/by-id/usb-...-part1 sudo -E $0" >&2
  exit 2
fi

if [[ ! -e "$DEVICE" ]]; then
  echo "ERROR: EMS_DATA_DEVICE does not exist: $DEVICE" >&2
  exit 3
fi

UUID="$(blkid -s UUID -o value "$DEVICE" 2>/dev/null || true)"
FSTYPE="$(blkid -s TYPE -o value "$DEVICE" 2>/dev/null || true)"
if ! getent group pi >/dev/null 2>&1; then
  echo "ERROR: required OS group 'pi' does not exist." >&2
  exit 4
fi

if [[ "$FSTYPE" != "ext4" || -z "$UUID" ]]; then
  echo "ERROR: EMS data device must already contain an ext4 filesystem with a UUID." >&2
  echo "Device=$DEVICE UUID=${UUID:-<none>} FSTYPE=${FSTYPE:-<none>}" >&2
  echo "NO FORMAT OPERATION WILL BE PERFORMED." >&2
  exit 5
fi

install -d -m 0755 "$DATA_MOUNT" /etc/systemd/journald.conf.d

# Remove only EMS-owned blocks; do not alter unrelated fstab entries.
sed -i '/^# EMS Industrial Tmpfs Start$/,/^# EMS Industrial Tmpfs End$/d' "$FSTAB"
sed -i '/^# EMS Industrial Data Start$/,/^# EMS Industrial Data End$/d' "$FSTAB"

cat >> "$FSTAB" <<EOF
# EMS Industrial Tmpfs Start
tmpfs /var/log tmpfs defaults,noatime,nosuid,nodev,mode=0755,size=32M 0 0
tmpfs /var/tmp tmpfs defaults,noatime,nosuid,nodev,mode=1777,size=16M 0 0
tmpfs /tmp tmpfs defaults,noatime,nosuid,nodev,mode=1777,size=32M 0 0
# EMS Industrial Tmpfs End
# EMS Industrial Data Start
UUID=$UUID $DATA_MOUNT ext4 defaults,noatime,commit=60,data=ordered 0 2
# EMS Industrial Data End
EOF

cat > "$JOURNAL_CONF" <<'EOF'
[Journal]
Storage=volatile
RuntimeMaxUse=10M
ForwardToSyslog=no
EOF

# Service runs as pi and therefore needs group-write access.
chown root:pi "$DATA_MOUNT"
chmod 0770 "$DATA_MOUNT"

install -m 0644 "$(dirname "$0")/ems-controller.service" /etc/systemd/system/ems-controller.service
systemctl daemon-reload
systemctl restart systemd-journald || true
mount "$DATA_MOUNT" 2>/dev/null || mount -a

if ! mountpoint -q "$DATA_MOUNT"; then
  echo "ERROR: $DATA_MOUNT is not mounted." >&2
  exit 5
fi
MOUNT_UUID="$(findmnt -no UUID "$DATA_MOUNT" 2>/dev/null || true)"
MOUNT_FSTYPE="$(findmnt -no FSTYPE "$DATA_MOUNT" 2>/dev/null || true)"
if [[ "$MOUNT_UUID" != "$UUID" || "$MOUNT_FSTYPE" != "ext4" ]]; then
  echo "ERROR: mounted filesystem does not match requested EMS device." >&2
  exit 6
fi

echo "EMS storage preparation complete: UUID=$UUID mounted at $DATA_MOUNT"
