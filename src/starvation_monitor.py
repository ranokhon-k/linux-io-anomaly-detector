"""
starvation_monitor.py - Detects when processes get starved of I/O.

Starvation happens when one or more processes hog the disk and other
processes can't get their I/O served. The classic sign: a process is
actively trying to do I/O (it has pending syscalls) but it's barely
getting any throughput, while its scheduler wait time keeps climbing.

We detect this by reading /proc/[pid]/schedstat which gives us the
cumulative wait time in nanoseconds. If the wait is growing fast but
actual bytes transferred is near zero, that process is being starved.

Also checks queue saturation — if the device queue is >80% full,
that's a sign the disk can't keep up and starvation becomes likely.
"""

import os
import time
from pathlib import Path

from config import (STARVATION_WAIT_THRESHOLD_MS, STARVATION_CHECK_INTERVAL,
                    PROC_DIR, SYS_BLOCK)
from utils import read_file_safe, log_alert


class StarvationMonitor:
    """
    Watches for I/O starvation by tracking process wait times
    and comparing them against actual throughput.
    """

    def __init__(self):
        self._cycle_counter = 0
        self._prev_process_waits = {}

    def should_check(self):
        """
        This check is expensive (reads schedstat for every process),
        so we only do it every N cycles.
        """
        self._cycle_counter += 1
        return self._cycle_counter % STARVATION_CHECK_INTERVAL == 0

    def check_starvation(self, process_io_list, system_io_rates):
        """
        Look for starved processes. The logic:
          - Read /proc/[pid]/schedstat to get wait_time (nanoseconds)
          - Compare with previous reading to get wait growth rate
          - If wait is growing fast BUT the process barely moved any bytes,
            it's being starved

        Returns list of starved process info dicts (empty if none found).
        """
        starved_processes = []
        current_time = time.time()

        for proc in process_io_list:
            pid = proc["pid"]

            # schedstat format: run_time_ns wait_time_ns nr_switches
            schedstat = read_file_safe(os.path.join(PROC_DIR, str(pid), "schedstat"))
            if schedstat is None:
                continue

            parts = schedstat.strip().split()
            if len(parts) < 3:
                continue

            wait_time_ns = int(parts[1])

            if pid in self._prev_process_waits:
                prev = self._prev_process_waits[pid]
                dt = current_time - prev["timestamp"]
                if dt <= 0:
                    continue

                wait_delta_ns = wait_time_ns - prev["wait_time_ns"]
                wait_delta_ms = wait_delta_ns / 1_000_000

                # does this process have I/O activity?
                has_io_activity = (proc.get("syscr", 0) + proc.get("syscw", 0)) > 0
                actual_bytes = proc.get("read_bytes", 0) + proc.get("write_bytes", 0)
                prev_bytes = prev.get("total_bytes", 0)
                bytes_delta = actual_bytes - prev_bytes

                # starvation = lots of waiting + almost no bytes getting through
                avg_wait_per_sec = wait_delta_ms / dt if dt > 0 else 0

                if (avg_wait_per_sec > STARVATION_WAIT_THRESHOLD_MS and
                    has_io_activity and bytes_delta < 4096):

                    starvation_info = {
                        "pid": pid,
                        "comm": proc.get("comm", "unknown"),
                        "wait_time_ms_per_sec": round(avg_wait_per_sec, 2),
                        "bytes_completed": bytes_delta,
                        "threshold_ms": STARVATION_WAIT_THRESHOLD_MS,
                    }
                    starved_processes.append(starvation_info)

                    log_alert(
                        alert_type="IO_STARVATION",
                        message=f"Process {proc.get('comm', 'unknown')} (PID {pid}) "
                                f"starved: wait={avg_wait_per_sec:.0f}ms/s, "
                                f"throughput={bytes_delta}B in {dt:.1f}s",
                        details=starvation_info
                    )

            # save for next time
            self._prev_process_waits[pid] = {
                "wait_time_ns": wait_time_ns,
                "timestamp": current_time,
                "total_bytes": proc.get("read_bytes", 0) + proc.get("write_bytes", 0),
            }

        # clean up dead processes so we don't leak memory
        active_pids = {proc["pid"] for proc in process_io_list}
        stale_pids = set(self._prev_process_waits.keys()) - active_pids
        for pid in stale_pids:
            del self._prev_process_waits[pid]

        return starved_processes

    def check_queue_saturation(self, device, queue_info, io_rates):
        """
        If the device queue is almost full (>80%), the disk is overwhelmed.
        This is usually a precondition for starvation.
        """
        if not queue_info or queue_info["nr_requests"] is None:
            return None

        max_depth = queue_info["nr_requests"]
        current_depth = queue_info.get("in_flight", 0)

        if max_depth <= 0:
            return None

        utilization = current_depth / max_depth

        if utilization > 0.8:
            saturation_info = {
                "device": device,
                "current_depth": current_depth,
                "max_depth": max_depth,
                "utilization": round(utilization, 3),
            }

            log_alert(
                alert_type="QUEUE_SATURATION",
                message=f"Device {device} queue saturated: "
                        f"{current_depth}/{max_depth} ({utilization*100:.1f}%)",
                details=saturation_info
            )
            return saturation_info

        return None
