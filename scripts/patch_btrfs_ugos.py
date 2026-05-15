#!/usr/bin/env python3
"""
patch_btrfs_ugos.py

Directly manipulates BTRFS superblocks on a target block device to clear
the proprietary UGREEN OS 'ugacl' incompatible feature flag (bit 62,
0x4000000000000000) and recalculate the CRC32C checksum.

Usage:
    # Read-only verification
    sudo ./patch_btrfs_ugos.py --check /dev/mapper/<your-btrfs-volume>

    # Dump superblocks for offline analysis (read-only)
    sudo ./patch_btrfs_ugos.py --dump /dev/mapper/<your-btrfs-volume>

    # Patch (DANGEROUS — only run on COW snapshots or after validation)
    sudo ./patch_btrfs_ugos.py --yes /dev/mapper/<your-btrfs-volume>

    # Patch with backups to a safe directory
    sudo ./patch_btrfs_ugos.py --yes --backup-dir /mnt/other-disk/backups /dev/mapper/...

See recover_btrfs.sh for the safe COW-snapshot recovery flow.
"""

import argparse
import os
import stat
import struct
import subprocess
import sys
import time
from pathlib import Path

# ── BTRFS superblock constants ───────────────────────────────────────────────
# Verified against linux/include/uapi/linux/btrfs_tree.h (kernel 6.x)
BTRFS_SUPER_MAGIC = b"_BHRfS_M"
BTRFS_SUPER_INFO_SIZE = 4096
BTRFS_CSUM_SIZE = 32

# Standard mirror locations per kernel source fs/btrfs/disk-io.c
BTRFS_SUPER_OFFSETS = [
    64 * 1024,                # 64 KiB
    64 * 1024 * 1024,         # 64 MiB
    256 * 1024 * 1024 * 1024, # 256 GiB
]

# ── Field offsets within struct btrfs_super_block ────────────────────────────
# struct btrfs_super_block is __packed__; these are exact byte offsets.
OFF_CSUM = 0x00          # 32 bytes (first N are actual checksum)
OFF_CSUM_DATA_START = 0x20  # CRC covers everything from here to end
OFF_FSID = 0x20          # 16 bytes
OFF_BYTENR = 0x30        # 8 bytes — MUST match physical mirror offset
OFF_FLAGS = 0x38         # 8 bytes
OFF_MAGIC = 0x40         # 8 bytes — '_BHRfS_M'
OFF_GENERATION = 0x48    # 8 bytes
OFF_INCOMPAT_FLAGS = 0xBC  # 8 bytes — THIS IS THE TARGET FIELD
OFF_CSUM_TYPE = 0xC4     # 2 bytes — must be 0 (CRC32C)

# UGREEN proprietary incompatible feature flag (bit 62)
UGREEN_PROPRIETARY_BIT = 0x4000000000000000


# ── CRC32C (Castagnoli) implementation ───────────────────────────────────────

def _build_crc32c_table():
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


# ── Core helpers ─────────────────────────────────────────────────────────────

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


def verify_superblock_crc(block: bytearray) -> tuple[bool, int, int]:
    """
    Verify the stored CRC32C against the block body.
    Returns (ok, stored_crc, computed_crc).
    """
    stored_crc = struct.unpack("<I", block[OFF_CSUM:OFF_CSUM + 4])[0]
    # Zero out checksum area, compute CRC over the remainder
    saved_csum = block[OFF_CSUM:OFF_CSUM + BTRFS_CSUM_SIZE]
    block[OFF_CSUM:OFF_CSUM + BTRFS_CSUM_SIZE] = b"\x00" * BTRFS_CSUM_SIZE
    computed_crc = crc32c(block[OFF_CSUM_DATA_START:BTRFS_SUPER_INFO_SIZE])
    block[OFF_CSUM:OFF_CSUM + BTRFS_CSUM_SIZE] = saved_csum
    return (stored_crc == computed_crc, stored_crc, computed_crc)


def verify_bytenr_matches(offset: int, block: bytearray) -> bool:
    """
    The bytenr field at 0x30 must equal the physical offset where we read
    the block. This confirms we are correctly aligned.
    """
    bytenr = struct.unpack("<Q", block[OFF_BYTENR:OFF_BYTENR + 8])[0]
    return bytenr == offset


def verify_csum_type_crc32c(block: bytearray) -> bool:
    """Return True if csum_type is 0 (CRC32C), which is what we support."""
    csum_type = struct.unpack("<H", block[OFF_CSUM_TYPE:OFF_CSUM_TYPE + 2])[0]
    return csum_type == 0


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
    # Zero out the entire checksum area first
    block[OFF_CSUM:OFF_CSUM + BTRFS_CSUM_SIZE] = b"\x00" * BTRFS_CSUM_SIZE
    new_crc = crc32c(block[OFF_CSUM_DATA_START:BTRFS_SUPER_INFO_SIZE])

    # Write the 4-byte CRC32C digest back to offset 0x00 (little-endian)
    block[OFF_CSUM:OFF_CSUM + 4] = struct.pack("<I", new_crc)


