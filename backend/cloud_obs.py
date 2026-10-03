"""Process-local diagnostics for sync, ACK, and DAY_BASED.

PROCESS_LOCAL / SINCE_RESTART. Nothing here is written to PostgreSQL.
Counters use bounded labels. Identifiers live only in a fixed-size ring.
"""
import hashlib
import threading
import time
from collections import deque

SCOPE = "PROCESS_LOCAL / SINCE_RESTART"
RING_MAX = 200
DEVICE_GAUGE_MAX = 256
COMPONENTS = frozenset({"sync", "ack", "batch", "pi_report", "queue", "day_based", "other"})

COMMANDS = frozenset({
    "set_active_slot", "set_days", "set_reset_day", "restart",
    "reboot", "reset_days", "off_slot", "off_all", "lcd_display", "other",
})
SLOTS = frozenset({"A", "B", "C", "D", "none", "other"})
STATUSES = frozenset({
    "queued", "delivered", "executing", "hardware_verified", "unknown_after_reboot",
    "completed", "failed", "expired", "acked", "unknown", "other",
})
SECRET_NAMES = ("password", "api_key", "apikey", "authorization", "secret", "signature", "private_key", "credential")

_lock = threading.Lock()
_counts = {}
_dims = {}
_ring = deque(maxlen=RING_MAX)
_pi = {}
_pi_order = deque()


def _bound(value, allowed, fallback="other"):
    text = str(value or fallback).strip().lower()
    if text in allowed:
        return text
    upper = str(value or "").strip().upper()
    if upper in allowed:
        return upper
    return fallback


def _inc(name, n=1):
    try:
        with _lock:
            _counts[name] = int(_counts.get(name, 0)) + int(n)
    except Exception:
        return


def _dim(name, key, n=1):
    try:
        with _lock:
            bucket = _dims.setdefault(name, {})
            bucket[key] = int(bucket.get(key, 0)) + int(n)
            if len(bucket) > 256:
                bucket.pop(next(iter(bucket)))
    except Exception:
        return


def _clean(value):
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, str):
        return value[:160]
    if isinstance(value, dict):
        out = {}
        for key, item in list(value.items())[:40]:
            name = str(key)[:40]
            if any(token in name.lower() for token in SECRET_NAMES):
                continue
            out[name] = _clean(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in list(value)[:20]]
    return str(value)[:160]


def note_observation_error(component):
    """Count an observer failure. Never raises and never calls safe_observe."""
    try:
        name = component if component in COMPONENTS else "other"
        with _lock:
            _counts["observation_errors_total"] = int(_counts.get("observation_errors_total", 0)) + 1
            bucket = _dims.setdefault("observation_errors_by_component", {})
            bucket[name] = int(bucket.get(name, 0)) + 1
            if len(bucket) > 16:
                bucket.pop(next(iter(bucket)))
            _ring.append({
                "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "kind": "observation_error",
                "component": name,
                "scope": SCOPE,
            })
    except Exception:
        return


def safe_observe(component, fn):
    """Run an observer. Observer exceptions are counted and do not propagate."""
    try:
        return fn()
    except Exception:
        note_observation_error(component)
        return None


def _event(kind, fields):
    try:
        body = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "kind": kind}
        body.update(_clean(fields) or {})
        with _lock:
            _ring.append(body)
    except Exception:
        return


def idempotency_hash(value):
    if not isinstance(value, str) or not value.strip():
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def finish_sync(device_id, society_id, http_status, duration_s, committed, statement_stats, delivery, config):
    _inc("sync_requests_total")
    if int(http_status) < 400 and committed:
        _inc("sync_success_total")
    else:
        _inc("sync_failure_total")
    delivered = bool(delivery and delivery.get("command_id"))
    if delivered:
        _inc("sync_commands_delivered_total")
        _inc("commands_delivered_total")
        command = _bound(delivery.get("command"), COMMANDS)
        slot = _bound(delivery.get("slot") or "none", SLOTS, "none")
        _dim("command_delivery_by_type", command)
        _dim("command_delivery_by_slot", slot)
    if isinstance(statement_stats, dict) and isinstance(statement_stats.get("statements"), int):
        statements = statement_stats["statements"]
        rows_attempted = dict(statement_stats.get("rows") or {})
        rows_committed = rows_attempted if committed else {}
    else:
        statements = "UNKNOWN"
        rows_attempted = "UNKNOWN"
        rows_committed = "UNKNOWN"
    _event("sync", {
        "device_id": device_id,
        "society_id": society_id,
        "http_status": int(http_status),
        "duration_ms": int(duration_s * 1000),
        "transaction": "committed" if committed else "rolled_back",
        "statements": statements,
        "rows_committed": rows_committed,
        "rows_attempted": rows_attempted,
        "command_delivered": delivered,
        "command_id": (delivery or {}).get("command_id"),
        "command": (delivery or {}).get("command"),
        "slot": (delivery or {}).get("slot"),
        "allocation_batch_id": (delivery or {}).get("allocation_batch_id"),
        "sequence_no": (delivery or {}).get("sequence_no"),
        "config_version": (config or {}).get("config_version"),
        "config_hash": (config or {}).get("config_hash"),
        "applied_config_version": (config or {}).get("applied_config_version"),
        "applied_config_hash": (config or {}).get("applied_config_hash"),
    })


