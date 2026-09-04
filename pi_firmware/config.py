import os

# --- Hardware Profiles ---
HARDWARE_PROFILES = {
    "EMS-4CH-v1": {
        "slots": ["A", "B", "C", "D"],
        "relay_gpio": {"A": 17, "B": 27, "C": 22, "D": 23},
        "feedback_gpio": {"A": 5, "B": 6, "C": 13, "D": 19},
        "feedback_capable": True
    }
}

# --- Timing Constants (Industrial Grade) ---
FEEDBACK_TIMEOUT_MS = 2000       # Max time to wait for contactor to close/open
FEEDBACK_DEBOUNCE_MS = 50        # Ignore contact bounce shorter than this
INTERLOCK_DELAY_MS = 500         # Break-before-make delay
LOCAL_MONITOR_INTERVAL_S = 2.0   # Fast local fault checking
SYNC_INTERVAL_S = 60.0           # Backend sync interval

# --- Storage Architecture ---
# Partition dedicated to EMS data to isolate from OS journaling
DATA_DIR = "/mnt/ems-data"
STATE_DIR = os.path.join(DATA_DIR, "state")
LOG_DIR = os.path.join(DATA_DIR, "logs")
QUEUE_DIR = os.path.join(DATA_DIR, "queue")
TELEMETRY_DIR = os.path.join(DATA_DIR, "telemetry")

STATE_FILE = os.path.join(STATE_DIR, "current_state.json")
BACKUP_STATE_FILE = os.path.join(STATE_DIR, "backup_state.json")
DB_FILE = os.path.join(QUEUE_DIR, "ems_queue.sqlite")

# Ensure directories exist
for d in [DATA_DIR, STATE_DIR, LOG_DIR, QUEUE_DIR, TELEMETRY_DIR]:
    os.makedirs(d, exist_ok=True)

def get_hardware_profile(profile_name: str = "EMS-4CH-v1") -> dict:
    profile = HARDWARE_PROFILES.get(profile_name)
    if not profile:
        raise ValueError(f"Unknown hardware profile: {profile_name}")
    return profile