"""
utils.py - Shared helper functions used across all modules.

Handles logging setup, alert writing, and file reading.
Nothing too fancy here, just stuff I got tired of repeating in every file.
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path

from config import LOG_DIR, LOG_FILE, ALERT_LOG_FILE, LOG_LEVEL


def setup_logging():
    """Set up file + console logging. Returns a logger instance."""
    log_path = Path(LOG_DIR)
    log_path.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path / LOG_FILE),
            logging.StreamHandler()
        ]
    )
    return logging.getLogger("io_anomaly_detector")


def log_alert(alert_type, message, details=None):
    """
    Append a structured alert to alerts.json.
    Each alert has a timestamp, type, message, and optional details dict.
    """
    log_path = Path(LOG_DIR)
    log_path.mkdir(parents=True, exist_ok=True)

    alert = {
        "timestamp": datetime.now().isoformat(),
        "type": alert_type,
        "message": message,
        "details": details or {}
    }

    alert_file = log_path / ALERT_LOG_FILE
    alerts = []
    if alert_file.exists():
        try:
            with open(alert_file, "r") as f:
                alerts = json.load(f)
        except (json.JSONDecodeError, IOError):
            alerts = []

    alerts.append(alert)

    with open(alert_file, "w") as f:
        json.dump(alerts, f, indent=2)

    return alert


def read_file_safe(filepath):
    """Try to read a file. If anything goes wrong, just return None."""
    try:
        with open(filepath, "r") as f:
            return f.read()
    except (IOError, PermissionError, FileNotFoundError, TypeError):
        return None


def parse_diskstats_line(line):
    """
    Parse one line from /proc/diskstats (Linux only).

    The format has 14+ fields per the kernel docs (4.18+):
    major minor name reads_completed reads_merged sectors_read read_ms
    writes_completed writes_merged sectors_written write_ms
    ios_in_progress io_ms weighted_io_ms
    """
    parts = line.split()
    if len(parts) < 14:
        return None

    return {
        "major": int(parts[0]),
        "minor": int(parts[1]),
        "device": parts[2],
        "reads_completed": int(parts[3]),
        "reads_merged": int(parts[4]),
        "sectors_read": int(parts[5]),
        "read_time_ms": int(parts[6]),
        "writes_completed": int(parts[7]),
        "writes_merged": int(parts[8]),
        "sectors_written": int(parts[9]),
        "write_time_ms": int(parts[10]),
        "ios_in_progress": int(parts[11]),
        "io_time_ms": int(parts[12]),
        "weighted_io_time_ms": int(parts[13]),
    }


def get_timestamp():
    """Current time as ISO string."""
    return datetime.now().isoformat()
