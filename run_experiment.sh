#!/bin/bash
# run_experiment.sh - Runs the full experiment inside the container.
#
# This script:
#   1. Starts the anomaly detector in the background
#   2. Lets it collect a baseline for 15 seconds
#   3. Runs the ransomware-like test (IOPS saturation)
#   4. Waits a bit for things to settle
#   5. Runs the write amplification test (fsync storm)
#   6. Stops the detector and prints a summary
#
# All results end up in /app/logs/ which is mounted to the host.

set -e

echo "============================================="
echo " I/O Anomaly Detection - Full Experiment"
echo " Running inside Docker (Ubuntu 22.04)"
echo "============================================="
echo ""

# clean previous logs
rm -f /app/logs/anomaly_detector.log /app/logs/alerts.json

# show system info
echo "[*] System info:"
echo "    Kernel: $(uname -r)"
echo "    CPUs: $(nproc)"
echo "    Memory: $(free -h | awk '/Mem:/{print $2}')"
echo ""

# show disk devices available
echo "[*] Block devices:"
lsblk -d -o NAME,SIZE,TYPE 2>/dev/null || cat /proc/diskstats | awk '{print "    "$3}' | head -5
echo ""

# check what scheduler is active
echo "[*] I/O schedulers:"
for dev in /sys/block/*/queue/scheduler; do
    if [ -f "$dev" ]; then
        devname=$(echo $dev | cut -d'/' -f4)
        echo "    $devname: $(cat $dev)"
    fi
done
echo ""

# --- Phase 1: Start the anomaly detector ---
echo "[*] Starting anomaly detector in background..."
python3 /app/src/anomaly_detector.py --interval 1.0 &
DETECTOR_PID=$!
echo "    PID: $DETECTOR_PID"
echo ""

# let it build up a baseline
echo "[*] Collecting baseline (15 seconds)..."
sleep 15
echo "    Done. Baseline established."
echo ""

# --- Phase 2: Ransomware-like test ---
echo "============================================="
echo " Test 1: Ransomware-like I/O (IOPS saturation)"
echo "============================================="
echo ""
python3 /app/tests/ransomware_like.py --duration 30 --num-files 200 --file-size 32 --no-cleanup
echo ""

# brief cooldown
echo "[*] Cooldown (10 seconds)..."
sleep 10
echo ""

# --- Phase 3: Write amplification test ---
echo "============================================="
echo " Test 2: Write Amplification (fsync storm)"
echo "============================================="
echo ""
python3 /app/tests/write_amplification.py --duration 30 --intensity high
echo ""

# --- Phase 4: Stop detector and summarize ---
echo "[*] Stopping anomaly detector..."
kill $DETECTOR_PID 2>/dev/null || true
wait $DETECTOR_PID 2>/dev/null || true
sleep 2

echo ""
echo "============================================="
echo " Experiment Complete - Results Summary"
echo "============================================="
echo ""

# count alerts by type
if [ -f /app/logs/alerts.json ]; then
    echo "[*] Alerts generated:"
    python3 -c "
import json
with open('/app/logs/alerts.json') as f:
    alerts = json.load(f)
counts = {}
for a in alerts:
    t = a['type']
    counts[t] = counts.get(t, 0) + 1
for t, c in sorted(counts.items()):
    print(f'    {t}: {c}')
print(f'    TOTAL: {len(alerts)}')
"
else
    echo "    No alerts generated (this shouldn't happen)"
fi

echo ""
echo "[*] Log files saved to /app/logs/"
echo "    - anomaly_detector.log (full trace)"
echo "    - alerts.json (structured alerts)"
echo ""
echo "Done."
