# EMS Real Raspberry Pi Field Test Checklist

Status: **FIELD TEST REQUIRED — NOT YET PERFORMED.** All automated tests use mocked GPIO/smartctl.
Do not tick any box without a physical Pi, relay board and (optionally) feedback wiring.

Hardware contract (unchanged): relays A/B/C/D = GPIO 17/27/22/23, feedback A/B/C/D = GPIO 5/6/13/19.
Effective feedback = device `feedback_hardware_installed` AND slot `feedback_enabled`. No manual-toggle GPIO exists.

| # | Step | Expected evidence | Pass |
|---|------|-------------------|------|
| 1 | Super Admin → society → Provisioning Center → DOWNLOAD PI PROVISIONING ZIP (confirm rotation) | ZIP downloaded; audit `PROVISIONING_PACKAGE` has device_id + key_id only | ☐ |
| 2 | On the Pi: unzip; `EMS_DATA_DEVICE=/dev/disk/by-id/<ext4> sudo -E ./install.sh` | Prints Device ID, API `https://ems-saass.onrender.com/api`, `Credential: CONFIGURED`, `Service: ACTIVE`; key never printed | ☐ |
| 3 | `cat /etc/ems/ems-controller.env` (root) | `EMS_DEVICE_ID` equals the registered UUID; file mode 0600 | ☐ |
| 4 | `journalctl -u ems-controller -n 50` | First `/api/pi/sync` HTTP 200; no `PI_AUTH_REJECTED` for this device in backend logs | ☐ |
| 5 | Website → society → Operations | Device card ONLINE, `Last sync` seconds ago | ☐ |
| 6 | Same page | Firmware version shown equals the Pi's `firmwareVersion` | ☐ |
| 7 | Slot A card | target/used days match backend `slot_configs`; `config APPLIED` badge after convergence | ☐ |
| 8 | Slot A → ACTIVATE (`set_active_slot`) | Last Response: DELIVERED → EXECUTING | ☐ |
| 9 | Observe relay A (GPIO 17) | Relay physically energises (LED/click/meter) | ☐ |
| 10 | If feedback installed + slot feedback enabled: contactor closes, GPIO 5 changes | Result `VERIFIED_ON`; without feedback hardware result is `GPIO_CONFIRMED` | ☐ |
| 11 | Backend `pi_commands` row | `hardware_verified_at` set with a positive verification token | ☐ |
| 12 | Last Response | Status COMPLETED **only after** HW VERIFIED timestamp; never before | ☐ |
| 13 | Slot A → DEACTIVATE (`off_slot`) | Relay A de-energises | ☐ |
| 14 | Feedback | Result `VERIFIED_OFF` (or `GPIO_CONFIRMED` without feedback); PHYSICAL shows OFF only from Pi report | ☐ |
| 15 | System Controls → RESTART | Result `RESTART_SCHEDULED`; service restarts via systemd; comes back ONLINE | ☐ |
| 16 | After restart | Slot state/used days identical to before; no command re-executed; logs show state recovery, no `UNKNOWN_AFTER_REBOOT` false completion | ☐ |
| 17 | Disconnect network, issue ACTIVATE B from the website, reconnect | Command stays queued/expired on cloud honestly; nothing executes twice | ☐ |
| 18 | While offline, physical operation continues; reconnect | Offline queue acks flush; `executed_command_ids` reconciles; sequence order preserved | ☐ |
| 19 | System Controls → REBOOT PI | Result `REBOOT_SCHEDULED`; `/mnt/ems-data/reboot.request` consumed; Pi reboots once (no loop); back ONLINE | ☐ |
| 20 | Storage badge + `/mnt/ems-data/health/` | storage OK; writes remain hourly/daily (no 60 s persistence) | ☐ |
| 21 | `/mnt/ems-data/health/smart.json` | `smart: AVAILABLE/UNAVAILABLE`, health OK/UNKNOWN; no controller impact when UNAVAILABLE (SD cards) | ☐ |
| 22 | `grep -R <api key> /var/log/supervisor /var/log/journal` (backend + Pi) | No hits | ☐ |
| 23 | Second Pi in the same society | Its ONLINE/OFFLINE, commands and config are independent; Pi-1 key rejected as Pi-2 (`PI_AUTH_REJECTED`) | ☐ |

Sign-off: ______________________  Date: __________  Pi model/serial: __________________