def note_pi_report(device_id, society_id, obs):
    if not isinstance(obs, dict) or len(str(obs)) > 8000:
        return
    key = str(device_id)
    body = _clean(obs)
    body["device_id"] = key
    body["society_id"] = society_id
    body["scope"] = SCOPE
    with _lock:
        if key not in _pi and len(_pi) >= DEVICE_GAUGE_MAX:
            old = _pi_order.popleft()
            _pi.pop(old, None)
        if key not in _pi:
            _pi_order.append(key)
        _pi[key] = body


def record_ack(fields, committed, http_status, duration_s, idempotent=False, rejected=False):
    _inc("ack_received_total")
    status = _bound((fields or {}).get("resulting_status") or (fields or {}).get("incoming_status"), STATUSES)
    if idempotent:
        _inc("ack_idempotent_total")
    if rejected or int(http_status) >= 400:
        _inc("ack_rejected_total")
    if status == "completed" and committed:
        _inc("ack_completed_total")
    if status == "acked" and committed:
        _inc("ack_acked_total")
    command = _bound((fields or {}).get("command"), COMMANDS)
    previous = _bound((fields or {}).get("previous_status"), STATUSES)
    _dim("ack_transition_by_command", f"{command}|{previous}|{status}")
    _event("ack", {
        **(fields or {}),
        "http_status": int(http_status),
        "duration_ms": int(duration_s * 1000),
        "idempotent": bool(idempotent),
        "rejected": bool(rejected),
        "transaction": "committed" if committed else "rolled_back",
    })


def note_batch(device_id, society_id, batch_id, rows, published, targets, superseded=False):
    statuses = [str(row.get("status")) for row in rows or []]
    applied = sum(1 for status in statuses if status in ("completed", "acked"))
    failed = sum(1 for status in statuses if status == "failed")
    expired = sum(1 for status in statuses if status == "expired")
    _inc("allocation_batch_evaluated")
    if published:
        _inc("allocation_batch_publications")
    elif failed or expired:
        if applied:
            _inc("allocation_batch_partial")
        else:
            _inc("allocation_batch_failures")
    _event("batch_publication", {
        "device_id": device_id,
        "society_id": society_id,
        "allocation_batch_id": batch_id,
        "evaluated": True,
        "published": bool(published),
        "superseded": bool(superseded),
        "member_count": len(statuses),
        "completed_or_acked": applied,
        "failed": failed,
        "expired": expired,
        "targets": targets or [],
        "config_version_incremented": bool(published),
        "config_version_before": "UNKNOWN",
        "config_version_after": "UNKNOWN",
    })


def note_software_recovery(fields):
    """Bounded recovery record. Never raises and never touches PostgreSQL."""
    try:
        body = fields if isinstance(fields, dict) else {}
        _inc("software_command_recovery_total")
        _event("software_command_recovery", {
            "device_id": body.get("device_id"),
            "command_id": body.get("command_id"),
            "command": body.get("command"),
            "sequence_no": body.get("sequence_no"),
            "previous_status": body.get("previous_status"),
            "resulting_status": body.get("resulting_status"),
            "recovery_reason": body.get("recovery_reason"),
            "allocation_batch_id": body.get("allocation_batch_id"),
            "idempotent": bool(body.get("idempotent")),
            "recovered": bool(body.get("recovered")),
            "expired": bool(body.get("expired")),
        })
    except Exception:
        return


def note_allocation(kind, http_status, duration_s, body, idempotency_hash_value=None):
    _inc("allocation_calculate_total" if kind == "calculate" else "allocation_apply_total")
    commands = (body or {}).get("commands") or []
    if kind == "apply" and body and not body.get("duplicate"):
        _inc("allocation_batches_created")
        _inc("allocation_commands_created", len(commands) or 0)
    _event("allocation_" + kind, {
        "http_status": int(http_status),
        "duration_ms": int(duration_s * 1000),
        "device_id": (body or {}).get("device_id"),
        "allocation_batch_id": (body or {}).get("batch_id"),
        "status": (body or {}).get("status"),
        "duplicate": bool((body or {}).get("duplicate")),
        "idempotency_key_present": idempotency_hash_value is not None,
        "idempotency_key_hash": idempotency_hash_value,
        "days": (body or {}).get("days"),
        "cycle_days": (body or {}).get("cycle_days"),
        "reset_day": (body or {}).get("reset_day"),
        "enabled_wings": (body or {}).get("enabled_wings"),
        "commands": commands,
        "command_count": len(commands),
    })


def snapshot(society_id=None, device_id=None, include_fleet=True):
    with _lock:
        recent = list(_ring)
        gauges = dict(_pi)
        counts = dict(_counts)
        dims = {name: dict(bucket) for name, bucket in _dims.items()}
    if society_id is not None:
        recent = [row for row in recent if str(row.get("society_id")) == str(society_id)]
        gauges = {key: value for key, value in gauges.items() if str(value.get("society_id")) == str(society_id)}
    if device_id:
        recent = [row for row in recent if str(row.get("device_id")) == str(device_id)]
        gauges = {key: value for key, value in gauges.items() if key == str(device_id)}
    body = {
        "scope": SCOPE,
        "durable": False,
        "recent": recent[-100:],
        "pi_gauges": gauges,
    }
    if include_fleet:
        body["counters"] = counts
        body["dimensions"] = dims
    return body
