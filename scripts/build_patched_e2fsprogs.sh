#!/bin/bash
# Build patched e2fsprogs that recognizes UGOS's 0x20000000 incompat flag.
# Produces:
#   ./build/e2fsprogs/misc/tune2fs   — strip the flag (one-way fix)
#   ./build/e2fsprogs/misc/fuse2fs   — read-only userspace mount
#   ./build/e2fsprogs/misc/e2image   — clone metadata for offline tests
#   ./build/e2fsprogs/e2fsck/e2fsck  — fsck the patched filesystem
#
# Usage: ./build_patched_e2fsprogs.sh
# Run from repo root (the directory containing patches/ and scripts/).

set -euo pipefail

PATCH="$(cd "$(dirname "$0")/.." && pwd)/patches/0001-Recognize-ugreen_proprietary-incompat-feature.patch"
BUILD_DIR="$(pwd)/build"

if [ ! -f "$PATCH" ]; then
    echo "ERROR: patch not found at $PATCH" >&2
    exit 1
fi

echo "=== [1/5] Installing build dependencies ==="
if command -v apt >/dev/null; then
    apt update
    apt install -y build-essential git autoconf automake libtool pkg-config \
                   libfuse-dev libblkid-dev uuid-dev gettext texinfo
elif command -v dnf >/dev/null; then
    dnf install -y gcc make git autoconf automake libtool pkgconfig \
                   fuse-devel libblkid-devel libuuid-devel gettext texinfo
else
    echo "WARN: unrecognized package manager — install build tools manually" >&2
fi

echo "=== [2/5] Cloning e2fsprogs ==="
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"
if [ ! -d e2fsprogs ]; then
    git clone --depth 1 https://git.kernel.org/pub/scm/fs/ext2/e2fsprogs.git
fi
cd e2fsprogs
git reset --hard HEAD
git clean -fdx

echo "=== [3/5] Applying patch ==="
git apply --check "$PATCH"
git apply "$PATCH"
echo "Patched files:"
git diff --stat

echo "=== [4/5] Configure & build ==="
./configure --prefix=/usr/local/e2fsprogs-ugos >/dev/null
make -j"$(nproc)"

echo "=== [5/5] Sanity check ==="
if strings misc/tune2fs | grep -q '^ugreen_proprietary$'; then
    echo "  ✓ ugreen_proprietary string present in tune2fs"
else
    echo "  ✗ patch string missing in binary — build is broken" >&2
    exit 1
fi
echo ""
echo "Built binaries:"
ls -la misc/tune2fs misc/fuse2fs misc/e2image e2fsck/e2fsck 2>&1 \
   | grep -v 'No such file' || true
echo ""
echo "Run them directly from this build tree, e.g.:"
echo "  $(pwd)/misc/tune2fs -O ^ugreen_proprietary /dev/mapper/<volume>"
