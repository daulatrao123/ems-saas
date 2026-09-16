"""Commissioning validation, without importing or executing Pi firmware."""
import hashlib
import json
import math
from fastapi import HTTPException


def check_expected(data, version):
    if "expected_version" not in data:
        return  # Existing clients remain compatible.
    expected = data["expected_version"]
    if type(expected) is not int or expected < 0:
        raise HTTPException(400, "expected_version must be a non-negative integer")
    if expected != version:
        raise HTTPException(409, "Energy configuration changed; discard the draft and refresh before saving")


def basis_hash(bills):
    return hashlib.sha256(json.dumps(bills["months"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def register_problems(rmap):
    if not isinstance(rmap, dict):
        return ["register_map must be an object"]
    problems = [] if rmap.get("verified") is True else ["Confirm the register map against the installed meter datasheet"]
    regs = rmap.get("registers")
    if not isinstance(regs, dict) or "energy_total_kwh" not in regs:
        return problems + ["registers.energy_total_kwh is required"]
    types = {"uint16": 1, "int16": 1, "uint32": 2, "int32": 2, "float32": 2, "uint64": 4, "int64": 4, "float64": 4}
    for key, reg in regs.items():
        units = {"energy_total_kwh": ("kWh", "Wh", "MWh"), "power_kw": ("kW", "W", "MW")}
        if key not in units or not isinstance(reg, dict):
            problems.append(f"Unsupported or invalid register {key}"); continue
        address = reg.get("address")
        if type(address) is not int or not 0 <= address <= 65535:
            problems.append(f"{key}.address must be an integer 0..65535")
        if reg.get("type") not in types:
            problems.append(f"{key}.type invalid")
        if type(reg.get("function", 3)) is not int or reg.get("function", 3) not in (3, 4):
            problems.append(f"{key}.function must be 3 or 4")
        if reg.get("word_order", "big") not in ("big", "little"):
            problems.append(f"{key}.word_order must be big or little")
        try:
            scale = reg.get("scale", 1)
            if isinstance(scale, bool) or not math.isfinite(float(scale)) or float(scale) <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            problems.append(f"{key}.scale must be finite and positive")
        if reg.get("unit") not in units[key]:
            problems.append(f"{key}.unit invalid")
    return problems