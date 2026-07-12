"""
process_correlator.py - Figures out which process is eating all the I/O.

When the system slows down, it's usually because one process is hogging
the disk. This module tracks per-process I/O rates (from /proc/[pid]/io)
and flags any process that's using more than 70% of the total bandwidth.

It also ties things together: when the threshold analyzer flags a latency
spike, this module can point at the process that probably caused it.
"""

import time

from config import TOP_N_HOGGERS, HOGGER_BANDWIDTH_THRESHOLD
from utils import log_alert


class ProcessCorrelator:
    """
    Tracks per-process I/O and identifies bandwidth hogs.
    Works by comparing consecutive snapshots of /proc/[pid]/io to compute rates.
    """

    def __init__(self):
        self._prev_process_io = {}
        self._prev_sample_time = None

    def update_process_stats(self, process_io_list):
        """Save current snapshot for next comparison."""
        current_time = time.time()
        current_stats = {}

        for proc in process_io_list:
            pid = proc["pid"]
            current_stats[pid] = {
                "comm": proc.get("comm", "unknown"),
                "read_bytes": proc.get("read_bytes", 0),
                "write_bytes": proc.get("write_bytes", 0),
                "syscr": proc.get("syscr", 0),
                "syscw": proc.get("syscw", 0),
                "timestamp": current_time,
            }

        self._prev_process_io = current_stats
        self._prev_sample_time = current_time

    def identify_hoggers(self, process_io_list, system_io_rates):
        """
        Compare current per-process I/O with previous snapshot.
        Sort by bandwidth, flag anything over the threshold.

        Returns top N processes with their rates and a hogger flag.
        """
        current_time = time.time()

        if not self._prev_process_io or not self._prev_sample_time:
            self.update_process_stats(process_io_list)
            return []

        dt = current_time - self._prev_sample_time
        if dt <= 0:
            dt = 1.0

        process_rates = []
        for proc in process_io_list:
            pid = proc["pid"]
            if pid not in self._prev_process_io:
                continue

            prev = self._prev_process_io[pid]
            read_delta = proc.get("read_bytes", 0) - prev["read_bytes"]
            write_delta = proc.get("write_bytes", 0) - prev["write_bytes"]

            # negative delta means the process restarted, skip it
            if read_delta < 0 or write_delta < 0:
                continue

            read_rate = read_delta / dt
            write_rate = write_delta / dt
            total_rate = read_rate + write_rate

            if total_rate > 0:
                process_rates.append({
                    "pid": pid,
                    "comm": proc.get("comm", "unknown"),
                    "read_bw_bytes": read_rate,
                    "write_bw_bytes": write_rate,
                    "total_bw_bytes": total_rate,
                    "syscr_rate": (proc.get("syscr", 0) - prev["syscr"]) / dt,
                    "syscw_rate": (proc.get("syscw", 0) - prev["syscw"]) / dt,
                })

        process_rates.sort(key=lambda x: x["total_bw_bytes"], reverse=True)

        total_system_io = sum(p["total_bw_bytes"] for p in process_rates)

        hoggers = []
        for proc in process_rates[:TOP_N_HOGGERS]:
            bandwidth_ratio = proc["total_bw_bytes"] / total_system_io if total_system_io > 0 else 0
            proc["bandwidth_ratio"] = round(bandwidth_ratio, 4)
            proc["is_hogger"] = bandwidth_ratio >= HOGGER_BANDWIDTH_THRESHOLD

            if proc["is_hogger"]:
                log_alert(
                    alert_type="IO_HOGGER",
                    message=f"Process {proc['comm']} (PID {proc['pid']}) consuming "
                            f"{bandwidth_ratio*100:.1f}% of I/O bandwidth "
                            f"({proc['total_bw_bytes']/1024/1024:.2f} MB/s)",
                    details=proc
                )

            hoggers.append(proc)

        self.update_process_stats(process_io_list)
        return hoggers

    def correlate_with_anomaly(self, hoggers, anomaly_info):
        """
        When a latency anomaly fires, check if any hogger process is
        likely the cause. Logs a correlation alert if so.
        """
        if not hoggers or not anomaly_info:
            return None

        correlation = {
            "anomaly": anomaly_info,
            "probable_causes": [],
        }

        for proc in hoggers:
            if proc["is_hogger"]:
                correlation["probable_causes"].append({
                    "pid": proc["pid"],
                    "comm": proc["comm"],
                    "bandwidth_ratio": proc["bandwidth_ratio"],
                    "total_bw_MB_s": round(proc["total_bw_bytes"] / 1024 / 1024, 3),
                })

        if correlation["probable_causes"]:
            log_alert(
                alert_type="ANOMALY_CORRELATION",
                message=f"Latency anomaly on {anomaly_info['device']} likely caused by: "
                        + ", ".join(f"{c['comm']}(PID:{c['pid']})"
                                   for c in correlation["probable_causes"]),
                details=correlation
            )

        return correlation
