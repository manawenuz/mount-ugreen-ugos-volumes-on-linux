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

## 3. Round 2 Audit Findings

### BUG-010: Missing Existing CRC Verification (RESOLVED)
*   **Status:** FIXED. Tool now verifies the existing superblock CRC before patching to avoid masking pre-existing bit rot or corruption.
*   **Impact:** Prevents masking pre-existing silent corruption with a freshly valid CRC.

### BUG-011: `COW_DIR` Logic Clashing (RESOLVED)
*   **Status:** FIXED. `recover_btrfs.sh` now respects user-provided `COW_DIR` and only applies tmpfs heuristics when the variable is unset.
*   **Impact:** User-exported `COW_DIR` is now always honored.

### BUG-012: Insecure Backup Location (RESOLVED)
*   **Status:** FIXED. Added `--backup-dir` argument and implemented a cross-device check to prevent writing backups to the same physical disk being mutated.
*   **Impact:** Eliminates the risk of writing the rollback artefact onto the device being mutated.

### BUG-013: Rigid Partial-Patch Abort (RESOLVED)
*   **Status:** FIXED. Tool now supports a "resume" state, allowing it to skip already-clean mirrors and patch only the remaining ones.
*   **Impact:** Interrupted runs can now be resumed safely.

### BUG-014: Crash Safety Documentation (RESOLVED)
*   **Status:** FIXED. `BTRFS_TESTING.md` now includes a comprehensive section on partial-patch behavior and recovery.
*   **Impact:** Operators now understand the risk and the remediation path.

### BUG-015: COW Snapshot Overrun (RESOLVED)
*   **Status:** FIXED. Default COW size bumped to 4GB, and verification mount now uses `noatime,nodiratime` to minimize write churn.
*   **Impact:** Long inspection sessions no longer risk COW exhaustion.
