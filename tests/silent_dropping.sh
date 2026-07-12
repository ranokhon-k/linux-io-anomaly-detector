#!/bin/bash
# silent_dropping.sh - Simulates a dying disk using dm-delay.
#
# The idea: use device-mapper to create a virtual block device that
# adds artificial latency to every I/O operation. The disk doesn't
# "fail" per se, it just gets really slow — like a real disk that's
# about to die. The anomaly detector should pick up the latency spike.
#
# Usage: sudo bash silent_dropping.sh [delay_ms] [duration_sec]
#
# Needs: root, dm-delay kernel module (not always available in Docker).
# This test is meant for bare-metal or VM environments.

set -e

DELAY_MS=${1:-200}          # Default: 200ms delay per I/O
DURATION_SEC=${2:-120}      # Default: run for 2 minutes
TEST_FILE="/tmp/dm_delay_test_backing"
LOOP_DEV=""
DM_NAME="io_delay_test"
DM_PATH="/dev/mapper/${DM_NAME}"

echo "============================================="
echo " Silent Dropping Test"
echo " Delay: ${DELAY_MS}ms per I/O operation"
echo " Duration: ${DURATION_SEC}s"
echo "============================================="

# Cleanup function
cleanup() {
    echo ""
    echo "[*] Cleaning up..."
    
    # Remove device-mapper target
    if dmsetup info ${DM_NAME} &>/dev/null; then
        dmsetup remove ${DM_NAME} 2>/dev/null || true
    fi
    
    # Detach loop device
    if [ -n "$LOOP_DEV" ] && losetup "$LOOP_DEV" &>/dev/null; then
        losetup -d "$LOOP_DEV" 2>/dev/null || true
    fi
    
    # Remove backing file
    rm -f "$TEST_FILE"
    
    echo "[*] Cleanup complete."
}

trap cleanup EXIT

# Check prerequisites
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root."
    exit 1
fi

if ! modprobe dm-delay 2>/dev/null; then
    echo "ERROR: dm-delay kernel module not available."
    echo "Try: modprobe dm-delay"
    exit 1
fi

# Step 1: Create a backing file (1GB sparse)
echo "[1/5] Creating backing file..."
dd if=/dev/zero of="$TEST_FILE" bs=1M count=0 seek=1024 2>/dev/null

# Step 2: Set up loop device
echo "[2/5] Setting up loop device..."
LOOP_DEV=$(losetup --find --show "$TEST_FILE")
echo "      Loop device: $LOOP_DEV"

# Get size in 512-byte sectors
SECTORS=$(blockdev --getsz "$LOOP_DEV")
echo "      Size: ${SECTORS} sectors"

# Step 3: Create dm-delay device
# Format: start_sector num_sectors delay target_device offset delay_ms
echo "[3/5] Creating dm-delay device (${DELAY_MS}ms delay)..."
echo "0 ${SECTORS} delay ${LOOP_DEV} 0 ${DELAY_MS}" | dmsetup create ${DM_NAME}
echo "      Device: ${DM_PATH}"

# Step 4: Generate I/O workload on the delayed device
echo "[4/5] Generating I/O workload for ${DURATION_SEC}s..."
echo "      (Watch anomaly_detector logs for LATENCY_ANOMALY alerts)"
echo ""

# Phase A: Light workload (establish baseline) — 20 seconds
echo "  Phase A: Light baseline workload (20s)..."
timeout 20 dd if=/dev/urandom of=${DM_PATH} bs=4k count=100 oflag=direct conv=notrunc 2>/dev/null &
LIGHT_PID=$!
wait $LIGHT_PID 2>/dev/null || true

# Phase B: Gradually increasing workload
REMAINING=$((DURATION_SEC - 20))
echo "  Phase B: Increasing workload (${REMAINING}s)..."

END_TIME=$((SECONDS + REMAINING))
BLOCK_SIZE=4096
OPS_PER_ROUND=50

while [ $SECONDS -lt $END_TIME ]; do
    # Random reads and writes
    dd if=${DM_PATH} of=/dev/null bs=${BLOCK_SIZE} count=${OPS_PER_ROUND} \
       iflag=direct skip=$((RANDOM % 1000)) 2>/dev/null &
    dd if=/dev/urandom of=${DM_PATH} bs=${BLOCK_SIZE} count=${OPS_PER_ROUND} \
       oflag=direct seek=$((RANDOM % 1000)) conv=notrunc 2>/dev/null &
    wait 2>/dev/null || true
    
    # Increase operations over time
    OPS_PER_ROUND=$((OPS_PER_ROUND + 10))
done

# Step 5: Done
echo ""
echo "[5/5] Test complete."
echo "============================================="
echo " Check logs/alerts.json for LATENCY_ANOMALY events"
echo "============================================="
