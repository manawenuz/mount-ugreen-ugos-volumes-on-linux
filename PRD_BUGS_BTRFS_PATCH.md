# Bug PRD: BTRFS Patching Critical Vulnerabilities

## 1. Overview
A series of critical defects have been identified in the BTRFS patching toolchain. These range from destructive offset errors to logic failures that would prevent successful restoration or mounting. This document serves as the authoritative list of bugs to be remediated before the tool is released.

## 2. Critical Data Loss Risks

### BUG-001: Incorrect `OFF_INCOMPAT_FLAGS`
*   **Current Value:** `0x128`
*   **Correct Value:** `0xBC`
*   **Impact:** Destructive. The tool patches random data inside the device identity (`dev_item.fsid`) and volume label. The actual `ugacl` flag remains set, so the volume still won't mount, but the metadata is now corrupted.

### BUG-002: Checksum Type Assumption
*   **Detail:** The tool assumes `CRC32C`. It does not check the `csum_type` field at offset `0xC4`.
*   **Impact:** If a user has a volume using `XXHASH`, `SHA256`, or `BLAKE2B` (available in modern kernels), the tool will overwrite the checksum with a CRC32C, permanently corrupting the superblock.

### BUG-003: Bytenr Validation Absence
*   **Detail:** The tool does not verify the `bytenr` field at offset `0x30` against the physical offset where the block was found.
*   **Impact:** High. If the tool is misaligned or reading from an unexpected location, it will calculate a "valid" CRC for garbage data and write it back.

## 3. Restoration & Rollback Failures

### BUG-004: Mirror Restore `bytenr` Mismatch
*   **Detail:** The PRD's `dd` instructions suggest restoring the same first block from the backup into all mirror slots (64KiB, 64MiB, etc.).
*   **Impact:** Failure. Each mirror *must* have its unique physical offset stored in its own `bytenr` field (0x30). Restoring the 64KiB block into the 64MiB slot results in a kernel mount error: `superblock bytenr mismatch`.

### BUG-005: Backup Format Offset Prefix
*   **Detail:** `write_backup()` adds an 8-byte binary offset prefix to each 4KiB block.
*   **Impact:** Standard `dd` commands will write the 8-byte prefix into the device, immediately corrupting the superblock structure.

### BUG-006: Rollback Instruction Mismatch
*   **Detail:** The documentation's `dd` commands do not account for the block-prefixing in the backup file.

## 4. Logic & Safety Bugs

### BUG-007: Erroneous 1 PiB Mirror Offset
*   **Detail:** The code calculates a 4th mirror at `1024^5` (1 PiB). Standard BTRFS mirrors only go up to 256 GiB. Additionally, the calculation is mathematically inconsistent with the "1 TiB" comment.

### BUG-008: `dmsetup` Quoting Vulnerability
*   **Detail:** In `recover_btrfs.sh`, device paths are passed to `echo | dmsetup` without quotes.
*   **Impact:** The script will fail or behave dangerously if a volume name contains spaces (e.g., `/dev/mapper/UGO Vol 1`).

### BUG-009: Misleading `OFF_BYTENR` Constant
*   **Detail:** Offset `0x20` is the `fsid`, not `bytenr`. While it works as a range-start for the CRC, the naming causes confusion during maintenance.

## 5. Remediation Requirements
1.  **Code**: Fix all offsets (`0xBC`, `0x30`, `0x20`).
2.  **Backup**: Remove binary prefixes; write raw 4KiB blocks.
3.  **Safety**: Add `csum_type` and `bytenr` cross-verification before writing.
4.  **Shell**: Quote all device variables and fix mirror offsets.
5.  **Docs**: Update `dd` instructions to account for mirror-specific blocks.
