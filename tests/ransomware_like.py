#!/usr/bin/env python3
"""
ransomware_like.py - Simulates a ransomware I/O pattern.

The idea: ransomware reads files in small chunks, XORs (encrypts) the data,
writes it back, then fsyncs. This creates tons of small random I/O ops,
which saturates IOPS without necessarily maxing out bandwidth in MB/s.

This should trigger IO_HOGGER and LATENCY_ANOMALY alerts from the detector.
"""

import os
import sys
import time
import random
import argparse
import shutil
from pathlib import Path


def create_test_files(test_dir, num_files=500, file_size_kb=64):
    """Make a bunch of small files to act as "victim" data."""
    print(f"[*] Creating {num_files} test files ({file_size_kb}KB each)...")
    test_dir.mkdir(parents=True, exist_ok=True)

    files = []
    for i in range(num_files):
        filepath = test_dir / f"file_{i:04d}.dat"
        with open(filepath, "wb") as f:
            f.write(os.urandom(file_size_kb * 1024))
        files.append(filepath)

    total_mb = (num_files * file_size_kb) / 1024
    print(f"    Created {total_mb:.1f} MB of test data")
    return files


def ransomware_io_pattern(files, duration_sec, block_size=4096):
    """
    Do what ransomware does:
      - pick a random file
      - read small chunks from random positions
      - XOR the bytes (fake encryption)
      - write them back to the same positions
      - fsync (so the OS can't just buffer it)

    This hammers IOPS because of all the small random ops + forced flushes.
    """
    print(f"[*] Starting ransomware-like I/O...")
    print(f"    Block size: {block_size}B, Duration: {duration_sec}s")
    print()

    start_time = time.time()
    ops_count = 0
    bytes_processed = 0
    files_processed = 0

    file_order = list(files)
    random.shuffle(file_order)
    file_idx = 0

    while time.time() - start_time < duration_sec:
        target_file = file_order[file_idx % len(file_order)]
        file_idx += 1

        try:
            # read phase: small random reads
            fd_read = os.open(str(target_file), os.O_RDONLY)
            file_size = os.fstat(fd_read).st_size

            num_reads = min(file_size // block_size, 16)
            if num_reads < 1:
                os.close(fd_read)
                continue

            max_blocks = file_size // block_size
            positions = random.sample(
                [i * block_size for i in range(max_blocks)],
                min(num_reads, max_blocks)
            )

            data_chunks = []
            for pos in positions:
                os.lseek(fd_read, pos, os.SEEK_SET)
                data = os.read(fd_read, block_size)
                data_chunks.append((pos, data))
                ops_count += 1
                bytes_processed += len(data)

            os.close(fd_read)

            # write phase: XOR and write back
            fd_write = os.open(str(target_file), os.O_WRONLY)

            for pos, data in data_chunks:
                encrypted = bytes(b ^ 0xAA for b in data)
                os.lseek(fd_write, pos, os.SEEK_SET)
                os.write(fd_write, encrypted)
                ops_count += 1
                bytes_processed += len(encrypted)

            # fsync forces it to hit the disk
            os.fsync(fd_write)
            os.close(fd_write)
            ops_count += 1

            files_processed += 1

        except (OSError, IOError, ValueError):
            continue

        if files_processed % 100 == 0 and files_processed > 0:
            elapsed = time.time() - start_time
            iops = ops_count / elapsed if elapsed > 0 else 0
            bw_mb = bytes_processed / (1024 * 1024 * elapsed) if elapsed > 0 else 0
            print(f"    [{elapsed:.0f}s] Files: {files_processed}, "
                  f"IOPS: {iops:.0f}, BW: {bw_mb:.2f} MB/s")

    elapsed = time.time() - start_time
    print()
    print(f"[*] Done:")
    print(f"    Duration: {elapsed:.1f}s")
    print(f"    Files processed: {files_processed}")
    print(f"    Total ops: {ops_count}")
    print(f"    Avg IOPS: {ops_count/elapsed:.0f}")
    print(f"    Avg BW: {bytes_processed/(1024*1024*elapsed):.2f} MB/s")


def main():
    parser = argparse.ArgumentParser(description="Ransomware-like I/O simulator")
    parser.add_argument("--dir", type=str, default="/tmp/ransomware_io_test")
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--num-files", type=int, default=500)
    parser.add_argument("--file-size", type=int, default=64,
                       help="File size in KB")
    parser.add_argument("--block-size", type=int, default=4096)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()

    test_dir = Path(args.dir)

    print("=" * 50)
    print(" Ransomware-like I/O Pattern Test")
    print("=" * 50)

    try:
        files = create_test_files(test_dir, args.num_files, args.file_size)

        print("[*] Letting caches settle (3s)...")
        time.sleep(3)

        ransomware_io_pattern(files, args.duration, args.block_size)

    except KeyboardInterrupt:
        print("\n[*] Interrupted.")
    finally:
        if not args.no_cleanup:
            print("[*] Cleaning up...")
            shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
