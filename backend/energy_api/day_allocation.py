"""DAY_BASED desired allocation. Calculation mode is not involved.

Cycle length follows the existing reset day: cycle day 1 is that day, and the
length is the number of days until the next reset day. Unit conversion is
largest-remainder and always sums to the cycle length.
"""
import uuid
from datetime import date, timedelta

from psycopg.types.json import Json

WINGS = ("A", "B", "C", "D")
ALLOCATION_MODES = ("AUTO", "MANUAL", "DAY_BASED")
DAY_MODE = "DAY_BASED"
COMMAND_EXPIRY_SECONDS = 300
IN_FLIGHT = {"queued", "delivered", "executing", "hardware_verified", "unknown_after_reboot"}
APPLIED = {"completed", "acked"}
FAILED = {"failed", "expired"}


def parse_mode(mode):
    if mode not in ALLOCATION_MODES:
        raise ValueError("allocation mode must be AUTO, MANUAL, or DAY_BASED")
    return mode


def period_bounds(operating_date, reset_day):
    """Return [start, end) for the reset period containing operating_date."""
    reset_day = int(reset_day)
    if not 1 <= reset_day <= 28:
        raise ValueError("reset day must be 1-28")
    if operating_date.day >= reset_day:
        start = date(operating_date.year, operating_date.month, reset_day)
        end = date(start.year + 1, 1, reset_day) if start.month == 12 else date(start.year, start.month + 1, reset_day)
    else:
        if operating_date.month == 1:
            start = date(operating_date.year - 1, 12, reset_day)
        else:
            start = date(operating_date.year, operating_date.month - 1, reset_day)
        end = date(operating_date.year, operating_date.month, reset_day)
    return start, end


def cycle_length(operating_date, reset_day):
    start, end = period_bounds(operating_date, reset_day)
    return (end - start).days


def cycle_day_index(operating_date, reset_day):
    start, _end = period_bounds(operating_date, reset_day)
    return (operating_date - start).days + 1


