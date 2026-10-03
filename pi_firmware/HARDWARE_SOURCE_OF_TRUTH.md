# EMS 4-CHANNEL HARDWARE SOURCE OF TRUTH

## STATUS

This file is the authoritative hardware definition for the EMS 4-channel
Raspberry Pi controller.

All firmware, provisioning, backend configuration and hardware-related
documentation MUST follow this file.

DO NOT invent GPIO mappings elsewhere.

GPIO numbering is BCM GPIO numbering, NOT physical header pin numbering.

---

# 1. CHANNEL ARCHITECTURE

The EMS controller has exactly 4 channels:

A
B
C
D

Each channel contains three recorded assignments:

1. Production relay output. GPIOManager opens this pin.
2. Retired/reserved toggle input. Recorded so the pin cannot be reused.
   GPIOManager does not open it. It is not a production control input.
3. Production contactor detect/feedback input. GPIOManager opens this pin.

Total recorded assignments:

4 production relay outputs
4 retired/reserved toggle inputs
4 production detect/feedback inputs

Total recorded GPIO signals = 12.
Production GPIOManager opens exactly 8 of them: the 4 relays and the 4 feedback inputs.

---

# 2. AUTHORITATIVE GPIO MAP

| Channel | Production relay OUT | Retired/reserved toggle | Production feedback IN |
|---------|----------------------|-------------------------|------------------------|
| A       | GPIO17               | GPIO5                   | GPIO19                 |
| B       | GPIO27               | GPIO6                   | GPIO16                 |
| C       | GPIO23               | GPIO13                  | GPIO20                 |
| D       | GPIO22               | GPIO12                  | GPIO21                 |

BCM GPIO numbers:

A:
- production relay = 17
- retired/reserved toggle = 5
- production feedback = 19

B:
- production relay = 27
- retired/reserved toggle = 6
- production feedback = 16

C:
- production relay = 23
- retired/reserved toggle = 13
- production feedback = 20

D:
- production relay = 22
- retired/reserved toggle = 12
- production feedback = 21

GPIOManager opens only:

17, 27, 23, 22, 19, 16, 20, 21

LCD I2C remains BCM 2 (SDA) and BCM 3 (SCL). Those pins are not EMS channel pins.

---

# 3. RELAY POLARITY

Relay outputs are ACTIVE-LOW.

Therefore:

GPIO LOW  -> relay ON
GPIO HIGH -> relay OFF

Firmware MUST initialize relays using:

OutputDevice(
    pin,
    active_high=False,
    initial_value=False
)

Do NOT use:

OutputDevice(pin)

because implicit/default polarity is not acceptable for this hardware.

All relays MUST start OFF.

---

# 4. RETIRED / RESERVED TOGGLE INPUT

These pins are retired. They are not production control inputs.

BCM assignment, retained so the pin cannot be given to another function:

A = GPIO5
B = GPIO6
C = GPIO13
D = GPIO12

The profile still records `toggle_active_low: false` for this historical
assignment. That flag does not make the pin live.

GPIOManager MUST NOT open these pins.
GPIOManager.toggles stays empty.
process_toggle_events() discards edges and returns 0.
A level on these pins MUST NOT energize a relay, clear FAULT, or request a channel.

The input diagnostic reports each pin as:

RESERVED / RETIRED / NOT USED BY CONTROLLER

and does not open it. There is no maintenance mode that claims these pins.

Do not attach a new switch to these pins and expect the controller to read it.

---

# 5. CONTACTOR DETECT / FEEDBACK

Detect inputs are logically ACTIVE-LOW.

Expected electrical arrangement:

GPIO input
+
internal pull-up
+
isolated contact/feedback signal pulling GPIO to GND when contactor is ON.

Therefore:

GPIO HIGH -> contactor OFF
GPIO LOW  -> contactor ON

Firmware should use an input configuration equivalent to:

Button(
    pin,
    pull_up=True,
    bounce_time=0.05
)

IMPORTANT:

