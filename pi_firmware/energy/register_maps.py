"""Configurable Modbus register maps. NO meter model is assumed: a map is usable only when it is
explicitly marked verified (filled from the meter's datasheet) and passes validate_register_map()."""
import math
import struct

REQUIRED_REGISTER = "energy_total_kwh"      # cumulative import/total active energy, the only mandatory register
OPTIONAL_REGISTERS = ("power_kw",)          # instantaneous active power (display only, never used for kWh)
VALUE_TYPES = {"uint16": 1, "int16": 1, "uint32": 2, "int32": 2, "float32": 2, "uint64": 4, "int64": 4, "float64": 4}
FUNCTION_CODES = (3, 4)
WORD_ORDERS = ("big", "little")

# Template only. Every address/type/scale MUST come from the installed meter's documentation.
GENERIC_TEMPLATE = {
    "name": "GENERIC_TEMPLATE",
    "verified": False,
    "registers": {
        "energy_total_kwh": {"address": None, "function": 3, "type": "float32", "word_order": "big", "scale": 1.0, "unit": "kWh"},
        "power_kw": {"address": None, "function": 3, "type": "float32", "word_order": "big", "scale": 1.0, "unit": "kW"},
    },
}

BUILTIN_MAPS = {"GENERIC_TEMPLATE": GENERIC_TEMPLATE}


def validate_register_map(rmap):
    """Returns a list of human-readable problems (empty list == usable)."""
    problems = []
    if not isinstance(rmap, dict):
        return ["register map must be an object"]
    if rmap.get("verified") is not True:
        problems.append("register map is not marked verified (fill it from the meter datasheet first)")
    regs = rmap.get("registers")
    if not isinstance(regs, dict) or REQUIRED_REGISTER not in regs:
        return problems + [f"register map must define registers.{REQUIRED_REGISTER}"]
    for key, reg in regs.items():
        if key != REQUIRED_REGISTER and key not in OPTIONAL_REGISTERS:
            problems.append(f"unknown register key {key}")
            continue
        if not isinstance(reg, dict):
            problems.append(f"{key}: must be an object"); continue
        addr = reg.get("address")
        if not isinstance(addr, int) or isinstance(addr, bool) or not 0 <= addr <= 0xFFFF:
            problems.append(f"{key}: address must be an integer 0..65535")
        if reg.get("function", 3) not in FUNCTION_CODES:
            problems.append(f"{key}: function must be 3 or 4")
        if reg.get("type") not in VALUE_TYPES:
            problems.append(f"{key}: type must be one of {sorted(VALUE_TYPES)}")
        if reg.get("word_order", "big") not in WORD_ORDERS:
            problems.append(f"{key}: word_order must be big or little")
        try:
            if float(reg.get("scale", 1.0)) <= 0:
                problems.append(f"{key}: scale must be > 0")
        except (TypeError, ValueError):
            problems.append(f"{key}: scale must be numeric")
        unit = reg.get("unit")
        if key == REQUIRED_REGISTER and unit not in ("kWh", "Wh", "MWh"):
            problems.append(f"{key}: unit must be kWh, Wh or MWh (got {unit})")
        if key == "power_kw" and unit not in ("kW", "W", "MW"):
            problems.append(f"{key}: unit must be kW, W or MW (got {unit})")
    return problems


def resolve_register_map(spec):
    """spec = builtin name (str) or inline map (dict). Returns (map, problems)."""
    if isinstance(spec, str):
        rmap = BUILTIN_MAPS.get(spec)
        if rmap is None:
            return None, [f"unknown register map {spec}"]
    else:
        rmap = spec
    return rmap, validate_register_map(rmap)


_UNIT_TO_KWH = {"kWh": 1.0, "Wh": 0.001, "MWh": 1000.0}
_UNIT_TO_KW = {"kW": 1.0, "W": 0.001, "MW": 1000.0}


def word_count(reg):
    return VALUE_TYPES[reg["type"]]


def decode_registers(words, reg):
    """Raw 16-bit register words -> engineering value in kWh / kW (unit conversion explicit)."""
    if len(words) != word_count(reg):
        raise ValueError(f"expected {word_count(reg)} words, got {len(words)}")
    ordered = list(words) if reg.get("word_order", "big") == "big" else list(reversed(words))
    raw = b"".join(struct.pack(">H", w & 0xFFFF) for w in ordered)
    fmt = {"uint16": ">H", "int16": ">h", "uint32": ">I", "int32": ">i", "float32": ">f", "uint64": ">Q", "int64": ">q", "float64": ">d"}[reg["type"]]
    value = struct.unpack(fmt, raw)[0]
    if math.isnan(value) or math.isinf(value):
        raise ValueError("non-finite value")
    value = float(value) * float(reg.get("scale", 1.0))
    unit = reg.get("unit")
    if unit in _UNIT_TO_KWH:
        return value * _UNIT_TO_KWH[unit]
    if unit in _UNIT_TO_KW:
        return value * _UNIT_TO_KW[unit]
    raise ValueError(f"unsupported unit {unit}")
