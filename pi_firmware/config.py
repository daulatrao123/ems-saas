import os


# ================================================================
# HARDWARE
# ================================================================

# Single authoritative GPIO map: see pi_firmware/HARDWARE_SOURCE_OF_TRUTH.md.
# BCM numbering. Change that document first, then this table. Never elsewhere.
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
        # Toggles: pull-down, GPIO HIGH = ON (HARDWARE_SOURCE_OF_TRUTH §4).
        "toggle_active_low": False,
        "detect_active_low": True,
    }
}

SUPPORTED_SLOTS = (
    "A",
    "B",
    "C",
    "D",
)


def _finalize_hardware_profiles():
    """Derive `slots` from `channels` and refuse any GPIO used twice (import-time guard)."""
    for name, profile in HARDWARE_PROFILES.items():
        channels = profile["channels"]
        profile["slots"] = list(channels.keys())
        seen = {}
        for slot, ch in channels.items():
            for role in ("relay_gpio", "toggle_gpio", "detect_gpio"):
                pin = ch[role]
                if pin in seen:
                    raise RuntimeError(
                        f"FATAL: hardware profile {name} assigns GPIO{pin} to both "
                        f"{seen[pin]} and {slot}.{role}"
                    )
                seen[pin] = f"{slot}.{role}"


_finalize_hardware_profiles()


# ================================================================
# FEEDBACK / TOGGLE POLARITY
# ================================================================

# Polarity lives in the hardware profile (*_active_low). gpiozero: pull_up=True -> "pressed" when
# LOW (active-low input); pull_up=False -> internal pull-down, "pressed" when HIGH (active-high).
# `is_pressed` therefore always means "logically active" for both toggles and detect inputs.
TOGGLE_DEBOUNCE_S = 0.05


# ================================================================
# HARDWARE TIMING
# ================================================================

FEEDBACK_TIMEOUT_MS = 2000
FEEDBACK_DEBOUNCE_MS = 50

INTERLOCK_DELAY_MS = 500

LOCAL_MONITOR_INTERVAL_S = 2.0


# ================================================================
# CLOUD
# ================================================================

SYNC_INTERVAL_S = 60.0
API_TIMEOUT_S = 10


# ================================================================
# SQLITE
# ================================================================

SQLITE_BUSY_TIMEOUT_MS = 5000

# Automatic WAL checkpoint threshold.
# This is deliberately moderate to prevent a huge WAL.
SQLITE_WAL_AUTOCHECKPOINT_PAGES = 512

# Do not perform explicit checkpoints frequently.
SQLITE_CHECKPOINT_INTERVAL_S = 3600


# ================================================================
# STATE
# ================================================================

STATE_VERSION = 4

STATE_SAVE_MIN_INTERVAL_S = 60.0


# ================================================================
# STORAGE
# ================================================================

DATA_DIR = "/mnt/ems-data"

STATE_DIR = os.path.join(
    DATA_DIR,
    "state",
)

LOG_DIR = os.path.join(
    DATA_DIR,
    "logs",
)

QUEUE_DIR = os.path.join(
    DATA_DIR,
    "queue",
)

TELEMETRY_DIR = os.path.join(
    DATA_DIR,
    "telemetry",
)

DIAGNOSTICS_DIR = os.path.join(
    DATA_DIR,
    "diagnostics",
)

HEALTH_DIR = os.path.join(
    DATA_DIR,
    "health",
)


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

STORAGE_COUNTER_FILE = os.path.join(
    HEALTH_DIR,
    "storage_counters.json",
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
# FLASH WRITE BUDGET
# ================================================================

# Maximum application normal-log budget.
DAILY_LOG_BUDGET_BYTES = (
    10 * 1024 * 1024
)

# Separate safety/critical log budget.
CRITICAL_LOG_BUDGET_BYTES = (
    2 * 1024 * 1024
)

# Desired normal operating target.
NORMAL_LOG_TARGET_BYTES = (
    3 * 1024 * 1024
)

# Protection threshold.
#
# IMPORTANT:
# This is NOT NAND endurance.
# It is only a daily application/system write
# protection threshold.
TOTAL_DAILY_PHYSICAL_BUDGET_BYTES = (
    50 * 1024 * 1024
)


# ================================================================
# STORAGE CAPACITY
# ================================================================

STORAGE_WARNING_PERCENT = 70
STORAGE_CLEANUP_PERCENT = 80
STORAGE_REDUCED_PERCENT = 90
STORAGE_PROTECTED_PERCENT = 95
STORAGE_CRITICAL_PERCENT = 98


# ================================================================
# STORAGE MONITORING
# ================================================================

STORAGE_METRICS_INTERVAL_S = 60

# Counters are persisted hourly, not every monitoring cycle.
STORAGE_HEALTH_PERSIST_INTERVAL_S = 3600

# Daily telemetry is written once per day.
STORAGE_DAILY_PERSIST_INTERVAL_S = 86400


# ================================================================
# RETENTION
# ================================================================

NORMAL_LOG_RETENTION_DAYS = 30
CRITICAL_LOG_RETENTION_DAYS = 365
TELEMETRY_RETENTION_DAYS = 365
DIAGNOSTIC_RETENTION_DAYS = 90


# ================================================================
# MEMORY
# ================================================================

MEMORY_SAMPLE_INTERVAL_S = 600

MEMORY_HISTORY_SAMPLES = 144

MEMORY_AVAILABLE_PRESSURE_PERCENT = 15

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
).rstrip("/")

DEVICE_ID = os.environ.get(
    "EMS_DEVICE_ID"
)

API_KEY = os.environ.get(
    "EMS_API_KEY"
)

if not DEVICE_ID:
    raise RuntimeError(
        "FATAL: EMS_DEVICE_ID is required."
    )

if not API_KEY:
    raise RuntimeError(
        "FATAL: EMS_API_KEY is required."
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