Detect polarity is independent from relay polarity.

If the actual electrical feedback circuit is ACTIVE-HIGH, this source of
truth MUST be updated before deployment rather than silently inverting
software logic.

---

# 6. SAFETY INTERLOCK

At most ONE relay may be ON at any time.

At most ONE contactor may report ON at any time.

If multiple relay outputs are detected ON:

SYSTEM = FAULT

If multiple contactor feedback inputs are ON:

SYSTEM = FAULT

If commanded relay state and enabled physical feedback disagree:

SYSTEM = FAULT

Never automatically continue operation after an interlock violation.

---

# 7. BREAK-BEFORE-MAKE

Changing channel:

A -> B
B -> C
C -> D
D -> A
etc.

MUST follow:

1. Turn current relay OFF.
2. Update commanded state OFF.
3. If feedback is enabled, wait for physical OFF confirmation.
4. If physical OFF is not confirmed within timeout -> FAULT.
5. Wait configured interlock delay.
6. Turn target relay ON.
7. Update commanded state ON.
8. If feedback is enabled, wait for physical ON confirmation.
9. If physical ON is not confirmed within timeout -> turn relay OFF and FAULT.
10. Only then mark target channel active.

Never make the new relay before the previous relay is safely OFF.

---

# 8. RETIRED TOGGLE BEHAVIOUR

The retired toggle pins are not monitored and are not a control path.

A = GPIO5
B = GPIO6
C = GPIO13
D = GPIO12

The controller does not generate toggle ON or toggle OFF requests from these
pins. Channel changes come from the cloud command path and the energy
allocator, both of which still pass through break-before-make and the FAULT
gate.

A retired pin MUST NOT bypass the command/interlock state machine, because
the controller does not read it.

---

# 9. RETIRED TOGGLE DEBOUNCE

No production debounce applies. GPIOManager does not register a callback on
BCM 5, 6, 13, or 12, so contact bounce on those pins cannot generate a command.

---

# 10. FEEDBACK DISABLED

If feedback hardware is not installed/configured for a channel:

Physical state MUST be:

UNKNOWN

Do NOT report UNKNOWN as OFF.

Do NOT claim physical verification.

Software GPIO state may still be reported as commanded/GPIO-confirmed,
but this must not be represented as physical contactor confirmation.

---

# 11. GPIO UNIQUENESS

Every GPIO must be unique across:

production relay outputs
retired/reserved toggle inputs
production feedback inputs

Current authoritative allocation:

17 production relay A
5  retired/reserved toggle A
19 production feedback A

27 production relay B
6  retired/reserved toggle B
16 production feedback B

23 production relay C
13 retired/reserved toggle C
20 production feedback C

22 production relay D
12 retired/reserved toggle D
21 production feedback D

No GPIO may be assigned to two functions.

---

# 12. FORBIDDEN CHANGES

Do NOT:

- invent new GPIO numbers
- reuse a GPIO
- change relay polarity implicitly
- assume relay polarity applies to inputs
- bypass the interlock
- energize multiple relays
- automatically probe unknown GPIOs by switching outputs
- make physical GPIO changes from cloud configuration
- silently change this mapping in config.py

If hardware mapping needs to change, this file must be changed first and
all dependent code must be updated.

---

# 13. CODE ARCHITECTURE REQUIREMENT

Hardware mapping must exist in ONE authoritative configuration object.

Recommended structure:

HARDWARE_PROFILES = {
    "EMS-4CH-v1": {
        "channels": {
            "A": {
                "relay_gpio": 17,
                "toggle_gpio": 5,
                "detect_gpio": 19,
            },
            "B": {
                "relay_gpio": 27,
                "toggle_gpio": 6,
                "detect_gpio": 16,
            },
            "C": {
                "relay_gpio": 23,
                "toggle_gpio": 13,
                "detect_gpio": 20,
            },
            "D": {
                "relay_gpio": 22,
                "toggle_gpio": 12,
                "detect_gpio": 21,
            },
        },
        "relay_active_low": True,
        "toggle_active_low": False,
        "detect_active_low": True,
    }
}

