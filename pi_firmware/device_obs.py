"""Process-local Pi diagnostics. Does not change queue, config, or GPIO decisions.

PROCESS_LOCAL / SINCE_RESTART. File sizes are stat() reads. Missing values are UNKNOWN.
"""
import os
import threading
import time

SCOPE = "PROCESS_LOCAL / SINCE_RESTART"
COMPONENTS = frozenset({"sync", "ack", "batch", "pi_report", "queue", "day_based", "other"})

_lock = threading.Lock()
_counts = {
    "claim_next_total": 0,
    "claim_next_empty": 0,
    "claim_next_command": 0,
    "claim_next_commit": 0,
    "claim_next_rollback": 0,
    "claim_next_tx_started": 0,
    "persist_attempts": 0,
    "persist_state_changed": 0,
    "persist_state_unchanged": 0,
    "config_apply_attempts": 0,
    "config_apply_changed": 0,
    "config_apply_unchanged": 0,
    "config_apply_rejected": 0,
    "energy_config_received_total": 0,
    "energy_config_accepted_total": 0,
    "energy_config_rejected_total": 0,
    "energy_config_report_integer": 0,
    "energy_config_report_missing": 0,
    "observation_errors_total": 0,
}
_error_components = {}
_sqlite = {"journal_mode": "UNKNOWN", "synchronous": "UNKNOWN"}
_last_config = None
_last_persist = None
_last_energy_received = None
_last_energy_report = None


def note_observation_error(component):
    """Count an observer failure. Never raises and never calls safe_observe."""
    try:
        name = component if component in COMPONENTS else "other"
        with _lock:
            _counts["observation_errors_total"] = int(_counts.get("observation_errors_total", 0)) + 1
            _error_components[name] = int(_error_components.get(name, 0)) + 1
            if len(_error_components) > 16:
                _error_components.pop(next(iter(_error_components)))
    except Exception:
        return


def safe_observe(component, fn):
    """Run an observer. Observer exceptions are counted and do not propagate."""
    try:
        return fn()
    except Exception:
        note_observation_error(component)
        return None


def _inc(name, n=1):
    try:
        with _lock:
            _counts[name] = int(_counts.get(name, 0)) + int(n)
    except Exception:
        return


def note_sqlite_settings(journal_mode, synchronous):
    try:
        with _lock:
            if journal_mode is not None:
                _sqlite["journal_mode"] = str(journal_mode)
            if synchronous is not None:
                _sqlite["synchronous"] = str(synchronous)
    except Exception:
        return


def claim_called():
    _inc("claim_next_total")


def claim_tx_started():
    _inc("claim_next_tx_started")


def claim_empty(committed):
    _inc("claim_next_empty")
    if committed:
        _inc("claim_next_commit")


def claim_rollback():
    _inc("claim_next_rollback")


def claim_command(command_id, action, slot, duration_s):
    _inc("claim_next_command")
    _inc("claim_next_commit")
    return {
        "command_id": command_id,
        "action": action,
        "slot": slot,
        "duration_ms": int((duration_s or 0) * 1000),
    }


def note_persist(changed, wrote, scheduled, path, duration_s, previous):
    _inc("persist_attempts")
    if changed and wrote:
        _inc("persist_state_changed")
    else:
        _inc("persist_state_unchanged")
    size = _file_size(path)
    body = {
        "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scheduled": scheduled,
        "changed": bool(changed and wrote),
        "wrote": bool(wrote),
        "bytes": size,
        "duration_ms": int((duration_s or 0) * 1000),
        "previous_scheduled": (previous or {}).get("scheduled") if isinstance(previous, dict) else None,
    }
    try:
        with _lock:
            global _last_persist
            _last_persist = body
    except Exception:
        return


def config_apply(device_id, incoming_version, current_version, incoming_hash, current_hash,
                 outcome, reason, persisted, duration_s):
    _inc("config_apply_attempts")
    if outcome == "changed":
        _inc("config_apply_changed")
    elif outcome == "unchanged":
        _inc("config_apply_unchanged")
    else:
        _inc("config_apply_rejected")
    body = {
        "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "device_id": device_id,
        "incoming_config_version": incoming_version,
        "current_config_version": current_version,
        "incoming_config_hash": incoming_hash,
        "current_config_hash": current_hash,
        "outcome": outcome,
        "persisted": bool(persisted),
        "rejection": reason,
        "duration_ms": int((duration_s or 0) * 1000),
    }
    try:
        with _lock:
            global _last_config
            _last_config = body
    except Exception:
        return


