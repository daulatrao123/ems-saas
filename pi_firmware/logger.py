import os
import logging
import logging.handlers
import threading
import time
from datetime import datetime
from config import LOG_DIR, DAILY_LOG_BUDGET_BYTES, CRITICAL_LOG_BUDGET_BYTES

# Global storage manager reference to check write budgets
_storage_mgr = None

def set_storage_manager(storage_mgr):
    global _storage_mgr
    _storage_mgr = storage_mgr

class DailyBudgetHandler(logging.Handler):
    def __init__(self, filename, budget_bytes):
        super().__init__()
        self.base_filename = filename
        self.budget_bytes = budget_bytes
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        self.current_filename = f"{self.base_filename}.{self.current_date}"
        self.bytes_written = 0
        self.fh = open(self.current_filename, 'a')
        try:
            self.bytes_written = os.path.getsize(self.current_filename)
        except FileNotFoundError:
            self.bytes_written = 0
            
        self._running = True
        self.flush_thread = threading.Thread(target=self._periodic_flush, daemon=True)
        self.flush_thread.start()

    def _periodic_flush(self):
        while self._running:
            time.sleep(60.0)
            self.fh.flush()

    def _rotate_if_new_day(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self.current_date:
            self.fh.flush()
            self.fh.close()
            self.current_date = today
            self.current_filename = f"{self.base_filename}.{self.current_date}"
            self.bytes_written = 0
            self.fh = open(self.current_filename, 'a')

    def emit(self, record):
        self._rotate_if_new_day()
        
        # HARD BUDGET STOP: Do not write if USB budget exceeded
        if _storage_mgr and not _storage_mgr.is_write_allowed():
            return
            
        msg = self.format(record) + "\n"
        msg_bytes = len(msg.encode('utf-8'))
        
        if self.bytes_written + msg_bytes <= self.budget_bytes:
            self.fh.write(msg)
            self.bytes_written += msg_bytes

def setup_logger():
    logger = logging.getLogger("EMS")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger

    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%dT%H:%M:%S%z')
    
    app_handler = DailyBudgetHandler(os.path.join(LOG_DIR, "ems_app.log"), DAILY_LOG_BUDGET_BYTES)
    app_handler.setFormatter(formatter)
    app_handler.setLevel(logging.INFO)
    app_handler.addFilter(lambda record: record.levelno < logging.ERROR)
    logger.addHandler(app_handler)
    
    crit_handler = logging.handlers.RotatingFileHandler(
        os.path.join(LOG_DIR, "critical.log"), maxBytes=CRITICAL_LOG_BUDGET_BYTES, backupCount=2
    )
    crit_handler.setFormatter(formatter)
    crit_handler.setLevel(logging.ERROR)
    logger.addHandler(crit_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger

logger = setup_logger()