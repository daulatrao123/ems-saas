"""EMS energy subsystem (E1): 1 common generation meter (M1) + 4 wing consumption meters (M2..M5).
Additive and isolated: no GPIO, no command queue, no FSM. See meter_manager.EnergyEngine."""
from .meter_manager import EnergyEngine  # noqa: F401
from .meter_registry import GENERATION_METER, METER_IDS, WING_METER  # noqa: F401
