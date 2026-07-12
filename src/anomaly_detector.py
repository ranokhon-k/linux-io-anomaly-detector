#!/usr/bin/env python3
"""
anomaly_detector.py - Main entry point for the monitoring tool.

This ties all the pieces together. Every POLL_INTERVAL seconds it:
  1. Reads disk stats from /proc/diskstats
  2. Computes rates (IOPS, bandwidth, latency)
  3. Feeds latency into the threshold analyzer (mu + 3*sigma check)
  4. Checks which processes are hogging I/O
  5. Periodically looks for starvation

If anything is off, it logs an alert to logs/alerts.json.
Run this with root (or inside the Docker container which runs as root).

Usage:
    python3 anomaly_detector.py
    python3 anomaly_detector.py --interval 0.5 --devices sda
"""

import sys
import os
import time
import signal
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import POLL_INTERVAL_SEC, MONITORED_DEVICES
from utils import setup_logging, get_timestamp
from io_collector import IOCollector
from threshold_analyzer import ThresholdAnalyzer
from process_correlator import ProcessCorrelator
from starvation_monitor import StarvationMonitor


class AnomalyDetector:
    """
    The main loop. Pulls together all the modules and runs them
    on a timer. Each cycle collects data, checks for anomalies,
    and logs anything suspicious.
    """

    def __init__(self):
        self.logger = setup_logging()
        self.collector = IOCollector()
        self.threshold_analyzer = ThresholdAnalyzer()
        self.process_correlator = ProcessCorrelator()
        self.starvation_monitor = StarvationMonitor()
        self._running = False
        self._cycle_count = 0

    def start(self):
        """Kick off the monitoring loop."""
        self._running = True
        self.logger.info("=" * 60)
        self.logger.info("I/O Anomaly Detector starting")
        self.logger.info(f"Poll interval: {POLL_INTERVAL_SEC}s")
        self.logger.info(f"Devices: {MONITORED_DEVICES or 'all'}")
        self.logger.info("=" * 60)

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        try:
            self._monitoring_loop()
        except KeyboardInterrupt:
            self.logger.info("Interrupted.")
        finally:
            self.stop()

    def stop(self):
        self._running = False
        self.logger.info(f"Stopped after {self._cycle_count} cycles.")

    def _signal_handler(self, signum, frame):
        self.logger.info(f"Got signal {signum}, shutting down...")
        self._running = False

    def _monitoring_loop(self):
        """Main loop — collect, analyze, sleep, repeat."""
        while self._running:
            cycle_start = time.time()
            self._cycle_count += 1

            try:
                self._run_cycle()
            except Exception as e:
                self.logger.error(f"Cycle {self._cycle_count} error: {e}")

            elapsed = time.time() - cycle_start
            sleep_time = max(0, POLL_INTERVAL_SEC - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _run_cycle(self):
        """One pass through all the checks."""
        # grab disk counters and compute rates
        disk_stats = self.collector.collect_diskstats()
        io_rates = self.collector.compute_io_rates(disk_stats)

        if not io_rates:
            # first cycle, no previous data to diff against yet
            return

        # check each device for latency anomalies
        anomalies = []
        for device, rates in io_rates.items():
            latency = rates["avg_latency_ms"]

            self.threshold_analyzer.add_sample(device, latency)

            anomaly = self.threshold_analyzer.check_anomaly(device, latency)
            if anomaly:
                anomalies.append(anomaly)
                self.logger.warning(
                    f"[ANOMALY] {device}: latency={latency:.2f}ms "
                    f"(threshold={anomaly['threshold']:.2f}ms, "
                    f"severity={anomaly['severity_sigma']:.1f} sigma)"
                )

            # also check if the queue is getting full
            queue_info = self.collector.collect_device_queue_depth(device)
            self.starvation_monitor.check_queue_saturation(device, queue_info, rates)

        # figure out who's hogging the disk
        process_io = self.collector.collect_process_io()
        hoggers = self.process_correlator.identify_hoggers(process_io, io_rates)

        if hoggers:
            for h in hoggers:
                if h["is_hogger"]:
                    self.logger.warning(
                        f"[HOGGER] {h['comm']} (PID {h['pid']}): "
                        f"{h['total_bw_bytes']/1024/1024:.2f} MB/s "
                        f"({h['bandwidth_ratio']*100:.1f}% of total)"
                    )

        # if there was an anomaly, try to pin it on a hogger
        for anomaly in anomalies:
            self.process_correlator.correlate_with_anomaly(hoggers, anomaly)

        # starvation check (less frequent)
        if self.starvation_monitor.should_check():
            starved = self.starvation_monitor.check_starvation(process_io, io_rates)
            if starved:
                for s in starved:
                    self.logger.warning(
                        f"[STARVATION] {s['comm']} (PID {s['pid']}): "
                        f"wait={s['wait_time_ms_per_sec']:.0f}ms/s, "
                        f"throughput={s['bytes_completed']}B"
                    )

        # print a status summary every 60 cycles
        if self._cycle_count % 60 == 0:
            self._log_status(io_rates)

    def _log_status(self, io_rates):
        """Periodic summary so we know things are still running."""
        self.logger.info(f"--- Status (cycle {self._cycle_count}) ---")
        for device, rates in io_rates.items():
            stats = self.threshold_analyzer.get_statistics(device)
            if stats:
                self.logger.info(
                    f"  {device}: mu={stats['mu']:.2f}ms, sigma={stats['sigma']:.2f}ms, "
                    f"threshold={stats['threshold']:.2f}ms, "
                    f"IOPS={rates['read_iops']+rates['write_iops']:.0f}"
                )


def parse_args():
    parser = argparse.ArgumentParser(
        description="I/O Anomaly Detector for Linux"
    )
    parser.add_argument("--interval", type=float, default=None,
                       help=f"Poll interval in seconds (default: {POLL_INTERVAL_SEC})")
    parser.add_argument("--devices", nargs="+", default=None,
                       help="Devices to monitor (e.g. sda nvme0n1)")
    parser.add_argument("--duration", type=int, default=None,
                       help="Run for N seconds then stop (for testing)")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.interval:
        import config
        config.POLL_INTERVAL_SEC = args.interval

    if args.devices:
        import config
        config.MONITORED_DEVICES = args.devices

    # warn if not root — we need it for /proc/[pid]/io
    if os.geteuid() != 0:
        print("WARNING: Not running as root. Some /proc reads will fail.")
        print("Run with: sudo python3 anomaly_detector.py")
        print()

    detector = AnomalyDetector()

    if args.duration:
        # run for a fixed time then stop (useful for automated testing)
        import threading
        timer = threading.Timer(args.duration, detector.stop)
        timer.daemon = True
        timer.start()

    detector.start()


if __name__ == "__main__":
    main()
