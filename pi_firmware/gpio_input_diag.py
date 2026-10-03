#!/usr/bin/env python3
"""EMS input diagnostic for production contactor feedback only.

GPIOManager owns the relay outputs and the contactor feedback inputs.
Retired toggle pins stay reserved in the hardware profile and are NOT opened
by this tool or by the controller. There is no second ownership model and no
maintenance mode that claims BCM 5/6/13/12.

This tool opens only the four feedback inputs. Stop ems-controller before
running it. Do not run it while the controller owns those feedback GPIOs.

It NEVER constructs an OutputDevice, never drives a pin, and never touches
the relay GPIOs (17/27/23/22) or the retired toggle GPIOs (5/6/13/12).

Run only after the controller is stopped, in the same environment as the
production service (lgpio pin factory, writable cwd):

    sudo systemctl stop ems-controller
    cd /mnt/ems-data && GPIOZERO_PIN_FACTORY=lgpio python3 /opt/ems/pi_firmware/gpio_input_diag.py
    sudo systemctl start ems-controller

If GPIOZERO_PIN_FACTORY is not set this script sets it to lgpio itself, so a
stray run does not fall back to the sysfs/native factory.
"""
import os
import sys
import time

os.environ.setdefault("GPIOZERO_PIN_FACTORY", "lgpio")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import HARDWARE_PROFILES  # noqa: E402

RELAY_PINS = {ch["relay_gpio"] for p in HARDWARE_PROFILES.values() for ch in p["channels"].values()}
RETIRED_TOGGLE_PINS = {ch["toggle_gpio"] for p in HARDWARE_PROFILES.values() for ch in p["channels"].values()}


def read_inputs(profile_name="EMS-4CH-v1", button_cls=None, samples=3, interval_s=0.05):
    """Read feedback inputs. Retired toggles are reported and never opened.

    Returns {"toggles": {slot: reserved record}, "detects": {slot: reading}}.
    """
    if button_cls is None:
        from gpiozero import Button as button_cls  # noqa: N813  (import here: no output classes)

    profile = HARDWARE_PROFILES[profile_name]
    detect_active_low = bool(profile["detect_active_low"])
    report = {"toggles": {}, "detects": {}}
    devices = []
    try:
        for slot, ch in profile["channels"].items():
            toggle_pin = ch["toggle_gpio"]
            assert toggle_pin in RETIRED_TOGGLE_PINS
            report["toggles"][slot] = {
                "gpio": toggle_pin,
                "status": "RESERVED",
                "role": "RETIRED",
                "ownership": "NOT USED BY CONTROLLER",
                "opened": False,
            }

            pin = ch["detect_gpio"]
            assert pin not in RELAY_PINS, f"refusing to touch relay GPIO{pin}"
            assert pin not in RETIRED_TOGGLE_PINS, f"refusing to open retired toggle GPIO{pin}"
            btn = button_cls(pin, pull_up=detect_active_low, bounce_time=None)
            devices.append(btn)
            reads = []
            for _ in range(samples):
                reads.append(bool(btn.is_pressed))
                time.sleep(interval_s)
            active = all(reads)
            stable = len(set(reads)) == 1
            level = ("LOW" if active else "HIGH") if detect_active_low else ("HIGH" if active else "LOW")
            report["detects"][slot] = {
                "gpio": pin,
                "polarity": "ACTIVE-LOW" if detect_active_low else "ACTIVE-HIGH",
                "level": level if stable else "UNSTABLE",
                "logical": ("ON" if active else "OFF") if stable else "UNKNOWN",
                "opened": True,
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
    print("Stop ems-controller before running. This tool claims feedback GPIOs only.")
    print("Relays GPIO 17/27/23/22 are never constructed or driven.")
    print("Retired toggles GPIO 5/6/13/12 are RESERVED / NOT USED BY CONTROLLER and are not opened.\n")
    try:
        report = read_inputs()
    except Exception as exc:  # report, do not mask: this is the GPIO_INIT_FAILED cause
        print(f"GPIO INPUT INIT FAILED: {type(exc).__name__}: {exc}")
        print("Check: ems-controller is stopped, GPIOZERO_PIN_FACTORY=lgpio, writable cwd (/mnt/ems-data), user in group gpio.")
        return 2
    print(f"{'ROLE':8} {'SLOT':4} {'GPIO':>4}  STATUS")
    for slot, r in report["toggles"].items():
        print(f"{'toggle':8} {slot:4} {r['gpio']:>4}  {r['status']} / {r['role']} / {r['ownership']}")
    print(f"\n{'ROLE':8} {'SLOT':4} {'GPIO':>4}  {'POLARITY':11} {'LEVEL':8} LOGICAL")
    for slot, r in report["detects"].items():
        print(f"{'detect':8} {slot:4} {r['gpio']:>4}  {r['polarity']:11} {r['level']:8} {r['logical']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