No other source should redefine these GPIO numbers.

---

# 14. SOURCE-OF-TRUTH RULE

If any existing file contains a conflicting GPIO mapping:

DO NOT preserve the conflicting mapping.

Update that file to use this source of truth.

Known historical naming:

Old third channel was called "G".

For the current 4-channel product it is renamed:

G -> C

Therefore:

old G relay GPIO23 -> current C relay GPIO23
old G toggle GPIO13 -> current C toggle GPIO13
old G detect GPIO20 -> current C detect GPIO20

The fourth channel D uses:

relay GPIO22
toggle GPIO12
detect GPIO21

---

# 15. HARDWARE VALIDATION

Before production deployment verify electrically:

1. Relay GPIO LOW activates relay.
2. Relay GPIO HIGH deactivates relay.
3. Retired toggle GPIOs 5/6/13/12 are not opened by the controller or by the input diagnostic.
4. Do not validate those retired pins as live switches.
5. Feedback inactive reads HIGH.
6. Feedback active reads LOW.
7. No GPIO is electrically shorted to another GPIO.
8. Inputs use appropriate isolation/protection for the installed contactor
   feedback circuit.
9. Industrial contactor testing is performed with appropriate safety
   isolation/E-stop procedures.

If electrical behaviour differs from this document:

STOP.

Do not compensate by randomly changing software polarity.

Update this source-of-truth after the electrical circuit is confirmed.

---

# 16. REQUIRED TEST CASES

Firmware tests MUST cover:

- all 4 channels exist
- all 12 GPIOs are unique
- relay active-low
- relay startup OFF
- retired toggles 5/6/13/12 are reserved and are not opened by GPIOManager
- retired toggle events do not energize a relay or clear FAULT
- the input diagnostic does not open retired toggle pins
- detect A
- detect B
- detect C
- detect D
- A -> B transition
- B -> C transition
- C -> D transition
- D -> A transition
- multiple relay ON => FAULT
- multiple detect ON => FAULT
- relay/detect mismatch => FAULT
- feedback disabled => UNKNOWN
- failed feedback ON confirmation => FAULT
- failed feedback OFF confirmation => FAULT
- break-before-make is enforced
- feedback remains active-low with pull-up
- GPIOManager opens exactly 17, 27, 23, 22, 19, 16, 20, 21
- LCD BCM 2/3 and UART/SPI/ID EEPROM pins stay unassigned
- GPIO initialisation failure -> FAULT, relays OFF, hardware_fault reported

Implemented by `qa/hw_source_of_truth_test.py` (mock gpiozero; run alone).

---

# 17. PHYSICAL PRESENCE

The retired toggle pins are not a presence input. GPIOManager does not read
them, and firmware MUST NOT derive "hardware present" or a channel request
from BCM 5, 6, 13, or 12.

Contactor feedback reports the contactor auxiliary contact. It is not a wing
presence strap. Firmware MUST NOT probe by driving outputs. "Logical slot
enabled" is an explicit administrative setting (cloud `slot_configs.disabled`),
separate from contactor feedback.

If true presence detection is required, the hardware must add one of:

- a per-wing presence strap to a dedicated GPIO input (pull-up, strap to GND = present)
- a 3-position input (OFF / ON / ABSENT) per wing
- an ADC / resistor-ladder wing-ID input

Such a signal must be added to this document first, then to the firmware.

---

# 18. DEVICE HARDWARE CAPABILITIES

`hardware_profile` remains the firmware GPIO map name `EMS-4CH-v1`.
It is not editable from the website and it does not change per installation.

`hardware_capabilities` is a separate versioned document (`capability_version` 1).
It records which optional hardware is physically installed. It does not select
AUTO, MANUAL, or DAY_BASED, and it does not carry GPIO numbers.

States:

