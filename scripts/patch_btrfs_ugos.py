#!/usr/bin/env python3
"""
patch_btrfs_ugos.py

Directly manipulates BTRFS superblocks on a target block device to clear
the proprietary UGREEN OS 'ugacl' incompatible feature flag (bit 62,
0x4000000000000000) and recalculate the CRC32C checksum.

Usage:
    sudo ./patch_btrfs_ugos.py /dev/mapper/<your-btrfs-volume>

This tool makes permanent, irreversible changes. Validate on a COW
snapshot first (see recover_btrfs.sh).
"""

import argparse
import os
import struct
import sys
import time
from pathlib import Path

# BTRFS superblock constants
BTRFS_SUPER_MAGIC = b"_BHRfS_M"
BTRFS_SUPER_INFO_SIZE = 4096
BTRFS_SUPER_OFFSETS = [
    64 * 1024,          # 64 KiB
    64 * 1024 * 1024,   # 64 MiB
    256 * 1024 * 1024 * 1024,  # 256 GiB
    1024 * 1024 * 1024 * 1024 * 1024,  # 1 TiB
]

# Field offsets within the 4 KiB superblock
OFF_CSUM = 0x00       # 32 bytes, first 4 are CRC32C
OFF_BYTENR = 0x20     # 8 bytes
OFF_MAGIC = 0x40      # 8 bytes
OFF_INCOMPAT_FLAGS = 0x128  # 8 bytes

UGREEN_PROPRIETARY_BIT = 0x4000000000000000


def _build_crc32c_table():
    """Build CRC32C (Castagnoli) lookup table."""
    poly = 0x1EDC6F41
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ poly
            else:
                crc >>= 1
        table.append(crc & 0xFFFFFFFF)
    return table


_CRC32C_TABLE = _build_crc32c_table()


def crc32c(data: bytes, seed: int = 0xFFFFFFFF) -> int:
    """Compute CRC32C (Castagnoli) over `data`."""
    crc = seed
    for byte in data:
        crc = (crc >> 8) ^ _CRC32C_TABLE[(crc ^ byte) & 0xFF]
    return crc ^ 0xFFFFFFFF


def find_valid_superblocks(device_path: str):
    """
    Read superblocks from all known mirror offsets.
    Returns a list of (offset, bytearray) tuples for blocks with valid magic.
    """
    valid = []
    with open(device_path, "rb") as f:
        for offset in BTRFS_SUPER_OFFSETS:
            f.seek(offset)
            block = bytearray(f.read(BTRFS_SUPER_INFO_SIZE))
            if len(block) != BTRFS_SUPER_INFO_SIZE:
                continue  # Device smaller than this offset
            magic = bytes(block[OFF_MAGIC:OFF_MAGIC + 8])
            if magic == BTRFS_SUPER_MAGIC:
                valid.append((offset, block))
    return valid


def verify_ugreen_flag_set(block: bytearray) -> bool:
    """Return True if the UGREEN proprietary bit is present in incompat_flags."""
    flags = struct.unpack("<Q", block[OFF_INCOMPAT_FLAGS:OFF_INCOMPAT_FLAGS + 8])[0]
    return bool(flags & UGREEN_PROPRIETARY_BIT)


def patch_superblock(block: bytearray) -> None:
    """
    Clear the UGREEN proprietary bit and recalculate the CRC32C checksum.
    Modifies `block` in place.
    """
    # 1. Clear the proprietary bit
    flags = struct.unpack("<Q", block[OFF_INCOMPAT_FLAGS:OFF_INCOMPAT_FLAGS + 8])[0]
    flags &= ~UGREEN_PROPRIETARY_BIT
    block[OFF_INCOMPAT_FLAGS:OFF_INCOMPAT_FLAGS + 8] = struct.pack("<Q", flags)

    # 2. Recalculate CRC32C over bytes 0x20 .. 0xFFF
    # Zero out the checksum area first
    block[OFF_CSUM:OFF_CSUM + 32] = b"\x00" * 32
    new_crc = crc32c(block[OFF_BYTENR:BTRFS_SUPER_INFO_SIZE])

    # Write the 4-byte CRC32C digest back to offset 0x00 (little-endian)
    block[OFF_CSUM:OFF_CSUM + 4] = struct.pack("<I", new_crc)


