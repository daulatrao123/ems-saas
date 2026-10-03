"""Single writer for the hardware capability document.

Legacy columns are derived from that document. They are not a second source of truth.
"""

import hardware_capabilities as hwcap


class CapabilityWriteError(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def load_document(raw):
    if isinstance(raw, dict):
        try:
            return hwcap.canonical_capabilities(raw)
        except hwcap.CapabilityError:
            return hwcap.default_capabilities()
    return hwcap.default_capabilities()


def document_for_feedback(current, installed, channels=None):
    """Build the canonical document for a feedback-installation change.

    channels is the requested per-wing enable map. When installation is false,
    every channel is disabled. A missing channel is disabled. This does not
    invent installed hardware beyond the requested flag.
    """
    doc = load_document(current)
    installed = bool(installed)
    doc["contactor_feedback"]["installed"] = installed
    for wing in hwcap.WINGS:
        requested = False if channels is None else bool(channels.get(wing))
        doc["contactor_feedback"]["channels"][wing] = {"enabled": bool(installed and requested)}
    return hwcap.canonical_capabilities(doc)


def document_for_registration(installed):
    """Registration records only the explicit installed flag. Channels stay off."""
    return document_for_feedback(None, installed, {wing: False for wing in hwcap.WINGS})


def persist_capabilities(cur, device_id, society_id, proposed, *, bump_config=True):
    """Persist the document and derive the compatibility columns from it.

    Returns True when a legacy feedback field actually changed.
    """
    installed = bool(proposed["contactor_feedback"]["installed"])
    from psycopg.types.json import Json
    cur.execute("SELECT feedback_hardware_installed FROM pi_devices WHERE id=%s", (device_id,))
    prev = cur.fetchone()
    changed = bool(prev["feedback_hardware_installed"]) != installed if prev else True
    for wing, channel in proposed["contactor_feedback"]["channels"].items():
        enabled = bool(channel["enabled"])
        cur.execute(
            """UPDATE slot_configs SET feedback_enabled=%s
               WHERE device_id=%s AND slot=%s AND feedback_enabled IS DISTINCT FROM %s""",
            (enabled, device_id, wing, enabled),
        )
        changed = changed or cur.rowcount > 0
    cur.execute(
        "UPDATE pi_devices SET hardware_capabilities=%s, feedback_hardware_installed=%s WHERE id=%s",
        (Json(proposed), installed, device_id),
    )
    if bump_config and changed and society_id:
        cur.execute("UPDATE societies SET config_version = config_version + 1 WHERE id=%s", (society_id,))
    return changed


def sync_meter_enables(cur, device_id, proposed):
    """Derive energy_meters.enabled from the capability document. Does not change allocation."""
    meter_changed = False
    mapping = {
        "M1": proposed["generation_meter"],
        **{hwcap.METER_BY_WING[wing]: proposed["consumption_meters"][wing] for wing in hwcap.WINGS},
    }
    for meter_id, component in mapping.items():
        cur.execute(
            "SELECT enabled, serial, modbus_address, register_map FROM energy_meters WHERE device_id=%s AND meter_id=%s",
            (device_id, meter_id),
        )
        meter = cur.fetchone()
        if not meter:
            continue
        if component["enabled"] and not meter["enabled"]:
            if not meter["serial"] or meter["modbus_address"] is None or not meter["register_map"]:
                raise CapabilityWriteError(409, f"Cannot enable {meter_id} from hardware capabilities until serial, address, and register map exist")
        desired_enabled = bool(component["installed"] and component["enabled"])
        if bool(meter["enabled"]) != desired_enabled:
            cur.execute(
                """UPDATE energy_meters
                   SET enabled=%s,
                       comm_status=CASE WHEN %s THEN comm_status ELSE 'DISABLED' END,
                       updated_at=NOW()
                   WHERE device_id=%s AND meter_id=%s""",
                (desired_enabled, desired_enabled, device_id, meter_id),
            )
            meter_changed = True
    if meter_changed:
        cur.execute("UPDATE pi_devices SET energy_config_version=energy_config_version+1 WHERE id=%s", (device_id,))
