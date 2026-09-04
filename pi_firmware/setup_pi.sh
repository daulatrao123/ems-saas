#!/bin/bash

# ================================================================
# INDUSTRIAL PI OS HARDENING (10-Year Flash Protection)
# ================================================================

echo "Starting Industrial Pi Hardening..."

# 1. DISABLE SWAP COMPLETELY
# Swap writes to the USB drive when RAM is full. For an industrial controller, 
# it is better to crash/restart than to silently destroy the USB drive.
echo "Disabling Swap..."
sudo dphys-swapfile swapoff
sudo dphys-swapfile uninstall
sudo systemctl disable dphys-swapfile

# 2. MOVE /var/log TO TMPFS (RAM)
# The OS writes thousands of logs to /var/log. We mount this in RAM so it 
# disappears on reboot, saving the USB drive from OS log wear.
echo "Moving OS logs to RAM (tmpfs)..."
sudo tee -a /etc/fstab > /dev/null <<EOL
# Industrial EMS Tmpfs Mounts
tmpfs /var/log tmpfs defaults,noatime,nosuid,mode=0755,size=32M 0 0
tmpfs /var/tmp tmpfs defaults,noatime,nosuid,mode=1777,size=16M 0 0
tmpfs /tmp tmpfs defaults,noatime,nosuid,mode=1777,size=32M 0 0
EOL

# 3. MOUNT USB DRIVE WITH FLASH-FRIENDLY FLAGS
# noatime stops the OS from updating file access times (massive write reduction).
# commit=60 syncs data to disk every 60 seconds instead of every 5 seconds.
echo "Configuring USB mount flags..."
# Find the USB drive UUID (assuming it's formatted as ext4)
USB_UUID=$(sudo blkid | grep /dev/sda1 | grep -o 'UUID="[^"]*"' | cut -d'"' -f2)
if [ ! -z "$USB_UUID" ]; then
    # Remove old ems-data mount if it exists
    sudo sed -i '/\/mnt\/ems-data/d' /etc/fstab
    # Add optimized mount
    echo "UUID=$USB_UUID /mnt/ems-data ext4 defaults,noatime,commit=60,data=ordered 0 1" | sudo tee -a /etc/fstab
else
    echo "WARNING: Could not find /dev/sda1 UUID. Please configure /etc/fstab manually."
fi

# 4. REDUCE JOURNALD WEAR
# Even in RAM, limit journald so it doesn't eat all memory.
echo "Configuring journald..."
sudo mkdir -p /etc/systemd/journald.conf.d/
sudo tee /etc/systemd/journald.conf.d/ems.conf > /dev/null <<EOL
[Journal]
Storage=volatile
RuntimeMaxUse=10M
ForwardToSyslog=no
EOL

echo "Hardening Complete. Please REBOOT the Pi now."