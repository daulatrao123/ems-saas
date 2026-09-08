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

Each channel contains:

1. Relay output
2. Physical toggle input
3. Contactor detect/feedback input

Total:

4 relay outputs
4 toggle inputs
4 detect/feedback inputs

Total GPIO signals = 12.

---

# 2. AUTHORITATIVE GPIO MAP

| Channel | Relay OUT | Toggle IN | Detect IN |
|---------|-----------|-----------|-----------|
| A       | GPIO17    | GPIO5     | GPIO19    |
| B       | GPIO27    | GPIO6     | GPIO16    |
| C       | GPIO23    | GPIO13    | GPIO20    |
| D       | GPIO22    | GPIO12    | GPIO21    |

BCM GPIO numbers:

A:
- relay = 17
- toggle = 5
- detect = 19

B:
- relay = 27
- toggle = 6
- detect = 16

C:
- relay = 23
- toggle = 13
- detect = 20

D:
- relay = 22
- toggle = 12
- detect = 21

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

# 4. TOGGLE INPUT

Toggle inputs are logically ACTIVE-LOW.

Expected electrical arrangement:

GPIO input
+
internal pull-up
+
external switch/contact to GND

Therefore:

GPIO HIGH -> toggle OFF
GPIO LOW  -> toggle ON

Firmware should use:

Button(
    pin,
    pull_up=True,
    bounce_time=0.05
)

Toggle polarity MUST remain independent from relay polarity.

A relay being active-low does NOT mean the toggle must automatically
use the same GPIO polarity.

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

# 8. PHYSICAL TOGGLE BEHAVIOUR

A physical toggle is a real control input.

Firmware MUST continuously monitor all four toggle inputs:

A = GPIO5
B = GPIO6
C = GPIO13
D = GPIO12

Toggle state changes MUST generate a software event.

Expected behaviour:

Toggle ON:
- request corresponding channel ON.

Toggle OFF:
- request corresponding channel OFF.

If another channel is active:
- perform normal break-before-make transition.

If toggle operation conflicts with safety/interlock rules:
- reject operation and enter/retain FAULT as appropriate.

Physical toggle operation MUST NOT directly bypass the command/interlock
state machine.

---

# 9. TOGGLE DEBOUNCE

Mechanical toggle transitions must be debounced.

Recommended:

bounce_time = 0.05 seconds

A toggle transition must not generate repeated commands because of contact
bounce.

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

relay outputs
toggle inputs
detect inputs

Current authoritative allocation:

17 relay A
5  toggle A
19 detect A

27 relay B
6  toggle B
16 detect B

23 relay C
13 toggle C
20 detect C

22 relay D
12 toggle D
21 detect D

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
        "toggle_active_low": True,
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
3. Toggle inactive reads HIGH.
4. Toggle active reads LOW.
5. Detect inactive reads HIGH.
6. Detect active reads LOW.
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
- toggle A event
- toggle B event
- toggle C event
- toggle D event
- toggle debounce
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

Implemented by `test_reports/hw_source_of_truth_test.py` (mock gpiozero; run alone).

---

# FINAL HARDWARE DEFINITION

4 CHANNELS:

A = Relay17 / Toggle5 / Detect19
B = Relay27 / Toggle6 / Detect16
C = Relay23 / Toggle13 / Detect20
D = Relay22 / Toggle12 / Detect21

Relay = ACTIVE-LOW
Toggle = ACTIVE-LOW
Detect = ACTIVE-LOW

12 unique GPIO signals.

This document is the single source of truth.
