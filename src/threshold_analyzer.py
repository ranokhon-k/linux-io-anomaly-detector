"""
threshold_analyzer.py - The core anomaly detection logic.

The idea here is pretty simple from a stats perspective:
  1. Keep a sliding window of recent latency measurements
  2. Compute the mean (mu) and standard deviation (sigma)
  3. If the current value is more than 3 sigma above the mean, flag it

This is basically a z-score approach. The 3-sigma rule means we only flag
things that are really unusual (99.7% of normal data falls within 3 sigma).
The sliding window lets it adapt — so if the system gets generally slower,
the baseline adjusts and we don't keep crying wolf.
"""

import numpy as np
from collections import deque

from config import WINDOW_SIZE, SIGMA_MULTIPLIER, MIN_SAMPLES_FOR_DETECTION
from utils import log_alert


class ThresholdAnalyzer:
    """
    Keeps a per-device sliding window of latency samples.
    Flags anomalies when latency > mu + 3*sigma.
    """

    def __init__(self, window_size=WINDOW_SIZE, sigma_multiplier=SIGMA_MULTIPLIER):
        self._window_size = window_size
        self._sigma_multiplier = sigma_multiplier
        self._windows = {}  # device_name -> deque of floats

    def add_sample(self, device, latency_ms):
        """Push a new latency value into the device's window."""
        if device not in self._windows:
            self._windows[device] = deque(maxlen=self._window_size)
        self._windows[device].append(latency_ms)

    def check_anomaly(self, device, latency_ms):
        """
        Check whether latency_ms is anomalous for this device.
        Returns a dict with details if anomaly detected, None otherwise.

        We don't start checking until we have MIN_SAMPLES_FOR_DETECTION samples,
        otherwise the stats would be meaningless.
        """
        if device not in self._windows:
            return None

        window = self._windows[device]

        if len(window) < MIN_SAMPLES_FOR_DETECTION:
            return None

        samples = np.array(window)
        mu = np.mean(samples)
        sigma = np.std(samples)

        threshold = mu + (self._sigma_multiplier * sigma)

        if latency_ms > threshold and sigma > 0:
            # how many sigmas above the mean are we?
            severity_sigma = (latency_ms - mu) / sigma

            anomaly_info = {
                "device": device,
                "latency_ms": round(latency_ms, 3),
                "mu": round(mu, 3),
                "sigma": round(sigma, 3),
                "threshold": round(threshold, 3),
                "severity_sigma": round(severity_sigma, 2),
                "window_size": len(window),
            }

            log_alert(
                alert_type="LATENCY_ANOMALY",
                message=f"Device {device}: latency {latency_ms:.1f}ms exceeds "
                        f"threshold {threshold:.1f}ms (mu={mu:.1f}, sigma={sigma:.1f})",
                details=anomaly_info
            )

            return anomaly_info

        return None

    def get_statistics(self, device):
        """Get a summary of the current window stats for a device."""
        if device not in self._windows or len(self._windows[device]) == 0:
            return None

        samples = np.array(self._windows[device])
        mu = np.mean(samples)
        sigma = np.std(samples)

        return {
            "device": device,
            "mu": round(mu, 3),
            "sigma": round(sigma, 3),
            "threshold": round(mu + self._sigma_multiplier * sigma, 3),
            "sample_count": len(samples),
            "min": round(np.min(samples), 3),
            "max": round(np.max(samples), 3),
            "p95": round(np.percentile(samples, 95), 3),
        }

    def reset(self, device=None):
        """Clear the window for one device or all of them."""
        if device:
            self._windows.pop(device, None)
        else:
            self._windows.clear()
