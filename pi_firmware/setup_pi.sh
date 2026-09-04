#!/bin/bash
# ================================================================
# INDUSTRIAL PI OS HARDENING (10-Year Flash Protection)
# ================================================================
echo "Starting Industrial Pi Hardening..."

# 1. DISABLE SWAP COMPLETELY
echo "Disabling Swap..."
sudo dphys-swapfile swapoff
sudo dphys-swapfile uninstall
sudo systemctl disable dphys-swapfile

# 2. MOVE OS LOGS TO RAM (tmpfs)
echo "Moving OS logs to RAM (tmpfs)..."
sudo tee -a /etc/fstab > /dev/null <<EOL
# Industrial EMS Tmpfs Mounts
tmpfs /var/log tmpfs defaults,noatime,nosuid,mode=0755,size=32M 0 0
tmpfs /var/tmp tmpfs defaults,noatime,nosuid,mode=1777,size=16M 0 0
tmpfs /tmp tmpfs defaults,noatime,nosuid,mode=1777,size=32M 0 0
EOL

# 3. MOUNT USB DRIVE WITH FLASH-FRIENDLY FLAGS
echo "Configuring USB mount flags..."
USB_UUID=$(sudo blkid | grep /dev/sda1 | grep -o 'UUID="[^"]*"' | cut -d'"' -f2)
if [ ! -z "$USB_UUID" ]; then
    sudo sed -i '/\/mnt\/ems-data/d' /etc/fstab
    echo "UUID=$USB_UUID /mnt/ems-data ext4 defaults,noatime,commit=60,data=ordered 0 1" | sudo tee -a /etc/fstab
else
    echo "WARNING: Could not find /dev/sda1 UUID. Configure /etc/fstab manually."
fi

# 4. REDUCE JOURNALD WEAR
echo "Configuring journald..."
sudo mkdir -p /etc/systemd/journald.conf.d/
sudo tee /etc/systemd/journald.conf.d/ems.conf > /dev/null <<EOL
[Journal]
Storage=volatile
RuntimeMaxUse=10M
ForwardToSyslog=no
EOL

echo "Hardening Complete. Please REBOOT the Pi now."