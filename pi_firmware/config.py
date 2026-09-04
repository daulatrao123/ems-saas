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

for d in [DATA_DIR, STATE_DIR, LOG_DIR, QUEUE_DIR, TELEMETRY_DIR]:
    os.makedirs(d, exist_ok=True)

# --- Storage Budgets ---
DAILY_LOG_BUDGET_BYTES = 10 * 1024 * 1024
CRITICAL_LOG_BUDGET_BYTES = 2 * 1024 * 1024

# --- Cloud API ---
API_BASE_URL = os.environ.get("EMS_API_URL", "https://ems-backend.onrender.com/api")
DEVICE_ID = os.environ.get("EMS_DEVICE_ID", "PI-001")
API_KEY = os.environ.get("EMS_API_KEY", "dev-secret-key")

def get_hardware_profile(profile_name: str = "EMS-4CH-v1") -> dict:
    profile = HARDWARE_PROFILES.get(profile_name)
    if not profile:
        raise ValueError(f"Unknown hardware profile: {profile_name}")
    return profile