def write_backup(valid_blocks, device_path: str) -> str:
    """Write all valid superblocks to a single backup file. Returns the path."""
    timestamp = int(time.time())
    safe_name = Path(device_path).name.replace("/", "_")
    backup_name = f"btrfs_sb_backup_{safe_name}_{timestamp}.bin"
    backup_path = Path(backup_name).resolve()

    with open(backup_path, "wb") as f:
        for offset, block in valid_blocks:
            f.write(struct.pack("<Q", offset))  # 8-byte offset prefix
            f.write(block)

    return str(backup_path)


def main():
    parser = argparse.ArgumentParser(
        description="Clear UGREEN OS proprietary BTRFS incompatible feature flag."
    )
    parser.add_argument("device", help="Target block device (e.g. /dev/sda1)")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive confirmation (DANGEROUS)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Read-only check: verify BTRFS magic and UGREEN flag presence, then exit.",
    )
    args = parser.parse_args()

    device = args.device
    if not os.path.exists(device):
        print(f"Error: device '{device}' does not exist.", file=sys.stderr)
        sys.exit(1)

    # For --check we only need read access
    if args.check:
        if not os.access(device, os.R_OK):
            print(f"Error: cannot read '{device}'. Try sudo?", file=sys.stderr)
            sys.exit(1)
    else:
        if not os.access(device, os.R_OK | os.W_OK):
            print(f"Error: cannot read/write '{device}'. Try sudo?", file=sys.stderr)
            sys.exit(1)

    # Read valid superblocks
    valid_blocks = find_valid_superblocks(device)
    if not valid_blocks:
        print(
            f"Error: no valid BTRFS superblocks found on '{device}'. "
            "Is this really a BTRFS filesystem?",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Found {len(valid_blocks)} valid BTRFS superblock mirror(s):")
    for offset, _ in valid_blocks:
        print(f"  - 0x{offset:08X} ({offset} bytes)")

    # Verify the proprietary bit is actually set
    missing_flag = False
    for offset, block in valid_blocks:
        if not verify_ugreen_flag_set(block):
            print(
                f"  Warning: UGREEN proprietary bit NOT set at offset 0x{offset:08X}. "
                "This mirror may already be patched or is not a UGREEN OS volume."
            )
            missing_flag = True

    if missing_flag and not all(
        verify_ugreen_flag_set(block) for _, block in valid_blocks
    ):
        print("\nError: not all superblocks have the UGREEN flag set.", file=sys.stderr)
        print("Aborting to avoid inconsistent state.", file=sys.stderr)
        sys.exit(1)

    if args.check:
        print("\nCheck passed: valid BTRFS with UGREEN proprietary flag present.")
        sys.exit(0)

    # Backup
    backup_path = write_backup(valid_blocks, device)
    print(f"\nBackup written to: {backup_path}")

    if not args.yes:
        print(
            "\nWARNING: This will PERMANENTLY modify the BTRFS superblocks on "
            f"{device}."
        )
        print("Make sure you have validated this on a COW snapshot first.")
        confirm = input("Proceed? [y/N]: ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            sys.exit(0)

    # Patch and write back
    print("\nPatching superblocks...")
    with open(device, "r+b") as f:
        for offset, block in valid_blocks:
            patch_superblock(block)

            # Verify the magic is still intact after patching
            magic = bytes(block[OFF_MAGIC:OFF_MAGIC + 8])
            if magic != BTRFS_SUPER_MAGIC:
                print(
                    f"  FATAL: magic corrupted at offset 0x{offset:08X}! "
                    "Aborting remaining writes.",
                    file=sys.stderr,
                )
                sys.exit(1)

            f.seek(offset)
            f.write(block)
            f.flush()
            os.fsync(f.fileno())
            print(f"  Patched and verified offset 0x{offset:08X}")

    print("\nDone! The UGREEN proprietary flag has been cleared.")
    print(
        "You should now be able to mount this volume with a standard Linux kernel."
    )
    print(f"\nRollback: restore from {backup_path} if needed.")


if __name__ == "__main__":
    main()
