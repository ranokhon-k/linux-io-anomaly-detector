"""
config.py - All the tuneable parameters live here.

I put everything in one place so I don't have to hunt through
the logic every time I want to change a number. These defaults
worked well for me during testing on Ubuntu 22.04 in Docker,
but depending on the disk hardware you might want to tweak them.
"""

import os

# --- Monitoring intervals ---
POLL_INTERVAL_SEC = 1.0          # how often we poll /proc (seconds)
WINDOW_SIZE = 60                 # sliding window for computing mu and sigma

# --- Anomaly detection ---
# Core formula: anomaly when latency > mu + 3*sigma
SIGMA_MULTIPLIER = 3.0
MIN_SAMPLES_FOR_DETECTION = 10   # wait for at least this many before flagging anything

# --- Process monitoring ---
TOP_N_HOGGERS = 5                # how many top processes to track
HOGGER_BANDWIDTH_THRESHOLD = 0.7 # 70% of total I/O = hogger

# --- Starvation ---
STARVATION_WAIT_THRESHOLD_MS = 500   # ms of wait time per second = starved
STARVATION_CHECK_INTERVAL = 5        # run starvation check every N cycles (expensive)

# --- Logging ---
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
LOG_FILE = "anomaly_detector.log"
ALERT_LOG_FILE = "alerts.json"
LOG_LEVEL = "INFO"

# --- Which disks to watch ---
# Empty = monitor everything we find in /proc/diskstats
MONITORED_DEVICES = []

# --- Kernel interface paths ---
PROC_DISKSTATS = "/proc/diskstats"
SYS_BLOCK = "/sys/block"
PROC_DIR = "/proc"
