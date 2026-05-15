#!/bin/bash
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root"
  exit 1
fi

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 /dev/mapper/<your-ugreen_os-btrfs-volume>"
    exit 1
fi

TARGET_DEV="$1"
if [ ! -b "$TARGET_DEV" ]; then
    echo "Error: Block device $TARGET_DEV not found."
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PATCHER="$REPO_ROOT/scripts/patch_btrfs_ugos.py"

if [ ! -x "$PATCHER" ]; then
    echo "Error: patch_btrfs_ugos.py not found or not executable."
    echo "Expected at: $PATCHER"
    exit 1
fi

# ── Pre-flight read-only validation ──
echo "=== Pre-flight: validating BTRFS superblocks (read-only) ==="
if ! python3 "$PATCHER" --check "$TARGET_DEV"; then
    echo ""
    echo "Error: $TARGET_DEV failed validation."
    echo "Either this is not a UGREEN OS BTRFS volume, it has already been patched,"
    echo "or a critical safety check (bytenr / csum_type) failed."
    exit 1
fi

COW_DIR="${COW_DIR:-/var/tmp}"
if df --type=tmpfs /tmp >/dev/null 2>&1; then
    echo "Note: /tmp is tmpfs, using $COW_DIR for COW file"
else
    COW_DIR="/tmp"
fi
COW_IMG="$COW_DIR/ugreen_os_btrfs_cow_$$.img"
SNAP_NAME="ugos_btrfs_safe_test_$$"
MOUNT_POINT="/mnt/recovery_btrfs_test_$$"
LOOP_DEV=""

cleanup() {
    echo "=== Tearing down snapshot environment ==="
    set +e
    if mountpoint -q "$MOUNT_POINT"; then
        umount "$MOUNT_POINT"
    fi
    rmdir "$MOUNT_POINT" 2>/dev/null
    if dmsetup status "$SNAP_NAME" >/dev/null 2>&1; then
        dmsetup remove "$SNAP_NAME"
    fi
    if [ -n "$LOOP_DEV" ]; then
        losetup -d "$LOOP_DEV"
    fi
    rm -f "$COW_IMG"
    set -e
    echo "Teardown complete. Original disk ($TARGET_DEV) was untouched."
}

trap cleanup EXIT INT TERM HUP

echo ""
echo "=== [1/5] Setting up COW Snapshot ==="
echo "Target: $TARGET_DEV"
truncate -s 1G "$COW_IMG"
LOOP_DEV=$(losetup --find --show "$COW_IMG")
SIZE=$(blockdev --getsz "$TARGET_DEV")

# BUG-008 fix: quote all variables passed to dmsetup
# Create snapshot: 0 <size> snapshot <origin> <cow> <persistent(P)/non-persistent(N)> <chunksize>
# chunk size 8 = 8 sectors = 4 KiB (matches typical BTRFS node size)
printf '%s\n' "0 $SIZE snapshot $TARGET_DEV $LOOP_DEV N 8" | dmsetup create "$SNAP_NAME"
SNAP_DEV="/dev/mapper/$SNAP_NAME"
echo "Created snapshot device: $SNAP_DEV"

echo "=== [2/5] Patching BTRFS superblocks (Safe Mode — COW snapshot only) ==="
python3 "$PATCHER" --yes "$SNAP_DEV"

echo "=== [3/5] Verifying filesystem integrity ==="
# Try a read-only mount first to validate the kernel accepts the patched superblock
mkdir -p "$MOUNT_POINT"
if ! mount -o ro "$SNAP_DEV" "$MOUNT_POINT" 2>/dev/null; then
    echo "ERROR: mount failed on the patched snapshot."
    echo "The kernel may still be rejecting the filesystem."
    echo "Do NOT proceed to permanent patching."
    exit 1
fi
umount "$MOUNT_POINT"

echo "=== [4/5] Mounting read-write for verification ==="
mount "$SNAP_DEV" "$MOUNT_POINT"

echo ""
echo "==========================================================="
echo " SUCCESS! The patched volume is mounted at:"
echo " $MOUNT_POINT"
echo "==========================================================="
echo "Open another terminal to inspect your files."
echo "Reads are coming from the real disk, writes went to RAM/tmp."
echo "No data has been modified on $TARGET_DEV."
echo ""
read -p "Press [Enter] when you are done inspecting to tear down the test environment..."

# Call cleanup explicitly and disable the trap so it doesn't run again on normal exit
trap - EXIT INT TERM HUP
cleanup

echo ""
echo "=== Validation Complete ==="
read -p "Did the test succeed? Are you ready to permanently patch $TARGET_DEV? (y/N) " confirm
if [[ "$confirm" =~ ^[Yy]$ ]]; then
    if findmnt -n "$TARGET_DEV" >/dev/null 2>&1; then
        echo "Error: $TARGET_DEV is currently mounted. Unmount before patching." >&2
        exit 1
    fi
    echo "Permanently patching $TARGET_DEV..."
    python3 "$PATCHER" --yes "$TARGET_DEV"

    echo "=== Verifying permanent patch ==="
    if python3 "$PATCHER" --check "$TARGET_DEV" >/dev/null 2>&1; then
        echo "WARNING: UGREEN flag still detected after patching!" >&2
        echo "Do NOT attempt to mount. Investigate before proceeding." >&2
        exit 1
    else
        echo "Done! UGREEN proprietary flag is cleared."
        echo "You can now natively mount $TARGET_DEV with any standard Linux kernel."
    fi
else
    echo "Aborted permanent patch."
fi