# ── Backup helpers ───────────────────────────────────────────────────────────

def _check_backup_dir_safe(backup_dir: str, device_path: str) -> bool:
    """
    Return True if backup_dir is NOT on the same underlying block device
    as device_path. Uses findmnt when available.
    """
    backup_dir = os.path.realpath(backup_dir)
    device_path = os.path.realpath(device_path)

    # If findmnt is available, ask what block device backs backup_dir
    try:
        result = subprocess.run(
            ["findmnt", "-n", "-o", "SOURCE", "--target", backup_dir],
            capture_output=True, text=True, check=True,
        )
        backing = result.stdout.strip().split("\n")[0]
        if backing:
            backing_real = os.path.realpath(backing) if os.path.exists(backing) else backing
            device_real = os.path.realpath(device_path)
            if backing_real == device_real:
                return False
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    return True


def write_backups(valid_blocks, device_path: str, backup_dir: str) -> list:
    """
    Write each valid superblock to its own raw backup file under backup_dir.
    Returns a list of (offset, filepath) tuples.
    """
    backup_dir = os.path.realpath(backup_dir)
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = int(time.time())
    safe_name = Path(device_path).name.replace("/", "_")
    backups = []

    for offset, block in valid_blocks:
        backup_name = (
            f"btrfs_sb_backup_{safe_name}_offset_{offset:08X}_{timestamp}.bin"
        )
        backup_path = Path(backup_dir) / backup_name
        with open(backup_path, "wb") as f:
            f.write(block)
        # Verify what we wrote
        written_size = backup_path.stat().st_size
        if written_size != BTRFS_SUPER_INFO_SIZE:
            raise RuntimeError(
                f"Backup verification failed: {backup_path} has size {written_size}, "
                f"expected {BTRFS_SUPER_INFO_SIZE}"
            )
        backups.append((offset, str(backup_path)))

    return backups


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Clear UGREEN OS proprietary BTRFS incompatible feature flag."
    )
    parser.add_argument("device", help="Target block device (e.g. /dev/sda1)")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive confirmation (DANGEROUS — only for COW snapshots)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Read-only check: verify BTRFS magic, CRC, bytenr, csum_type, and UGREEN flag.",
    )
    parser.add_argument(
        "--dump",
        action="store_true",
        help="Read-only dump: save all valid superblocks to timestamped .bin files.",
    )
    parser.add_argument(
        "--backup-dir",
        dest="backup_dir",
        default=".",
        help="Directory for superblock backups (default: current directory). "
             "Should be on a different physical disk than the target device.",
    )
    args = parser.parse_args()

    device = args.device
    if not os.path.exists(device):
        print(f"Error: device '{device}' does not exist.", file=sys.stderr)
        sys.exit(1)

    read_only_mode = args.check or args.dump

    # For read-only modes we only need read access
    if read_only_mode:
        if not os.access(device, os.R_OK):
            print(f"Error: cannot read '{device}'. Try sudo?", file=sys.stderr)
            sys.exit(1)
    else:
        if not os.access(device, os.R_OK | os.W_OK):
            print(f"Error: cannot read/write '{device}'. Try sudo?", file=sys.stderr)
            sys.exit(1)

    # ── Read valid superblocks ──
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
        print(f"  - 0x{offset:08X} ({offset} bytes, {offset / 1024:.1f} KiB)")

    # ── Validate each mirror and classify ──
    # Per-mirror classification: error | needs_patch | already_clean
    mirror_states = {}  # offset -> state
    has_error = False
    needs_patch_count = 0
    already_clean_count = 0

    for offset, block in valid_blocks:
        errors = []

        # 1. CRC validation (BUG-010)
        crc_ok, stored_crc, computed_crc = verify_superblock_crc(block)
        if not crc_ok:
            errors.append(
                f"CRC mismatch at mirror 0x{offset:08X} "
                f"(stored=0x{stored_crc:08X}, computed=0x{computed_crc:08X})"
            )

        # 2. Bytenr must match physical offset
        if not verify_bytenr_matches(offset, block):
            bytenr = struct.unpack("<Q", block[OFF_BYTENR:OFF_BYTENR + 8])[0]
            errors.append(
                f"bytenr mismatch at mirror 0x{offset:08X} "
                f"(expected=0x{offset:08X}, found=0x{bytenr:08X})"
            )

        # 3. csum_type must be CRC32C (0)
        if not verify_csum_type_crc32c(block):
            csum_type = struct.unpack("<H", block[OFF_CSUM_TYPE:OFF_CSUM_TYPE + 2])[0]
            errors.append(
                f"unsupported csum_type={csum_type} at mirror 0x{offset:08X} "
                f"(only CRC32C/0 is supported)"
            )

        # 4. UGREEN flag state
        ugreen_set = verify_ugreen_flag_set(block)

        if errors:
            has_error = True
            mirror_states[offset] = "error"
            for e in errors:
                print(f"  ERROR: {e}", file=sys.stderr)
        elif ugreen_set:
            mirror_states[offset] = "needs_patch"
            needs_patch_count += 1
        else:
            mirror_states[offset] = "already_clean"
            already_clean_count += 1
            print(
                f"  Info: mirror 0x{offset:08X} is already clean "
                "(UGREEN flag not set)."
            )

    # ── Handle error state ──
    if has_error:
        print(
            "\nValidation failed due to errors above. Aborting to avoid data corruption.",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Handle all-clean state ──
    if needs_patch_count == 0:
        if already_clean_count > 0:
            print("\nAll mirrors are already clean. Nothing to patch.")
            sys.exit(0)
        else:
            # Should not reach here (no valid blocks caught earlier)
            print("\nNo actionable mirrors found.", file=sys.stderr)
            sys.exit(1)

    # ── Read-only modes ──
    if args.dump:
        backups = write_backups(valid_blocks, device, args.backup_dir)
        print("\nDump complete. Superblock backups saved (read-only, originals untouched):")
        for offset, path in backups:
            print(f"  0x{offset:08X} -> {path}")
        sys.exit(0)

    if args.check:
        if already_clean_count > 0:
            print(
                f"\nCheck: mixed state detected ({needs_patch_count} need patching, "
                f"{already_clean_count} already clean). A resume is possible.",
                file=sys.stderr,
            )
            sys.exit(2)  # distinct exit code for mixed/resume state
        else:
            print("\nCheck passed: all mirrors need patching and are structurally valid.")
            print("  - CRC32C checksums verified")
            print("  - bytenr matches physical offset")
            print("  - csum_type = CRC32C (0)")
            print("  - UGREEN proprietary flag (0x4000000000000000) is present")
            sys.exit(0)

    # ── Patch mode: check backup-dir safety (BUG-012) ──
    if not _check_backup_dir_safe(args.backup_dir, device):
        print(
            f"\nError: backup directory '{args.backup_dir}' appears to be on the same "
            f"physical device as '{device}'.",
            file=sys.stderr,
        )
        print(
            "Writing backups to the device being mutated destroys the rollback path. "
            "Use --backup-dir pointing to a different disk.",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Backup before writing ──
    print("\nCreating backups before patching...")
    backups = write_backups(valid_blocks, device, args.backup_dir)
    for offset, path in backups:
        print(f"  0x{offset:08X} -> {path}")

    # ── Interactive confirmation ──
    if not args.yes:
        print(
            "\nWARNING: This will PERMANENTLY modify the BTRFS superblocks on "
            f"{device}."
        )
        print("Make sure you have validated this on a COW snapshot first.")
        if already_clean_count > 0:
            print(
                f"Note: {already_clean_count} mirror(s) are already clean and will be skipped."
            )
        confirm = input("Proceed? [y/N]: ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            sys.exit(0)

    # ── Patch and write back (BUG-013: skip already_clean) ──
    print("\nPatching superblocks...")
    with open(device, "r+b") as f:
        for offset, block in valid_blocks:
            if mirror_states[offset] == "already_clean":
                print(f"  Skipping already-clean mirror 0x{offset:08X}")
                continue

            patch_superblock(block)

            # Post-patch sanity checks
            magic = bytes(block[OFF_MAGIC:OFF_MAGIC + 8])
            if magic != BTRFS_SUPER_MAGIC:
                print(
                    f"  FATAL: magic corrupted at mirror 0x{offset:08X}! "
                    "Aborting remaining writes.",
                    file=sys.stderr,
                )
                sys.exit(1)

            if not verify_bytenr_matches(offset, block):
                print(
                    f"  FATAL: bytenr corrupted at mirror 0x{offset:08X}! "
                    "Aborting remaining writes.",
                    file=sys.stderr,
                )
                sys.exit(1)

            if not verify_csum_type_crc32c(block):
                print(
                    f"  FATAL: csum_type corrupted at mirror 0x{offset:08X}! "
                    "Aborting remaining writes.",
                    file=sys.stderr,
                )
                sys.exit(1)

            # Commit to disk
            f.seek(offset)
            f.write(block)
            f.flush()
            os.fsync(f.fileno())
            print(f"  Patched and verified mirror 0x{offset:08X}")

    print("\nDone! The UGREEN proprietary flag has been cleared.")
    print(
        "You should now be able to mount this volume with a standard Linux kernel."
    )
    print(f"\nRollback: restore from the backup files above if needed.")
    print("Example (for a single mirror):")
    for offset, path in backups:
        seek_4k = offset // 4096
        print(f"  dd if={path} of={device} bs=4K count=1 seek={seek_4k}")
        break  # Only show one example


if __name__ == "__main__":
    main()
