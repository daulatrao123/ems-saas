"""Exactly five physical meters: M1 common generation + M2..M5 wing consumption.
Physical identity (serial) is separate from the Modbus address; one address == one physical meter."""
from .register_maps import resolve_register_map

GENERATION = "GENERATION"
CONSUMPTION = "CONSUMPTION"
METER_IDS = ("M1", "M2", "M3", "M4", "M5")
METER_ROLE = {"M1": GENERATION, "M2": CONSUMPTION, "M3": CONSUMPTION, "M4": CONSUMPTION, "M5": CONSUMPTION}
METER_WING = {"M1": None, "M2": "A", "M3": "B", "M4": "C", "M5": "D"}
WING_METER = {"A": "M2", "B": "M3", "C": "M4", "D": "M5"}
GENERATION_METER = "M1"


class RegistryError(ValueError):
    pass


class MeterDefinition:
    __slots__ = (
        "config_problems",
        "ct_ratio",
        "enabled",
        "max_kw",
        "meter_id",
        "modbus_address",
        "model",
        "phases",
        "register_map",
        "register_map_name",
        "role",
        "serial",
        "wing",
    )

    def __init__(self, meter_id, cfg):
        self.meter_id = meter_id
        self.role = METER_ROLE[meter_id]
        self.wing = METER_WING[meter_id]
        cfg = cfg or {}
        self.serial = (str(cfg["serial"]).strip() or None) if cfg.get("serial") is not None else None
        addr = cfg.get("modbus_address")
        self.modbus_address = int(addr) if isinstance(addr, int) and not isinstance(addr, bool) else None
        self.model = cfg.get("model")
        self.phases = cfg.get("phases")
        self.ct_ratio = cfg.get("ct_ratio")
        self.max_kw = cfg.get("max_kw")  # optional plausibility bound for deltas
        self.enabled = bool(cfg.get("enabled", False))
        spec = cfg.get("register_map")
        self.register_map_name = spec if isinstance(spec, str) else (spec or {}).get("name") if isinstance(spec, dict) else None
        self.register_map, problems = (resolve_register_map(spec) if spec is not None else (None, ["no register map configured"]))
        if self.enabled:
            if self.modbus_address is None or not 1 <= self.modbus_address <= 247:
                problems.append("modbus_address must be 1..247")
            if not self.serial:
                problems.append("physical serial number is required")
        self.config_problems = problems

    @property
    def pollable(self):
        return self.enabled and not self.config_problems

    def as_dict(self):
        return {"meter_id": self.meter_id, "role": self.role, "wing": self.wing, "serial": self.serial,
                "modbus_address": self.modbus_address, "model": self.model, "register_map": self.register_map_name,
                "phases": self.phases, "ct_ratio": self.ct_ratio, "enabled": self.enabled,
                "config_problems": list(self.config_problems)}


def build_registry(config):
    """config = {"meters": {"M1": {...}, ...}}. Always returns all five meters (unconfigured ones disabled).
    Raises RegistryError for structural violations (unknown ids, duplicate addresses/serials)."""
    meters_cfg = (config or {}).get("meters") or {}
    unknown = sorted(set(meters_cfg) - set(METER_IDS))
    if unknown:
        raise RegistryError(f"unknown meter ids {unknown}; exactly {list(METER_IDS)} exist")
    registry = {mid: MeterDefinition(mid, meters_cfg.get(mid)) for mid in METER_IDS}
    seen_addr, seen_serial = {}, {}
    for m in registry.values():
        if m.enabled and m.modbus_address is not None:
            if m.modbus_address in seen_addr:
                raise RegistryError(f"Modbus address {m.modbus_address} assigned to both {seen_addr[m.modbus_address]} and {m.meter_id}")
            seen_addr[m.modbus_address] = m.meter_id
        if m.serial:
            if m.serial in seen_serial:
                raise RegistryError(f"serial {m.serial} assigned to both {seen_serial[m.serial]} and {m.meter_id}")
            seen_serial[m.serial] = m.meter_id
    assert sum(1 for m in registry.values() if m.role == GENERATION) == 1
    assert sum(1 for m in registry.values() if m.role == CONSUMPTION) == 4
    return registry