def units_to_days(units, cycle_days, order=WINGS):
    """Largest remainder. The returned integers sum to cycle_days."""
    order = tuple(order)
    if cycle_days < 0 or not isinstance(cycle_days, int):
        raise ValueError("cycle_days must be a non-negative integer")
    cleaned = {}
    for wing in order:
        value = units.get(wing, 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{wing} units must be a number")
        if value < 0:
            raise ValueError(f"{wing} units must be >= 0")
        cleaned[wing] = float(value)
    total = sum(cleaned.values())
    if cycle_days == 0:
        return {wing: 0 for wing in order}
    if total <= 0:
        raise ValueError("units must be greater than zero")
    exact = {wing: cleaned[wing] / total * cycle_days for wing in order}
    out = {wing: int(exact[wing]) for wing in order}
    remainder = cycle_days - sum(out.values())
    ranked = sorted(order, key=lambda wing: (-(exact[wing] - out[wing]), order.index(wing)))
    for index in range(remainder):
        out[ranked[index]] += 1
    return out


def scheduled_wing(days, index, enabled):
    cursor = 1
    for wing in WINGS:
        if wing not in enabled:
            continue
        count = int(days.get(wing, 0) or 0)
        if count <= 0:
            continue
        if cursor <= index < cursor + count:
            return wing
        cursor += count
    return None


def _day_value(value, wing):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{wing} days must be an integer")
    if not 0 <= value <= 31:
        raise ValueError(f"{wing} days must be 0-31")
    return value


def resolve_days(kind, wings, enabled, cycle_days):
    """Return integer days for enabled wings. Disabled wings are omitted."""
    enabled = [wing for wing in WINGS if wing in set(enabled)]
    supplied = wings if isinstance(wings, dict) else None
    if supplied is None:
        raise ValueError("wings must be an object")
    disabled = [wing for wing in WINGS if wing not in enabled and wing in supplied and supplied[wing] not in (0, 0.0, None)]
    if disabled:
        raise ValueError("disabled wings cannot receive an allocation")
    if kind == "DAYS":
        days = {wing: _day_value(supplied.get(wing, 0), wing) for wing in enabled}
    elif kind == "UNITS":
        days = units_to_days({wing: supplied.get(wing, 0) for wing in enabled}, cycle_days, enabled)
        for wing, count in days.items():
            _day_value(count, wing)
    else:
        raise ValueError("type must be DAYS or UNITS")
    if sum(days.values()) != cycle_days:
        raise ValueError("enabled wing days must equal the current cycle length")
    if any(count > cycle_days for count in days.values()):
        raise ValueError("a wing cannot exceed the cycle length")
    return days


def application_status(rows):
    """Derive batch status from existing command rows. Empty is not APPLIED."""
    if not rows:
        return None
    statuses = [str(row["status"]) for row in rows]
    if any(status in IN_FLIGHT for status in statuses):
        return "PARTIALLY_APPLIED" if any(status in APPLIED or status in FAILED for status in statuses) else "APPLYING"
    if all(status in APPLIED for status in statuses):
        return "APPLIED"
    if all(status in FAILED for status in statuses):
        return "FAILED"
    if any(status in FAILED for status in statuses):
        return "PARTIALLY_APPLIED"
    return "APPLYING"


def publish_completed_batch(cur, device_id, society_id, batch_id):
    """Write target_days only when every command in this DAY_BASED batch is completed or acked.

    Until then the previous slot_configs remain the schedule the next Pi sync will deliver.
    A newer batch that is already fully applied is not overwritten. Manual set_days has no
    batch id and does not use this function. Publication is one cloud config update; the Pi
    still applies that config on its next successful sync, not inside the set_days ACK.
    """
    if not batch_id:
        return False
    cur.execute(
        """SELECT slot, params, status, sequence_no FROM pi_commands
           WHERE device_id=%s AND allocation_batch_id=%s FOR UPDATE""",
        (device_id, batch_id),
    )
    rows = list(cur.fetchall())
    if not rows or any(str(row["status"]) not in APPLIED for row in rows):
        return False
    this_max = max(int(row["sequence_no"] or 0) for row in rows)
    cur.execute(
        """SELECT 1 FROM pi_commands
           WHERE device_id=%s AND allocation_batch_id IS NOT NULL AND allocation_batch_id <> %s
           GROUP BY allocation_batch_id
           HAVING bool_and(status IN ('completed', 'acked')) AND max(sequence_no) > %s
           LIMIT 1""",
        (device_id, batch_id, this_max),
    )
    if cur.fetchone():
        return False
    changed = False
    for row in rows:
        params = row["params"] or {}
        days = params.get("days", 0)
        if isinstance(days, bool) or not isinstance(days, int) or not 0 <= int(days) <= 31:
            raise ValueError("batch days must be 0-31")
        days = int(days)
        cur.execute(
            "SELECT target_days FROM slot_configs WHERE device_id=%s AND slot=%s FOR UPDATE",
            (device_id, row["slot"]),
        )
        current = cur.fetchone()
        if current is None or int(current["target_days"]) != days:
            cur.execute(
                "UPDATE slot_configs SET target_days = %s WHERE device_id = %s AND slot = %s",
                (days, device_id, row["slot"]),
            )
            changed = True
    if changed:
        cur.execute("UPDATE societies SET config_version = config_version + 1 WHERE id = %s", (society_id,))
    return changed


def queue_set_days(cur, device_id, days, batch_id, idempotency_key, now):
    """Insert one set_days command per wing in the current transaction. Caller commits."""
    cur.execute("SELECT next_command_sequence FROM pi_devices WHERE id=%s FOR UPDATE", (device_id,))
    row = cur.fetchone()
    sequence = int(row["next_command_sequence"])
    created = []
    for wing in WINGS:
        if wing not in days:
            continue
        sequence += 1
        command_id = str(uuid.uuid4())
        key = f"{idempotency_key}:{wing}"[:128]
        cur.execute("""INSERT INTO pi_commands
                       (id, device_id, command, slot, params, status, created_at, expires_at, idempotency_key, sequence_no, allocation_batch_id)
                       VALUES (%s, %s, 'set_days', %s, %s, 'queued', %s, %s, %s, %s, %s)""",
                    (command_id, device_id, wing, Json({"days": int(days[wing])}), now,
                     now + timedelta(seconds=COMMAND_EXPIRY_SECONDS), key, sequence, batch_id))
        created.append({"command_id": command_id, "slot": wing, "sequence_no": sequence, "days": int(days[wing])})
    cur.execute("UPDATE pi_devices SET next_command_sequence=%s WHERE id=%s", (sequence, device_id))
    return created
