import os


# ================================================================
# HARDWARE
# ================================================================

HARDWARE_PROFILES = {
    "EMS-4CH-v1": {
        "slots": ["A", "B", "C", "D"],

        "relay_gpio": {
            "A": 17,
            "B": 27,
            "C": 22,
            "D": 23,
        },

        "feedback_gpio": {
            "A": 5,
            "B": 6,
            "C": 13,
            "D": 19,
        },

        "feedback_capable": True,
    }
}

SUPPORTED_SLOTS = ("A", "B", "C", "D")


# ================================================================
# HARDWARE TIMING
# ================================================================

FEEDBACK_TIMEOUT_MS = 2000
FEEDBACK_DEBOUNCE_MS = 50
INTERLOCK_DELAY_MS = 500

# Physical contactor monitoring is RAM-only.
LOCAL_MONITOR_INTERVAL_S = 2.0


# ================================================================
# CLOUD
# ================================================================

SYNC_INTERVAL_S = 60.0


# ================================================================
# SQLITE
# ================================================================

SQLITE_BUSY_TIMEOUT_MS = 5000

# Smaller WAL checkpoint keeps WAL bounded.
SQLITE_WAL_AUTOCHECKPOINT_PAGES = 512


# ================================================================
# STATE
# ================================================================

STATE_VERSION = 2


# ================================================================
# LOCAL STORAGE
# ================================================================

DATA_DIR = "/mnt/ems-data"

STATE_DIR = os.path.join(DATA_DIR, "state")
LOG_DIR = os.path.join(DATA_DIR, "logs")
QUEUE_DIR = os.path.join(DATA_DIR, "queue")
TELEMETRY_DIR = os.path.join(DATA_DIR, "telemetry")
DIAGNOSTICS_DIR = os.path.join(DATA_DIR, "diagnostics")
HEALTH_DIR = os.path.join(DATA_DIR, "health")

STATE_FILE = os.path.join(
    STATE_DIR,
    "current_state.json",
)

BACKUP_STATE_FILE = os.path.join(
    STATE_DIR,
    "backup_state.json",
)

RECOVERY_STATE_FILE = os.path.join(
    STATE_DIR,
    "recovery_state.json",
)

DB_FILE = os.path.join(
    QUEUE_DIR,
    "ems_queue.sqlite",
)


for directory in (
    DATA_DIR,
    STATE_DIR,
    LOG_DIR,
    QUEUE_DIR,
    TELEMETRY_DIR,
    DIAGNOSTICS_DIR,
    HEALTH_DIR,
):
    os.makedirs(
        directory,
        exist_ok=True,
    )


# ================================================================
# FLASH-WEAR POLICY
#
# These are SOFTWARE budgets.
# They are NOT NAND endurance guarantees.
# ================================================================

# Normal application logging ceiling.
DAILY_LOG_BUDGET_BYTES = (
    10 * 1024 * 1024
)

# Critical event logging ceiling.
CRITICAL_LOG_BUDGET_BYTES = (
    2 * 1024 * 1024
)

# Target is deliberately much lower than the ceiling.
NORMAL_LOG_TARGET_BYTES = (
    3 * 1024 * 1024
)

# Protection threshold for measured daily block-device writes.
#
# This does NOT mean "the flash is worn out".
# It means optional writes should be reduced.
TOTAL_DAILY_PHYSICAL_BUDGET_BYTES = (
    50 * 1024 * 1024
)


# ================================================================
# STORAGE CAPACITY PRESSURE
# ================================================================

STORAGE_WARNING_PERCENT = 70
STORAGE_CLEANUP_PERCENT = 80
STORAGE_REDUCED_PERCENT = 90
STORAGE_PROTECTED_PERCENT = 95
STORAGE_CRITICAL_PERCENT = 98


# ================================================================
# STORAGE MONITORING
#
# These operations are RAM-heavy / read-only.
# Do NOT write these measurements every minute.
# ================================================================

STORAGE_METRICS_INTERVAL_S = 60

# Persist accumulated storage statistics only hourly.
STORAGE_HEALTH_PERSIST_INTERVAL_S = 3600

# Daily summary.
STORAGE_DAILY_PERSIST_INTERVAL_S = 86400


# ================================================================
# LOG RETENTION
# ================================================================

NORMAL_LOG_RETENTION_DAYS = 30

# Critical history should be retained considerably longer.
CRITICAL_LOG_RETENTION_DAYS = 365

TELEMETRY_RETENTION_DAYS = 365

DIAGNOSTIC_RETENTION_DAYS = 90


# ================================================================
# MEMORY
# ================================================================

MEMORY_SAMPLE_INTERVAL_S = 600

# 24 hours at 10-minute intervals.
MEMORY_HISTORY_SAMPLES = 144

# Enter memory pressure when MemAvailable is below this percentage.
MEMORY_AVAILABLE_PRESSURE_PERCENT = 15

# Any sustained swap use above this is suspicious.
SWAP_WARNING_BYTES = (
    10 * 1024 * 1024
)

MEMORY_LEAK_MIN_SAMPLES = 72

MEMORY_LEAK_RSS_GROWTH_PERCENT = 20
MEMORY_LEAK_VMS_GROWTH_PERCENT = 20
MEMORY_LEAK_FD_GROWTH_PERCENT = 25
MEMORY_LEAK_THREAD_GROWTH_PERCENT = 25


# ================================================================
# CLOUD CREDENTIALS
# ================================================================

API_BASE_URL = os.environ.get(
    "EMS_API_URL",
    "https://ems-backend.onrender.com/api",
)

DEVICE_ID = os.environ.get(
    "EMS_DEVICE_ID"
)

API_KEY = os.environ.get(
    "EMS_API_KEY"
)

if not DEVICE_ID or not API_KEY:
    raise RuntimeError(
        "FATAL: EMS_DEVICE_ID and EMS_API_KEY "
        "environment variables are required."
    )


def get_hardware_profile(
    profile_name: str = "EMS-4CH-v1",
) -> dict:

    profile = HARDWARE_PROFILES.get(
        profile_name
    )

    if profile is None:
        raise ValueError(
            f"Unknown hardware profile: {profile_name}"
        )

    return profile