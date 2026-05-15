#!/bin/bash
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root"
  exit 1
fi

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 /dev/mapper/<your-ugos-volume>"
    exit 1
fi

TARGET_DEV="$1"
if [ ! -b "$TARGET_DEV" ]; then
    echo "Error: Block device $TARGET_DEV not found."
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TUNE2FS="$REPO_ROOT/build/e2fsprogs/misc/tune2fs"
E2FSCK="$REPO_ROOT/build/e2fsprogs/e2fsck/e2fsck"

if [ ! -x "$TUNE2FS" ] || [ ! -x "$E2FSCK" ]; then
    echo "Error: Patched binaries not found."
    echo "Please run ./scripts/build_patched_e2fsprogs.sh first."
    exit 1
fi

COW_IMG="/tmp/ugos_cow_writes_$$.img"
SNAP_NAME="ugos_safe_test_$$"
MOUNT_POINT="/mnt/recovery_test_$$"
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

trap cleanup EXIT INT TERM

echo "=== [1/5] Setting up COW Snapshot ==="
echo "Target: $TARGET_DEV"
truncate -s 1G "$COW_IMG"
LOOP_DEV=$(losetup --find --show "$COW_IMG")
SIZE=$(blockdev --getsz "$TARGET_DEV")

# Create snapshot: 0 <size> snapshot <origin> <cow> <persistent(P)/non-persistent(N)> <chunksize>
echo "0 $SIZE snapshot $TARGET_DEV $LOOP_DEV N 8" | dmsetup create "$SNAP_NAME"
SNAP_DEV="/dev/mapper/$SNAP_NAME"
echo "Created snapshot device: $SNAP_DEV"

echo "=== [2/5] Stripping ugreen_proprietary flag (Safe Mode) ==="
"$TUNE2FS" -O ^ugreen_proprietary "$SNAP_DEV"

echo "=== [3/5] Verifying filesystem integrity ==="
# -f forces check, -n opens read-only/answers no
"$E2FSCK" -fn "$SNAP_DEV" || true

echo "=== [4/5] Mounting for verification ==="
mkdir -p "$MOUNT_POINT"
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
trap - EXIT INT TERM
cleanup

echo ""
echo "=== Validation Complete ==="
read -p "Did the test succeed? Are you ready to permanently patch $TARGET_DEV? (y/N) " confirm
if [[ "$confirm" =~ ^[Yy]$ ]]; then
    echo "Permanently patching $TARGET_DEV..."
    "$TUNE2FS" -O ^ugreen_proprietary "$TARGET_DEV"
    echo "Done! You can now natively mount $TARGET_DEV."
else
    echo "Aborted permanent patch."
fi
