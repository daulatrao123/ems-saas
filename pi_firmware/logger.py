import logging
import logging.handlers
import os

LOG_DIR = "/var/log/ems"
LOG_FILE = os.path.join(LOG_DIR, "ems_app.log")

def setup_logger():
    """Configures a strictly size-capped rotating logger."""
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)

    logger = logging.getLogger("EMS")
    logger.setLevel(logging.INFO)
    
    # Prevent multiple handlers if called twice
    if logger.handlers:
        return logger

    # 5 MB per file, 2 backups = 15 MB absolute maximum disk usage
    # This enforces your "<=10 MB/day" application budget
    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, 
        maxBytes=5*1024*1024, 
        backupCount=2
    )
    
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s', 
        datefmt='%Y-%m-%dT%H:%M:%S%z'
    )
    handler.setFormatter(formatter)
    
    logger.addHandler(handler)
    
    # Also output to stdout for real-time debugging during development
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger

# Global logger instance
logger = setup_logger()