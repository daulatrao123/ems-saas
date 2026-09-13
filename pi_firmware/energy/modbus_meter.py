"""ModbusMeter: reads one physical meter through the shared bus and returns a typed Reading.
Decoding/scaling/unit conversion comes from the configured register map only."""
import time

from .meter_bus import BusError
from .register_maps import REQUIRED_REGISTER, decode_registers, word_count


class Reading:
    __slots__ = ("cumulative_kwh", "error", "meter_id", "ok", "power_kw", "ts")

    def __init__(self, meter_id, ok, cumulative_kwh=None, power_kw=None, ts=None, error=None):
        self.meter_id, self.ok, self.cumulative_kwh, self.power_kw, self.ts, self.error = meter_id, ok, cumulative_kwh, power_kw, ts, error

    def as_dict(self):
        return {"meter_id": self.meter_id, "ok": self.ok, "cumulative_kwh": self.cumulative_kwh, "power_kw": self.power_kw, "ts": self.ts, "error": self.error}


class ModbusMeter:
    def __init__(self, definition, bus, now=time.time):
        self.definition = definition
        self.bus = bus
        self.now = now

    def _read(self, key):
        reg = self.definition.register_map["registers"][key]
        words = self.bus.read_registers(self.definition.modbus_address, int(reg["address"]), word_count(reg), int(reg.get("function", 3)))
        return decode_registers(words, reg)

    def read(self):
        d = self.definition
        ts = self.now()
        if not d.pollable:
            return Reading(d.meter_id, False, ts=ts, error="not configured: " + "; ".join(d.config_problems))
        try:
            kwh = self._read(REQUIRED_REGISTER)
            if kwh < 0:
                return Reading(d.meter_id, False, ts=ts, error=f"negative cumulative energy {kwh}")
        except (BusError, ValueError, KeyError) as exc:
            return Reading(d.meter_id, False, ts=ts, error=f"{type(exc).__name__}: {exc}")
        power = None
        if "power_kw" in d.register_map["registers"]:
            try:
                power = self._read("power_kw")
            except (BusError, ValueError):
                power = None  # optional, never blocks the energy reading
        return Reading(d.meter_id, True, cumulative_kwh=kwh, power_kw=power, ts=ts)
