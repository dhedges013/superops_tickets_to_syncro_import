import os
import logging
from datetime import datetime

# syncro_configs.py
SYNCRO_TIMEZONE = "America/New_York"
TICKETS_CSV_PATH = "tickets.csv"
COMMENTS_CSV_PATH = "ticket_comments.csv"
TEMP_FILE_PATH = "syncro_temp_data.json"

# Syncro API Configuration
# SYNCRO_SUBDOMAIN = "subdomain"
# SYNCRO_API_KEY = "your_api_key"
DEFAULT_SYNCRO_SUBDOMAIN = "subdomain"
DEFAULT_SYNCRO_API_KEY = "your_api_key"
SYNCRO_SUBDOMAIN = DEFAULT_SYNCRO_SUBDOMAIN
SYNCRO_API_KEY = DEFAULT_SYNCRO_API_KEY

try:
    from local_config import SYNCRO_SUBDOMAIN as LOCAL_SYNCRO_SUBDOMAIN
    from local_config import SYNCRO_API_KEY as LOCAL_SYNCRO_API_KEY

    SYNCRO_SUBDOMAIN = LOCAL_SYNCRO_SUBDOMAIN
    SYNCRO_API_KEY = LOCAL_SYNCRO_API_KEY
except ImportError:
    pass

SYNCRO_API_BASE_URL = f"https://{SYNCRO_SUBDOMAIN}.syncromsp.com/api/v1"

# Rate limiting configuration
RATE_LIMIT_SECONDS = 0.5

# Logging Configuration
LOG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "logs"))
os.makedirs(LOG_DIR, exist_ok=True)
RUN_ID = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_FILE_NAME = f"import_{RUN_ID}.log"
LOG_FILE_PATH = os.path.join(LOG_DIR, LOG_FILE_NAME)


class RunContextFilter(logging.Filter):
    """Ensure import log records always include a stable run_id."""

    def filter(self, record):
        if not hasattr(record, "run_id"):
            record.run_id = RUN_ID
        return True


_logging_configured = False


def configure_logging():
    """Configure a single shared file logger for the current import run."""
    global _logging_configured
    if _logging_configured:
        return

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.INFO)

    file_handler = logging.FileHandler(LOG_FILE_PATH, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.addFilter(RunContextFilter())
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s run_id=%(run_id)s logger=%(name)s level=%(levelname)s %(message)s"
        )
    )

    root_logger.addHandler(file_handler)
    _logging_configured = True

def get_logger(name):
    configure_logging()
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = True
    return logger


def get_log_file_path():
    configure_logging()
    return LOG_FILE_PATH

# Reset root logger to prevent console logging
logging.getLogger().handlers.clear()