- NOT_INSTALLED: the part is absent. This is not a fault.
- INSTALLED_DISABLED: the part is present and turned off in configuration.
- INSTALLED_ENABLED: the part is present and allowed to run.

Runtime health is separate: HEALTHY, OFFLINE, FAULT, UNKNOWN, NOT_APPLICABLE.

Contactor feedback not installed: physical verification is unavailable.
The UI must not show VERIFIED from the commanded relay. Installed feedback
that stays unreadable until the existing feedback budget expires is still a
fail-safe fault. One inconclusive sample is qualified again inside that
budget. A stable mismatch faults on that reading. Relay GPIO, polarity, and
break-before-make deadtime are unchanged.

Meters stay M1 generation and M2–M5 consumption for wings A–D.
A meter that is not installed does not produce a communication fault and
does not supply another wing's reading. DAY_BASED allocation does not
require meters.

Migration: existing `feedback_hardware_installed` is copied. A meter is installed
only when `energy_meters.enabled` is true. A serial number is not installation.
`enabled=false` stays not installed even when a serial exists. Serial values
are not deleted. LCD starts unspecified (`installed` null), so the controller
keeps its current display until an administrator sets it. `installed` false
stops display writes. `installed` true follows the existing LCD enable switch.

A missing capability document is legacy: the controller keeps the canonical
feedback flag. A stored document that cannot be read is not legacy. The
controller reports `CAPABILITY_STATE_INVALID`, treats optional hardware as
unavailable, and does not turn feedback or meters back on from the old flag.

`hardware_capabilities.contactor_feedback` is the only authoritative record
of physical feedback installation. `pi_devices.feedback_hardware_installed`
and `slot_configs.feedback_enabled` are compatibility fields derived from
that document. Registration, society assignment, `POST /feedback-hardware`,
and `PUT /hardware-capabilities` all update the document through one helper.

The Pi stores the canonical config and the capability document in one SQLite
transaction (`synchronous=FULL`). A power loss keeps the previous pair or
commits the new pair. It does not commit one without the other. A repeated
sync of the same documents does not rewrite flash. A capability-only change
applies without bumping the canonical config version.

Expected state is `NOT_INSTALLED` or `INSTALLED`. Runtime state is
`NOT_APPLICABLE`, `HEALTHY`, `OFFLINE`, `FAULT`, or `UNKNOWN`.
`NOT_INSTALLED` is not `FAULT` and is not `VERIFIED`.

New hardware-capability firmware requires a compatible provisioning package
or a future multi-file/release OTA. The current OTA stages only
`ems_controller.py`. It cannot deploy `hardware_capabilities.py`,
`offline_queue.py`, or `lcd_display.py`. Do not activate this controller
through the current single-file OTA unless those files are already present
under `/opt/ems/pi_firmware`.

The cloud stores the document on `pi_devices.hardware_capabilities`.
Super Admin is the only role that can change it (`PUT` requires confirmation
when contactor feedback installation changes). The change is written to
`audit_log` as `HARDWARE_CAPABILITIES_SET`. The Pi receives it on the
existing `POST /api/pi/sync` reply. An invalid document is rejected and the
previous valid document stays in force. An unchanged hash is not rewritten.

Unknown JSON keys are ignored. A different `capability_version` is rejected.

---

# FINAL HARDWARE DEFINITION

4 CHANNELS:

A = production relay 17 / retired toggle 5 / production feedback 19
B = production relay 27 / retired toggle 6 / production feedback 16
C = production relay 23 / retired toggle 13 / production feedback 20
D = production relay 22 / retired toggle 12 / production feedback 21

Relay = ACTIVE-LOW
Feedback = ACTIVE-LOW, pull-up, LOW = contactor ON
Retired toggles = reserved, not opened, not production control inputs

GPIOManager opens exactly: 17, 27, 23, 22, 19, 16, 20, 21
LCD I2C: SDA 2, SCL 3

12 unique recorded GPIO signals. 8 are opened in production.

This document is the single source of truth.
