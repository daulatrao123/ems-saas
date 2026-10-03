# EMS Real Raspberry Pi Field Test Checklist

Status: **FIELD TEST REQUIRED — NOT YET PERFORMED.** All automated tests use mocked GPIO/smartctl.
Do not tick any box without a physical Pi, relay board and (optionally) feedback wiring.

Hardware contract: see `HARDWARE_SOURCE_OF_TRUTH.md` (BCM). Relays A/B/C/D = GPIO 17/27/23/22 (ACTIVE-LOW: pin LOW = relay ON),
toggles A/B/C/D = GPIO 5/6/13/12 (ACTIVE-HIGH: pull-down, HIGH = ON, LOW = OFF-or-unwired), detect/feedback A/B/C/D = GPIO 19/16/20/21 (pull-up, LOW = contactor ON).
Effective feedback = device `feedback_hardware_installed` AND slot `feedback_enabled`. Retired toggle pins BCM 5/6/13/12 stay reserved and must not be reused; they do not energize relays. Logical slot enable is admin config (`ENABLE/DISABLE` on the slot card).

Contactor feedback wiring is not approved for a live coil until an electrician confirms the auxiliary contact. Required: isolated dry contact or optocoupler, 3.3 V GPIO logic, internal pull-up, LOW = contactor ON. Do not pull a GPIO up to 5 V. Do not connect contactor coil voltage to a Raspberry Pi pin. Do not run a live coil test from this repository's automated tests; those tests use mocked GPIO.
Feedback diagnostic, only while `ems-controller` is stopped. It opens feedback GPIOs 19/16/20/21 and does not open relays or retired toggles 5/6/13/12: `sudo systemctl stop ems-controller && cd /mnt/ems-data && sudo -u pi GPIOZERO_PIN_FACTORY=lgpio python3 /opt/ems/pi_firmware/gpio_input_diag.py`

| # | Step | Expected evidence | Pass |
|---|------|-------------------|------|
| 1 | Super Admin → society → Provisioning Center → DOWNLOAD PI PROVISIONING ZIP (confirm rotation) | ZIP downloaded; audit `PROVISIONING_PACKAGE` has device_id + key_id + service_sha256 only | ☐ |
| 1b | **Before copying to the Pi:** `unzip -p ems-pi-provisioning-*.zip ems-pi-provisioning/systemd/ems-controller.service \| grep -E '^(WorkingDirectory\|Environment=GPIOZERO_PIN_FACTORY\|DeviceAllow)='` | Exactly `WorkingDirectory=/mnt/ems-data` and `Environment=GPIOZERO_PIN_FACTORY=lgpio`; **zero** `DeviceAllow=` lines (they break lgpio: `can not open gpiochip`). Anything else = stale backend → STOP, redeploy backend, re-download | ☐ |
| 2 | On the Pi: unzip; `EMS_DATA_DEVICE=/dev/disk/by-id/<ext4> sudo -E ./install.sh` | Prints `Package integrity: OK`, `Service unit validation: OK`, Device ID, API `https://ems-saass.onrender.com/api`, `Credential: CONFIGURED`, `Service: ACTIVE` after the 30 s stability window; key never printed | ☐ |
| 2b | `systemctl is-active ems-controller; systemctl show ems-controller -p NRestarts --value` (repeat after 2 min) | `active` and `0` both times; `journalctl -u ems-controller` shows no gpiozero fallback / `/sys/class/gpio` errors | ☐ |
| 3 | `cat /etc/ems/ems-controller.env` (root) | `EMS_DEVICE_ID` equals the registered UUID; file mode 0600 | ☐ |
| 4 | `journalctl -u ems-controller -n 50` | First `/api/pi/sync` HTTP 200; no `PI_AUTH_REJECTED` for this device in backend logs | ☐ |
| 5 | Website → society → Operations | Device card ONLINE, `Last sync` seconds ago | ☐ |
| 6 | Same page | Firmware version shown equals the Pi's `firmwareVersion` | ☐ |
| 7 | Slot A card | target/used days match backend `slot_configs`; `config APPLIED` badge after convergence | ☐ |
| 8 | Slot A → ACTIVATE (`set_active_slot`) | Last Response: DELIVERED → EXECUTING | ☐ |
| 9 | Observe relay A (GPIO 17, pin driven LOW) | Relay physically energises (LED/click/meter); GPIO 17 HIGH again after DEACTIVATE | ☐ |
| 10 | If feedback installed + slot feedback enabled: contactor closes, detect GPIO 19 goes LOW | Result `VERIFIED_ON`; without feedback hardware result is `GPIO_CONFIRMED` and PHYSICAL stays `UNKNOWN` | ☐ |
| 10b | With the controller running, confirm retired toggle pins BCM 5/6/13/12 are not claimed | `gpio_input_diag.py` is not used while the service is up. Snapshot `toggle_input` is null for A–D. Driving BCM 6 does not change the active relay | ☐ |
| 10c | Stop `ems-controller`, run `gpio_input_diag.py`, start the service again | Toggles print `RESERVED / RETIRED / NOT USED BY CONTROLLER` for GPIO 5/6/13/12. Only feedback GPIO 19/16/20/21 are read. Service returns ONLINE and no relay was energized by the diagnostic | ☐ |
| 10e | Super Admin → device → Hardware Configuration. Set the installed parts for this site and save | Pi sync applies the profile without a firmware rebuild. Absent feedback or meters show NOT INSTALLED / NOT AVAILABLE, not FAULT. Installed feedback that cannot be read still faults. Allocation mode is unchanged. There is one hardware editor; the old feedback toggle is read-only | ☐ |
| 10f | Confirm the Pi was installed from a provisioning package that contains `hardware_capabilities.py`, `offline_queue.py`, and `lcd_display.py` | New hardware-capability firmware requires a compatible provisioning package or a future multi-file/release OTA. The current single-file OTA cannot deploy this change | ☐ |
| 10d | Bench: hold two detect inputs LOW at once / one detect LOW with relays OFF | System FAULT, all relays OFF, no automatic recovery until service restart + healthy reconciliation | ☐ |
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
