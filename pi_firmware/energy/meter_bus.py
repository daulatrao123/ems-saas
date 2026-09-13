"""RS485 / Modbus RTU bus abstraction. One isolated USB-RS485 adapter, five meters on the same wire.
Real bus = minimalmodbus + pyserial (imported lazily so tests and non-metering Pis need neither).
The energy engine only ever calls MeterBus.read_registers(); it never touches serial or GPIO itself."""
import threading

DEFAULT_SERIAL = {"baudrate": 9600, "bytesize": 8, "parity": "N", "stopbits": 1, "timeout_s": 0.5}


class BusError(Exception):
    pass


class MeterBus:
    """Interface: read_registers(address, register, count, function) -> list[int]. Raises BusError."""

    def read_registers(self, address, register, count, function):
        raise NotImplementedError

    def close(self):
        pass


class MinimalModbusBus(MeterBus):
    """Serialises access to the single RS485 line (one transaction at a time)."""

    def __init__(self, port, serial=None):
        self.port = port
        self.serial = {**DEFAULT_SERIAL, **(serial or {})}
        self._lock = threading.Lock()
        self._instruments = {}
        try:
            import minimalmodbus
            import serial as _pyserial  # noqa: F401
        except ImportError as exc:
            raise BusError(f"minimalmodbus/pyserial not installed: {exc}")
        self._mm = minimalmodbus

    def _instrument(self, address):
        inst = self._instruments.get(address)
        if inst is None:
            inst = self._mm.Instrument(self.port, address, mode=self._mm.MODE_RTU, close_port_after_each_call=False)
            inst.serial.baudrate = int(self.serial["baudrate"])
            inst.serial.bytesize = int(self.serial["bytesize"])
            inst.serial.parity = {"N": "N", "E": "E", "O": "O"}[str(self.serial["parity"]).upper()]
            inst.serial.stopbits = int(self.serial["stopbits"])
            inst.serial.timeout = float(self.serial["timeout_s"])
            inst.clear_buffers_before_each_transaction = True
            self._instruments[address] = inst
        return inst

    def read_registers(self, address, register, count, function):
        with self._lock:
            try:
                return list(self._instrument(address).read_registers(register, count, functioncode=function))
            except Exception as exc:  # IOError / ValueError / serial exceptions -> one bus error type
                raise BusError(f"{type(exc).__name__}: {exc}")

    def close(self):
        with self._lock:
            for inst in self._instruments.values():
                try:
                    inst.serial.close()
                except Exception:
                    pass
            self._instruments.clear()


class MockMeterBus(MeterBus):
    """Test double: registers[(address, register)] = [words]; failures[address] = error or None."""

    def __init__(self):
        self.registers = {}
        self.failures = {}
        self.calls = []

    def set_words(self, address, register, words):
        self.registers[(address, register)] = list(words)

    def read_registers(self, address, register, count, function):
        self.calls.append((address, register, count, function))
        if self.failures.get(address):
            raise BusError(self.failures[address])
        words = self.registers.get((address, register))
        if words is None or len(words) != count:
            raise BusError(f"no response from address {address} register {register}")
        return list(words)


def build_bus(config):
    """config = {"port": "/dev/serial/by-id/...", "serial": {...}} -> real bus, or None when unconfigured."""
    port = (config or {}).get("port")
    if not port:
        return None
    return MinimalModbusBus(port, (config or {}).get("serial"))
