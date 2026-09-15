"""Current logical wing scope for consumption presentation, never dispatch policy."""
from .queries import WINGS


def consumption_scope(cur, did):
    cur.execute("SELECT slot, disabled FROM slot_configs WHERE device_id=%s", (did,))
    # Missing configuration is not proof that a wing is unused. Only an explicit
    # logical disable excludes it; meter availability/feedback never does.
    disabled = {r["slot"] for r in cur.fetchall() if r.get("disabled") is True}
    return {"included_wings": [w for w in WINGS if w not in disabled],
            "excluded_wings": [w for w in WINGS if w in disabled],
            "scope_basis": "CURRENT_LOGICAL_CONFIGURATION"}