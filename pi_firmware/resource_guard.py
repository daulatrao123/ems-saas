import threading
from logger import logger

class ResourceGuard:
    """
    Centralized policy engine for Storage and Memory protection.
    """
    def __init__(self, storage_io_meter, memory_manager):
        self.io_meter = storage_io_meter
        self.memory_monitor = memory_manager
        self.state = "NORMAL"
        self.lock = threading.Lock()

    def evaluate_state(self):
        with self.lock:
            io_metrics = self.io_meter.get_metrics()
            mem_metrics = self.memory_monitor.get_metrics()
            mem_pressure = self.memory_monitor.is_memory_pressure()
            
            used_percent = io_metrics.get("used_percent", 0)
            budget_exceeded = io_metrics.get("budget_exceeded", False)
            storage_ok = io_metrics.get("storage_ok", True)
            
            new_state = "NORMAL"
            
            if not storage_ok:
                new_state = "STORAGE_FAILED"
            elif used_percent >= 98 or budget_exceeded:
                new_state = "STORAGE_CRITICAL"
            elif used_percent >= 95 or mem_metrics.get("memory_state") == "OOM_DETECTED":
                new_state = "STORAGE_PROTECTED"
            elif used_percent >= 90 or mem_pressure:
                new_state = "WRITE_REDUCED"
                
            if new_state != self.state:
                logger.critical(f"RESOURCE GUARD: State transitioned {self.state} -> {new_state}")
                self.state = new_state

    def is_write_allowed(self, category: str) -> bool:
        with self.lock:
            if self.state == "NORMAL":
                return True
            elif self.state == "WRITE_REDUCED":
                return category in ["critical_log", "state", "queue_db"]
            elif self.state == "STORAGE_PROTECTED":
                return category in ["critical_log", "state"]
            elif self.state in ["STORAGE_CRITICAL", "STORAGE_FAILED"]:
                return category == "critical_log"
            return False