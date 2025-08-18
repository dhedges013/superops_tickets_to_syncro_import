import os
import logging
from datetime import datetime

# syncro_configs.py
SYNCRO_TIMEZONE = "America/New_York"
TICKETS_CSV_PATH = "tickets.csv"
COMMENTS_CSV_PATH = "ticket_comments.csv"
TEMP_FILE_PATH = "syncro_temp_data.json"

# Syncro API Configuration
SYNCRO_SUBDOMAIN = "subdomain"
SYNCRO_API_KEY = "your_api_key"

SYNCRO_API_BASE_URL = f"https://{SYNCRO_SUBDOMAIN}.syncromsp.com/api/v1"

# Rate limiting configuration
RATE_LIMIT_SECONDS = 0.5

# Logging Configuration
LOG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "logs"))
os.makedirs(LOG_DIR, exist_ok=True)

def get_logger(name):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Create a new log file with the current date and time
    log_file_name = f"app_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_file_path = os.path.join(LOG_DIR, log_file_name)

    # File handler with UTF-8 encoding
    file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
    file_handler.setLevel(logging.INFO)

    # Log format
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)

    # Remove other handlers to prevent duplicate logs
    if logger.hasHandlers():
        logger.handlers.clear()

    logger.addHandler(file_handler)

    # Prevent logs from propagating to the root logger
    logger.propagate = False

    return logger

# Reset root logger to prevent console logging
logging.getLogger().handlers.clear()