def note_energy_config_received(version, validation, persisted, applied, reason):
    """Last energy-config receive only. Never raises and does not change config."""
    try:
        valid = type(version) is int
        accepted = validation == "accepted"
        _inc("energy_config_received_total")
        _inc("energy_config_accepted_total" if accepted else "energy_config_rejected_total")
        body = {
            "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "version": version if valid else None,
            "version_is_integer": valid,
            "validation": "accepted" if accepted else "rejected",
            "persisted": bool(persisted),
            "applied": bool(applied),
            "reason": None if accepted else str(reason or "REJECTED")[:80],
        }
        with _lock:
            global _last_energy_received
            _last_energy_received = body
    except Exception:
        return


def note_energy_config_report(config_version):
    """Record the snapshot version exactly. Never replaces null with a number."""
    try:
        valid = type(config_version) is int
        _inc("energy_config_report_integer" if valid else "energy_config_report_missing")
        body = {
            "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "config_version": config_version if valid else None,
            "valid_integer": valid,
        }
        with _lock:
            global _last_energy_report
            _last_energy_report = body
    except Exception:
        return


def _file_size(path):
    if not path:
        return "UNKNOWN"
    try:
        if not os.path.exists(path):
            return None
        return os.path.getsize(path)
    except OSError:
        return "UNKNOWN"


def _sqlite_files(db_file):
    if not db_file:
        return {"bytes": "UNKNOWN", "wal_bytes": "UNKNOWN", "shm_bytes": "UNKNOWN"}
    return {
        "bytes": _file_size(db_file),
        "wal_bytes": _file_size(db_file + "-wal"),
        "shm_bytes": _file_size(db_file + "-shm"),
    }


def _log_gauges():
    try:
        from logger import _file_handlers, ring
    except Exception:
        return {"normal_log_bytes": "UNKNOWN", "critical_log_bytes": "UNKNOWN", "ring_dropped": "UNKNOWN"}
    normal = critical = "UNKNOWN"
    try:
        for handler in list(_file_handlers):
            if getattr(handler, "category", "") == "normal_log":
                normal = getattr(handler, "bytes_written", "UNKNOWN")
            elif getattr(handler, "category", "") == "critical_log":
                critical = getattr(handler, "bytes_written", "UNKNOWN")
        dropped = getattr(ring, "dropped", "UNKNOWN")
    except Exception:
        return {"normal_log_bytes": "UNKNOWN", "critical_log_bytes": "UNKNOWN", "ring_dropped": "UNKNOWN"}
    return {
        "normal_log_bytes": normal,
        "critical_log_bytes": critical,
        "ring_dropped": dropped,
        "file_records_dropped": "UNKNOWN",
    }


def snapshot(db_file=None, state_file=None, telemetry_dir=None, day_based_path=None):
    csv_path = None
    if telemetry_dir:
        csv_path = os.path.join(telemetry_dir, time.strftime("daily_%Y-%m-%d.csv"))
    with _lock:
        counts = dict(_counts)
        counts["observation_errors_by_component"] = dict(_error_components)
        sqlite_settings = dict(_sqlite)
        last_config = dict(_last_config) if isinstance(_last_config, dict) else None
        last_persist = dict(_last_persist) if isinstance(_last_persist, dict) else None
        last_energy_received = dict(_last_energy_received) if isinstance(_last_energy_received, dict) else None
        last_energy_report = dict(_last_energy_report) if isinstance(_last_energy_report, dict) else None
    files = _sqlite_files(db_file)
    files.update(_log_gauges())
    files["state_bytes"] = _file_size(state_file)
    files["day_based_bytes"] = _file_size(day_based_path)
    files["telemetry_csv_bytes"] = _file_size(csv_path)
    files["telemetry_csv_writes"] = "UNKNOWN"
    return {
        "scope": SCOPE,
        "counters": counts,
        "sqlite": {**files, "journal_mode": sqlite_settings.get("journal_mode", "UNKNOWN"),
                   "synchronous": sqlite_settings.get("synchronous", "UNKNOWN")},
        "last_config_apply": last_config,
        "last_day_based_persist": last_persist,
        "last_energy_config_received": last_energy_received,
        "last_energy_config_report": last_energy_report,
    }
