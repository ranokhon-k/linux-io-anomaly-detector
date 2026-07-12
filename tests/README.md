# Test Scenarios

These are the workloads I used to stress-test the anomaly detector. Each one
simulates a different kind of I/O problem you might see in practice.

## What's here

**ransomware_like.py** — Makes tons of small random read/write ops with fsync
after each file. Saturates IOPS without maxing bandwidth. Should trigger the
hogger detection and latency anomaly alerts.

**write_amplification.py** — Spawns threads that do tiny writes and immediately
fsync. This forces the page cache to flush constantly, starving other processes.
Should trigger starvation and queue saturation alerts.

**silent_dropping.sh** — Uses device-mapper (dm-delay) to simulate a disk that
responds super slowly but doesn't actually fail. Needs privileged mode and the
dm-delay kernel module. Triggers latency anomaly alerts as the threshold
analyzer picks up the spike.

## How to run them

The easiest way is to just use docker-compose from the project root — it runs
the detector and both Python tests automatically:

```bash
docker-compose up --build
```

If you want to run them manually inside the container:

```bash
# terminal 1: start the detector
python3 /app/src/anomaly_detector.py

# terminal 2: pick a test
python3 /app/tests/ransomware_like.py --duration 30
python3 /app/tests/write_amplification.py --duration 30 --intensity high
bash /app/tests/silent_dropping.sh 200 60
```

## What alerts to expect

- **ransomware_like** → `IO_HOGGER` + `LATENCY_ANOMALY`
- **write_amplification** → `IO_STARVATION` + `QUEUE_SATURATION` + `LATENCY_ANOMALY`
- **silent_dropping** → `LATENCY_ANOMALY`
