# I/O Anomaly Detector for Linux

Operating Systems Module A project. University of Messina, 2025/2026.

## What this does

This is a background monitoring tool that watches Linux disk I/O and detects
anomalies in real time. It reads stats directly from the kernel (`/proc` and
`/sys`), builds up a rolling baseline, and fires alerts when something unusual
happens.

Three types of problems it catches:

1. **Latency spikes** when disk response time shoots above mu + 3*sigma
2. **Bandwidth hoggers** when one process consumes more than 70% of all I/O
3. **I/O starvation** when a process keeps waiting but gets zero throughput

Everything runs inside a Docker container (Ubuntu 22.04) so you don't need
a separate Linux machine. One command and it does the whole thing.

## How to run it

### Prerequisites

- Docker Desktop installed and running
- That's literally it

### On Windows (easiest way)

Double-click `run.bat`. It checks Docker is running, builds the container,
runs both test scenarios, and shows you the results.

Or from a terminal:

```
run.bat
```

### From any OS with Docker

```bash
docker-compose up --build
```

### What happens when you run it

1. Builds a Docker container with Ubuntu 22.04 + Python + numpy
2. Starts the anomaly detector (1 sample per second)
3. Collects baseline for 15 seconds
4. Runs the ransomware-like test for 30 seconds (tons of small random I/O)
5. 10 second cooldown
6. Runs the write amplification test for 30 seconds (fsync storm)
7. Stops and prints a summary

Total time: about 2 minutes on first run (mostly downloading Ubuntu image).
Subsequent runs are faster because Docker caches the build.

### Where results go

After it finishes, check:

- `logs/alerts.json` for the structured alert data (what got flagged, when, why)
- `logs/anomaly_detector.log` for the full monitoring trace

You can also run `docker logs io_anomaly_detector` to see the console output again.

### Running it again

If you already ran it once and want to re-run:

```bash
docker-compose down
docker-compose up --build
```

Or just run `run.bat` again on Windows.

## Project layout

```
├── Dockerfile              # Container definition (Ubuntu 22.04 + deps)
├── docker-compose.yml      # One-command orchestration
├── run.bat                 # Windows one-click runner
├── run_experiment.sh       # The script that runs inside the container
├── requirements.txt        # Python dependency (numpy)
├── src/
│   ├── anomaly_detector.py # Main loop, ties all modules together
│   ├── io_collector.py     # Reads /proc/diskstats, /proc/[pid]/io
│   ├── threshold_analyzer.py  # mu + 3*sigma anomaly detection
│   ├── process_correlator.py  # Identifies which process caused a spike
│   ├── starvation_monitor.py  # Checks /proc/[pid]/schedstat
│   ├── config.py           # All tuneable parameters in one place
│   └── utils.py            # Logging and file helpers
├── tests/
│   ├── ransomware_like.py     # Test 1: small random R/W, IOPS saturation
│   ├── write_amplification.py # Test 2: excessive fsync()
│   └── silent_dropping.sh     # Test 3: dm-delay (needs bare metal)
├── report/
│   ├── main.tex            # Full LaTeX report
│   └── diagrams/           # Figures used in the report
└── logs/                   # Output (generated on each run)
```

## How the detector works

Every second, the main loop does this:

1. Reads `/proc/diskstats` and computes IOPS, bandwidth, and average latency
2. Feeds latency into a sliding window of 60 samples, computes mu and sigma
3. If latency > mu + 3*sigma, fires a LATENCY_ANOMALY alert
4. Reads `/proc/[pid]/io` for every process, computes per-process rates
5. If any process uses more than 70% of total bandwidth, flags it as IO_HOGGER
6. Every 5 cycles, reads `/proc/[pid]/schedstat` to check for starvation

When a latency spike and a hogger happen at the same time, it correlates them
and logs which process probably caused the problem.

## Configuration

All parameters live in `src/config.py`:

| Parameter | Default | What it controls |
|-----------|---------|-----------------|
| POLL_INTERVAL_SEC | 1.0 | How often we sample (seconds) |
| WINDOW_SIZE | 60 | Sliding window length for stats |
| SIGMA_MULTIPLIER | 3.0 | How many sigmas = anomaly |
| HOGGER_BANDWIDTH_THRESHOLD | 0.7 | 70% bandwidth = hogger |
| STARVATION_WAIT_THRESHOLD_MS | 500 | Wait time before flagging starvation |

## Test scenarios

Three scenarios, each testing a different failure mode:

**Ransomware-like** (ransomware_like.py): Creates hundreds of small files, reads
them in 4KB random chunks, XORs the data, writes it back, fsyncs. Generates
thousands of IOPS with low bandwidth. Should trigger hogger + latency alerts.

**Write amplification** (write_amplification.py): 8 threads doing tiny writes
with fsync after every single one. Forces the page cache to flush constantly,
starving any other process trying to do I/O.

**Silent dropping** (silent_dropping.sh): Uses dm-delay to make a virtual disk
respond with 200ms latency on every operation. Simulates a disk that's dying
but hasn't actually failed. Needs the dm-delay kernel module (works on bare
metal or VMs, not always in Docker).

## Notes

- The container runs as root because /proc/[pid]/io requires it
- The `--privileged` flag in docker-compose gives full access to /proc and /sys
- The silent_dropping test might not work inside Docker (needs dm-delay module)
- The other two tests work everywhere Docker runs
- First build takes a few minutes; after that Docker caches everything
