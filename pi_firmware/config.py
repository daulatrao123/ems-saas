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

# --- Timing Constants ---
FEEDBACK_TIMEOUT_MS = 2000
FEEDBACK_DEBOUNCE_MS = 50
INTERLOCK_DELAY_MS = 500
LOCAL_MONITOR_INTERVAL_S = 2.0
SYNC_INTERVAL_S = 60.0
SQLITE_BUSY_TIMEOUT_MS = 5000

# --- Storage Architecture ---
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

# --- Storage Budgets (10-year design) ---
DAILY_LOG_BUDGET_BYTES = 10 * 1024 * 1024  # 10 MB hard limit per day

def get_hardware_profile(profile_name: str = "EMS-4CH-v1") -> dict:
    profile = HARDWARE_PROFILES.get(profile_name)
    if not profile:
        raise ValueError(f"Unknown hardware profile: {profile_name}")
    return profile