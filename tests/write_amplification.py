#!/usr/bin/env python3
"""
write_amplification.py - Tests what happens when fsync() goes crazy.

fsync() tells the OS "flush this file to disk RIGHT NOW, don't buffer it."
Normally the page cache batches writes for efficiency. When a process calls
fsync() after every tiny write, the disk has to do way more physical I/O
than the logical data would suggest. That's write amplification.

This test spawns multiple threads doing small writes + immediate fsyncs.
Meanwhile a "victim" thread does normal I/O and measures its own latency.
The victim should get starved because the fsync threads hog the queue.

Should trigger: IO_STARVATION, QUEUE_SATURATION, LATENCY_ANOMALY alerts.
"""

import os
import sys
import time
import argparse
import threading
import tempfile
from pathlib import Path


class FsyncStorm:
    """Hammers the disk with small writes + fsync after each one."""

    def __init__(self, test_dir, num_writers=4, write_size=512, fsync_every=1):
        self.test_dir = Path(test_dir)
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.num_writers = num_writers
        self.write_size = write_size
        self.fsync_every = fsync_every
        self._running = False
        self._stats = {"total_writes": 0, "total_fsyncs": 0, "total_bytes": 0}
        self._lock = threading.Lock()

    def start(self, duration_sec):
        """Run the storm for the given number of seconds."""
        self._running = True
        threads = []

        print(f"[*] Starting fsync storm:")
        print(f"    Writers: {self.num_writers}")
        print(f"    Write size: {self.write_size} bytes")
        print(f"    fsync every: {self.fsync_every} write(s)")
        print()

        for i in range(self.num_writers):
            t = threading.Thread(target=self._writer_thread, args=(i, duration_sec))
            t.daemon = True
            threads.append(t)
            t.start()

        # Progress reporting
        start_time = time.time()
        while time.time() - start_time < duration_sec and self._running:
            time.sleep(5)
            elapsed = time.time() - start_time
            with self._lock:
                writes = self._stats["total_writes"]
                fsyncs = self._stats["total_fsyncs"]
                total_bytes = self._stats["total_bytes"]

            write_rate = writes / elapsed if elapsed > 0 else 0
            fsync_rate = fsyncs / elapsed if elapsed > 0 else 0
            bw = total_bytes / (1024 * 1024 * elapsed) if elapsed > 0 else 0

            print(f"    [{elapsed:.0f}s] writes: {writes}, fsyncs: {fsyncs}, "
                  f"rate: {write_rate:.0f} w/s, {fsync_rate:.0f} fsync/s, "
                  f"BW: {bw:.2f} MB/s")

        self._running = False
        for t in threads:
            t.join(timeout=5)

        # Final stats
        elapsed = time.time() - start_time
        print()
        print(f"[*] Fsync storm complete:")
        print(f"    Total writes: {self._stats['total_writes']}")
        print(f"    Total fsyncs: {self._stats['total_fsyncs']}")
        print(f"    Total data: {self._stats['total_bytes']/(1024*1024):.1f} MB")
        print(f"    Avg write rate: {self._stats['total_writes']/elapsed:.0f}/s")
        print(f"    Avg fsync rate: {self._stats['total_fsyncs']/elapsed:.0f}/s")

    def _writer_thread(self, thread_id, duration_sec):
        """Each thread just writes tiny chunks and fsyncs like crazy."""
        filepath = self.test_dir / f"fsync_test_{thread_id}.dat"
        start_time = time.time()
        local_writes = 0
        local_fsyncs = 0
        local_bytes = 0

        try:
            fd = os.open(str(filepath), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)

            while time.time() - start_time < duration_sec and self._running:
                # Small write
                data = os.urandom(self.write_size)
                os.write(fd, data)
                local_writes += 1
                local_bytes += self.write_size

                # Frequent fsync (the key behavior causing write amplification)
                if local_writes % self.fsync_every == 0:
                    os.fsync(fd)
                    local_fsyncs += 1

                # Periodically update shared stats
                if local_writes % 100 == 0:
                    with self._lock:
                        self._stats["total_writes"] += 100
                        self._stats["total_fsyncs"] += (100 // self.fsync_every)
                        self._stats["total_bytes"] += 100 * self.write_size
                    local_writes = 0
                    local_fsyncs = 0

                    # Truncate file periodically to avoid filling disk
                    os.ftruncate(fd, 0)
                    os.lseek(fd, 0, os.SEEK_SET)

            os.close(fd)

        except OSError as e:
            print(f"    Writer {thread_id} error: {e}")

        # Update remaining stats
        with self._lock:
            self._stats["total_writes"] += local_writes
            self._stats["total_fsyncs"] += local_fsyncs
            self._stats["total_bytes"] += local_bytes

    def cleanup(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)


class BackgroundVictim:
    """
    Does normal I/O in the background while the storm rages.
    We measure its latency to see how much the fsync storm hurts it.
    """

    def __init__(self, filepath):
        self.filepath = filepath
        self._running = False
        self._thread = None
        self.latencies = []

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._io_loop)
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _io_loop(self):
        """Write 4KB every 0.5s and time how long it takes."""
        while self._running:
            start = time.time()
            try:
                with open(self.filepath, "ab") as f:
                    f.write(os.urandom(4096))
                    f.flush()
                    os.fsync(f.fileno())
            except OSError:
                pass
            latency_ms = (time.time() - start) * 1000
            self.latencies.append(latency_ms)
            time.sleep(0.5)  # Normal, infrequent I/O

    def get_stats(self):
        if not self.latencies:
            return None
        import numpy as np
        arr = np.array(self.latencies)
        return {
            "count": len(arr),
            "mean_ms": round(np.mean(arr), 2),
            "p50_ms": round(np.median(arr), 2),
            "p95_ms": round(np.percentile(arr, 95), 2),
            "p99_ms": round(np.percentile(arr, 99), 2),
            "max_ms": round(np.max(arr), 2),
        }


