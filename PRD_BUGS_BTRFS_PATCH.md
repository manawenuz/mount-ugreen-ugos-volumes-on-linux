# Bug PRD: BTRFS Patching Critical Vulnerabilities

## 1. Overview
A series of critical defects have been identified in the BTRFS patching toolchain. These range from destructive offset errors to logic failures that would prevent successful restoration or mounting. This document serves as the authoritative list of bugs to be remediated before the tool is released.

## 2. Resolved Bugs

### BUG-001: Incorrect `OFF_INCOMPAT_FLAGS` (RESOLVED)
*   **Status:** FIXED. Offset updated to `0xBC`.
*   **Impact:** Destructive risk eliminated.

### BUG-002: Checksum Type Assumption (RESOLVED)
*   **Status:** FIXED. Tool now verifies `csum_type == 0` (CRC32C) before patching.

### BUG-003: Bytenr Validation Absence (RESOLVED)
*   **Status:** FIXED. Tool now verifies that the superblock's internal `bytenr` field matches its physical disk offset.

### BUG-004: Mirror Restore `bytenr` Mismatch (RESOLVED)
*   **Status:** FIXED. Backups are now per-mirror, and rollback instructions utilize the correct block for each physical slot.

### BUG-005: Backup Format Offset Prefix (RESOLVED)
*   **Status:** FIXED. Backups are now raw 4KiB blocks.

### BUG-006: Rollback Instruction Mismatch (RESOLVED)
*   **Status:** FIXED. Documentation now reflects raw binary restoration.

### BUG-007: Erroneous 1 PiB Mirror Offset (RESOLVED)
*   **Status:** FIXED. Non-standard mirror removed; list limited to 64KiB, 64MiB, and 256GiB.

### BUG-008: `dmsetup` Quoting Vulnerability (RESOLVED)
*   **Status:** FIXED. Shell script now uses hardened string passing for device paths.

### BUG-009: Misleading `OFF_BYTENR` Constant (RESOLVED)
*   **Status:** FIXED. Constants renamed to `OFF_BYTENR` (0x30) and `OFF_CSUM_DATA_START` (0x20).

### BUG-010: No verification of existing superblock CRC32C before patching (RESOLVED)
*   **Status:** FIXED. `find_valid_superblocks` now verifies the stored CRC32C against the body before accepting a mirror. CRC mismatch is treated as a hard abort. The same check surfaces in `--check` and `--dump` modes.
*   **Impact:** Prevents masking pre-existing silent corruption (bit rot, aborted writes) with a freshly valid CRC.

### BUG-011: `COW_DIR` user override silently discarded when `/tmp` is not tmpfs (RESOLVED)
*   **Status:** FIXED. `recover_btrfs.sh` now only applies the tmpfs heuristic when `COW_DIR` was not explicitly provided by the caller.
*   **Impact:** User-exported `COW_DIR=/some/path` is now always honored.

### BUG-012: Superblock backups written to current working directory (RESOLVED)
*   **Status:** FIXED. Added `--backup-dir DIR` argument. Before writing, the tool uses `findmnt` to verify the backup directory is not on the same underlying block device as the target. If the check detects a collision, it aborts with a clear remediation hint.
*   **Impact:** Eliminates the risk of writing the only rollback artefact onto the device being mutated.

### BUG-013: Pre-flight requires UGREEN flag on *every* mirror, preventing resume after partial patch (RESOLVED)
*   **Status:** FIXED. The patcher now classifies each mirror as `needs_patch`, `already_clean`, or `error`. Runs are permitted when all mirrors are either `needs_patch` or `already_clean`, with at least one `needs_patch`. Already-clean mirrors are skipped during writes. `--check` exits with code 2 for the mixed state so operators know a resume is possible.
*   **Impact:** Interrupted runs can now be resumed safely without manual intervention.

### BUG-014: Partial-write crash window across mirrors is undocumented (RESOLVED)
*   **Status:** FIXED. Added a "Crash Safety" subsection to `BTRFS_TESTING.md` describing the window, the kernel's mirror-selection behaviour, and the resume procedure introduced in BUG-013.
*   **Impact:** Operators now understand the risk and the remediation path.

### BUG-015: 1 GiB COW image may overflow during inspection (RESOLVED)
*   **Status:** FIXED. Default COW size bumped to 4 GiB. Mount step now uses `-o noatime,nodiratime`. `COW_SIZE` exposed as an environment override alongside `COW_DIR`.
*   **Impact:** Long inspection sessions no longer risk COW exhaustion.
