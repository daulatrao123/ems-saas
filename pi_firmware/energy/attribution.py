"""M1 generation attribution from VERIFIED contactor feedback only (never desired state, UI or cloud)."""
WINGS = ("A", "B", "C", "D")
ATTRIBUTED = "ATTRIBUTED"
UNATTRIBUTED = "UNATTRIBUTED"
FAULT = "FAULT"


def attribute(feedback):
    """feedback = {"A": "ON"|"OFF"|"UNKNOWN"|"PENDING", ...} -> (wing|None, status, reason).
    Exactly one ON -> that wing. None ON -> UNATTRIBUTED. More than one ON -> FAULT."""
    on = [w for w in WINGS if str((feedback or {}).get(w, "UNKNOWN")).upper() == "ON"]
    if len(on) == 1:
        return on[0], ATTRIBUTED, None
    if len(on) > 1:
        return None, FAULT, f"multiple wings physically active: {''.join(on)}"
    unknown = [w for w in WINGS if str((feedback or {}).get(w, "UNKNOWN")).upper() not in ("ON", "OFF")]
    if len(unknown) == len(WINGS):
        return None, UNATTRIBUTED, "contactor feedback unavailable"
    return None, UNATTRIBUTED, "no wing physically active" if not unknown else f"no wing ON, feedback unknown for {''.join(unknown)}"