def main():
    parser = argparse.ArgumentParser(description="Write Amplification (fsync storm) test")
    parser.add_argument("--duration", type=int, default=60,
                       help="Test duration in seconds")
    parser.add_argument("--intensity", choices=["low", "medium", "high"], default="high",
                       help="Storm intensity level")
    parser.add_argument("--dir", type=str, default="/tmp/write_amp_test",
                       help="Directory for test files")
    parser.add_argument("--no-cleanup", action="store_true",
                       help="Don't remove test files after completion")
    args = parser.parse_args()

    # Configure intensity
    intensity_config = {
        "low":    {"num_writers": 2, "write_size": 1024, "fsync_every": 10},
        "medium": {"num_writers": 4, "write_size": 512,  "fsync_every": 3},
        "high":   {"num_writers": 8, "write_size": 256,  "fsync_every": 1},
    }
    config = intensity_config[args.intensity]

    print("=" * 50)
    print(" Write Amplification (fsync storm) Test")
    print(f" Intensity: {args.intensity}")
    print("=" * 50)
    print()

    storm = FsyncStorm(args.dir, **config)

    # Start background victim process
    victim_file = os.path.join(args.dir, "victim_io.dat")
    os.makedirs(args.dir, exist_ok=True)
    victim = BackgroundVictim(victim_file)

    try:
        print("[*] Starting background victim process...")
        victim.start()

        # Let victim establish baseline (10 seconds)
        print("[*] Establishing baseline (10s)...")
        time.sleep(10)

        baseline_stats = victim.get_stats()
        if baseline_stats:
            print(f"    Baseline latency: mean={baseline_stats['mean_ms']:.1f}ms, "
                  f"p95={baseline_stats['p95_ms']:.1f}ms")
        print()

        # Start fsync storm
        storm.start(args.duration)

        # Stop victim and get final stats
        victim.stop()
        final_stats = victim.get_stats()

        print()
        print("[*] Victim process I/O impact:")
        if final_stats:
            print(f"    Mean latency: {final_stats['mean_ms']:.1f}ms")
            print(f"    P95 latency:  {final_stats['p95_ms']:.1f}ms")
            print(f"    P99 latency:  {final_stats['p99_ms']:.1f}ms")
            print(f"    Max latency:  {final_stats['max_ms']:.1f}ms")

            if baseline_stats and baseline_stats['mean_ms'] > 0:
                amplification = final_stats['mean_ms'] / baseline_stats['mean_ms']
                print(f"    Latency amplification: {amplification:.1f}x")

    except KeyboardInterrupt:
        print("\n[*] Interrupted by user.")
        victim.stop()
    finally:
        if not args.no_cleanup:
            print("[*] Cleaning up...")
            storm.cleanup()

    print()
    print("=" * 50)
    print(" Check logs/alerts.json for IO_STARVATION events")
    print("=" * 50)


if __name__ == "__main__":
    main()
