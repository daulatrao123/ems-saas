#!/usr/bin/env python3
"""EMS input-only GPIO diagnostic (field safe).

Reads the 4 toggle inputs and the 4 contactor-detect inputs from the authoritative hardware
profile and prints raw level + logical meaning. It NEVER constructs an OutputDevice, never
drives any pin, never touches the relay GPIOs (17/27/23/22) and never probes unknown pins.

Run in the SAME environment as the production service (lgpio pin factory, writable cwd):

    cd /mnt/ems-data && GPIOZERO_PIN_FACTORY=lgpio python3 /opt/ems/pi_firmware/gpio_input_diag.py

If GPIOZERO_PIN_FACTORY is not set this script sets it to lgpio itself, so a stray run from
/tmp does not fall back to the sysfs/native factory (which is what produced the misleading
'/sys/class/gpio/gpio5/value' failure earlier).
"""
import os
import sys
import time

os.environ.setdefault("GPIOZERO_PIN_FACTORY", "lgpio")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import HARDWARE_PROFILES  # noqa: E402

RELAY_PINS = {ch["relay_gpio"] for p in HARDWARE_PROFILES.values() for ch in p["channels"].values()}


def read_inputs(profile_name="EMS-4CH-v1", button_cls=None, samples=3, interval_s=0.05):
    """Return {"toggles": {slot: {...}}, "detects": {slot: {...}}} using input devices only."""
    if button_cls is None:
        from gpiozero import Button as button_cls  # noqa: N813  (import here: no output classes)

    profile = HARDWARE_PROFILES[profile_name]
    toggle_active_low = bool(profile["toggle_active_low"])
    detect_active_low = bool(profile["detect_active_low"])
    report = {"toggles": {}, "detects": {}}
    devices = []
    try:
        for slot, ch in profile["channels"].items():
            for role, pin, active_low, key in (
                ("toggle", ch["toggle_gpio"], toggle_active_low, "toggles"),
                ("detect", ch["detect_gpio"], detect_active_low, "detects"),
            ):
                assert pin not in RELAY_PINS, f"refusing to touch relay GPIO{pin}"
                btn = button_cls(pin, pull_up=active_low, bounce_time=None)
                devices.append(btn)
                reads = []
                for _ in range(samples):
                    reads.append(bool(btn.is_pressed))
                    time.sleep(interval_s)
                active = all(reads)
                stable = len(set(reads)) == 1
                level = ("LOW" if active else "HIGH") if active_low else ("HIGH" if active else "LOW")
                report[key][slot] = {
                    "gpio": pin,
                    "polarity": "ACTIVE-LOW" if active_low else "ACTIVE-HIGH",
                    "level": level if stable else "UNSTABLE",
                    "logical": ("ON" if active else "OFF") if stable else "UNKNOWN",
                }
    finally:
        for d in devices:
            try:
                d.close()
            except Exception:
                pass
    return report


def main():
    print(f"pin factory: {os.environ.get('GPIOZERO_PIN_FACTORY')}  cwd: {os.getcwd()}")
    print("INPUT-ONLY diagnostic: relays (GPIO 17/27/23/22) are never constructed or driven.\n")
    try:
        report = read_inputs()
    except Exception as exc:  # report, do not mask: this is the GPIO_INIT_FAILED cause
        print(f"GPIO INPUT INIT FAILED: {type(exc).__name__}: {exc}")
        print("Check: service env GPIOZERO_PIN_FACTORY=lgpio, writable cwd (/mnt/ems-data), user in group gpio.")
        return 2
    print(f"{'ROLE':7} {'SLOT':4} {'GPIO':>4}  {'POLARITY':11} {'LEVEL':8} LOGICAL")
    for slot, r in report["toggles"].items():
        print(f"{'toggle':7} {slot:4} {r['gpio']:>4}  {r['polarity']:11} {r['level']:8} {r['logical']}")
    for slot, r in report["detects"].items():
        print(f"{'detect':7} {slot:4} {r['gpio']:>4}  {r['polarity']:11} {r['level']:8} {r['logical']}")
    print("\nNOTE: a LOW toggle means OFF *or* not wired; this tool cannot detect physical presence.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
