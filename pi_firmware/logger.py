import os
import logging
import logging.handlers
from datetime import datetime
from config import LOG_DIR, DAILY_LOG_BUDGET_BYTES

class DailyBudgetHandler(logging.Handler):
    """
    Enforces a true daily write budget (≤10 MB/day).
    Drops INFO/WARNING if budget is exceeded. CRITICAL/ERROR always logged.
    """
    def __init__(self, filename, budget_bytes):
        super().__init__()
        self.base_filename = filename
        self.budget_bytes = budget_bytes
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        self.current_filename = f"{self.base_filename}.{self.current_date}"
        self.bytes_written = 0
        self.fh = open(self.current_filename, 'a')
        
        # Get current file size for initialization
        try:
            self.bytes_written = os.path.getsize(self.current_filename)
        except FileNotFoundError:
            self.bytes_written = 0

    def _rotate_if_new_day(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self.current_date:
            self.fh.close()
            self.current_date = today
            self.current_filename = f"{self.base_filename}.{self.current_date}"
            self.bytes_written = 0
            self.fh = open(self.current_filename, 'a')

    def emit(self, record):
        self._rotate_if_new_day()
        msg = self.format(record) + "\n"
        msg_bytes = len(msg.encode('utf-8'))
        
        # Always allow CRITICAL and ERROR
        if record.levelno >= logging.ERROR:
            self.fh.write(msg)
            self.fh.flush()
            self.bytes_written += msg_bytes
            return
            
        # Check budget for INFO/WARNING
        if self.bytes_written + msg_bytes <= self.budget_bytes:
            self.fh.write(msg)
            self.fh.flush()
            self.bytes_written += msg_bytes

def setup_logger():
    logger = logging.getLogger("EMS")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s', 
        datefmt='%Y-%m-%dT%H:%M:%S%z'
    )
    
    # 1. Bounded daily application log
    app_handler = DailyBudgetHandler(
        os.path.join(LOG_DIR, "ems_app.log"), 
        DAILY_LOG_BUDGET_BYTES
    )
    app_handler.setFormatter(formatter)
    app_handler.setLevel(logging.INFO)
    logger.addHandler(app_handler)
    
    # 2. Critical events only (bounded 1 MB rotating)
    crit_handler = logging.handlers.RotatingFileHandler(
        os.path.join(LOG_DIR, "critical.log"),
        maxBytes=1*1024*1024,
        backupCount=2
    )
    crit_handler.setFormatter(formatter)
    crit_handler.setLevel(logging.ERROR)
    logger.addHandler(crit_handler)

    # 3. Stdout for debugging
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger

logger = setup_logger()