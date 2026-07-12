"""
io_collector.py - Grabs I/O metrics from the kernel.

This is where all the data collection happens. We read from:
  - /proc/diskstats for disk-level counters
  - /sys/block/<dev>/stat and queue/ for queue depth and scheduler info
  - /proc/<pid>/io for per-process I/O accounting

The trick is that /proc/diskstats gives us cumulative counters, so we need
two samples to compute rates. That's what compute_io_rates() does — it
compares the current snapshot with the previous one.
"""

import os
import time
from pathlib import Path

from config import PROC_DISKSTATS, SYS_BLOCK, PROC_DIR, MONITORED_DEVICES
from utils import read_file_safe, parse_diskstats_line


class IOCollector:
    """Reads I/O stats from /proc and /sys."""

    def __init__(self):
        self._prev_stats = {}
        self._prev_time = None

    def collect_diskstats(self):
        """
        Parse /proc/diskstats. Returns a dict keyed by device name.
        We skip partitions (like sda1, sda2) and only keep whole disks.
        """
        content = read_file_safe(PROC_DISKSTATS)
        if content is None:
            return {}

        stats = {}
        for line in content.strip().split("\n"):
            parsed = parse_diskstats_line(line)
            if parsed is None:
                continue

            device = parsed["device"]

            if MONITORED_DEVICES and device not in MONITORED_DEVICES:
                continue

            # skip partitions — if "sda1" and "sda" already exists, skip it
            if device[-1].isdigit() and device.rstrip("0123456789") in stats:
                continue

            stats[device] = parsed

        return stats

    def collect_device_queue_depth(self, device):
        """
        Check how full the device queue is.
        nr_requests = max slots, in_flight = currently occupied.
        """
        base_path = Path(SYS_BLOCK) / device

        nr_requests = read_file_safe(base_path / "queue" / "nr_requests")
        stat_content = read_file_safe(base_path / "stat")

        result = {"device": device, "nr_requests": None, "in_flight": None}

        if nr_requests:
            result["nr_requests"] = int(nr_requests.strip())

        if stat_content:
            parts = stat_content.split()
            if len(parts) >= 9:
                # field 9 in /sys/block/<dev>/stat is ios currently in progress
                result["in_flight"] = int(parts[8])

        return result

    def collect_scheduler_info(self, device):
        """
        Find out which I/O scheduler is active.
        The kernel shows it like: [bfq] mq-deadline kyber none
        Active one is in brackets.
        """
        sched_path = Path(SYS_BLOCK) / device / "queue" / "scheduler"
        content = read_file_safe(str(sched_path))
        if content is None:
            return "unknown"

        for token in content.split():
            if token.startswith("[") and token.endswith("]"):
                return token[1:-1]
        return "unknown"

    def collect_process_io(self):
        """
        Walk through /proc and read each process's I/O counters.
        Returns a list of dicts with pid, comm, read_bytes, write_bytes, etc.

        Some processes won't be readable (permission denied) so we just skip those.
        """
        processes = []

        try:
            pids = [d for d in os.listdir(PROC_DIR) if d.isdigit()]
        except OSError:
            return processes

        for pid in pids:
            io_path = os.path.join(PROC_DIR, pid, "io")
            comm_path = os.path.join(PROC_DIR, pid, "comm")

            io_content = read_file_safe(io_path)
            comm_content = read_file_safe(comm_path)

            if io_content is None:
                continue

            proc_info = {
                "pid": int(pid),
                "comm": comm_content.strip() if comm_content else "unknown"
            }

            for line in io_content.strip().split("\n"):
                if ":" in line:
                    key, val = line.split(":", 1)
                    proc_info[key.strip()] = int(val.strip())

            processes.append(proc_info)

        return processes

    def compute_io_rates(self, current_stats):
        """
        Given a fresh snapshot from collect_diskstats(), compute the delta
        from the previous snapshot and return rates per device.

        First call returns empty (we need 2 points to compute a rate).
        After that, we get: IOPS, bandwidth, average latency per device.
        """
        current_time = time.time()
        rates = {}

        if self._prev_stats and self._prev_time:
            dt = current_time - self._prev_time
            if dt <= 0:
                dt = 1.0

            for device, curr in current_stats.items():
                if device not in self._prev_stats:
                    continue

                prev = self._prev_stats[device]

                reads_delta = curr["reads_completed"] - prev["reads_completed"]
                writes_delta = curr["writes_completed"] - prev["writes_completed"]
                read_sectors_delta = curr["sectors_read"] - prev["sectors_read"]
                write_sectors_delta = curr["sectors_written"] - prev["sectors_written"]
                read_time_delta = curr["read_time_ms"] - prev["read_time_ms"]
                write_time_delta = curr["write_time_ms"] - prev["write_time_ms"]
                io_time_delta = curr["weighted_io_time_ms"] - prev["weighted_io_time_ms"]

                total_ops = reads_delta + writes_delta

                # weighted_io_time / total_ops gives us average latency
                avg_latency_ms = 0.0
                if total_ops > 0:
                    avg_latency_ms = io_time_delta / total_ops

                rates[device] = {
                    "read_iops": reads_delta / dt,
                    "write_iops": writes_delta / dt,
                    "read_bw_bytes": (read_sectors_delta * 512) / dt,
                    "write_bw_bytes": (write_sectors_delta * 512) / dt,
                    "avg_latency_ms": avg_latency_ms,
                    "read_time_ms": read_time_delta,
                    "write_time_ms": write_time_delta,
                    "queue_depth": curr["ios_in_progress"],
                }

        self._prev_stats = current_stats
        self._prev_time = current_time
        return rates
