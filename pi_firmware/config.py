import json
import os

# --- Hardware Profiles ---
# Never let the web UI define GPIO pins. They are fixed to the hardware version.
HARDWARE_PROFILES = {
    "EMS-4CH-v1": {
        "slots": ["A", "B", "C", "D"],
        "relay_gpio": {"A": 17, "B": 27, "C": 22, "D": 23},
        "feedback_gpio": {"A": 5, "B": 6, "C": 13, "D": 19},
        "feedback_capable": True
    }
}

# --- Timing Constants (Critical for Industrial Reliability) ---
FEEDBACK_TIMEOUT_MS = 2000       # Max time to wait for contactor to close/open
FEEDBACK_DEBOUNCE_MS = 50        # Ignore contact bounce shorter than this
INTERLOCK_DELAY_MS = 500         # Break-before-make delay to prevent cross-connection
LOCAL_MONITOR_INTERVAL_S = 2.0   # Fast local fault checking (does not hit backend)
SYNC_INTERVAL_S = 60.0           # Normal backend sync interval

# --- Persistence ---
STATE_FILE = os.path.join(os.path.dirname(__file__), 'pi_state.json')

def get_hardware_profile(profile_name: str = "EMS-4CH-v1") -> dict:
    profile = HARDWARE_PROFILES.get(profile_name)
    if not profile:
        raise ValueError(f"Unknown hardware profile: {profile_name}")
    return profile