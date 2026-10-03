"""Software-command recovery. Hardware commands are never selected here.

A lost COMPLETED acknowledgement can leave set_days, set_reset_day, reset_days,
restart, reboot, or lcd_display in executing. After the existing expires_at
deadline the cloud expires that row and does not write configuration. It does
not return the row to queued and it does not touch set_active_slot, off_slot,
or off_all.
"""
try:
    import cloud_obs
except ImportError:  # repo-root test imports this module as backend.command_reliability
    from backend import cloud_obs

SOFTWARE_COMMANDS = (
    "set_days",
    "set_reset_day",
    "reset_days",
    "restart",
    "reboot",
    "lcd_display",
)
HARDWARE_COMMANDS = ("set_active_slot", "off_slot", "off_all")


def newer_set_days_committed(cur, device_id, slot, sequence_no):
    """True when a later set_days for this device and slot is already completed or acked."""
    if isinstance(sequence_no, bool) or not isinstance(sequence_no, int):
        return False
    cur.execute(
        """SELECT 1 AS stale FROM pi_commands
           WHERE device_id=%s AND command='set_days' AND slot=%s
             AND sequence_no > %s AND status IN ('completed', 'acked')
           LIMIT 1""",
        (device_id, slot, sequence_no),
    )
    return cur.fetchone() is not None


def expire_stuck_software_commands(cur, device_id, now):
    """Expire executing software commands whose expires_at has passed.

    Does not write slot_configs or societies. Caller owns the transaction.
    Observation failures are ignored.
    """
    cur.execute(
        """UPDATE pi_commands
           SET status='expired', error='SOFTWARE_EXECUTING_EXPIRED'
           WHERE device_id=%s AND status='executing' AND expires_at <= %s
             AND command = ANY(%s)
           RETURNING id, command, slot, sequence_no, allocation_batch_id""",
        (device_id, now, list(SOFTWARE_COMMANDS)),
    )
    rows = list(cur.fetchall() or [])
    for row in rows:
        fields = {
            "device_id": str(device_id),
            "command_id": str(row.get("id")),
            "command": row.get("command"),
            "sequence_no": row.get("sequence_no"),
            "previous_status": "executing",
            "resulting_status": "expired",
            "recovery_reason": "SOFTWARE_EXECUTING_EXPIRED",
            "allocation_batch_id": str(row.get("allocation_batch_id")) if row.get("allocation_batch_id") else None,
            "idempotent": False,
            "recovered": True,
            "expired": True,
        }
        cloud_obs.safe_observe("ack", lambda fields=fields: cloud_obs.note_software_recovery(fields))
    return rows
