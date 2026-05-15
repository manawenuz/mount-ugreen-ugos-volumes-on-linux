# Audit PRD: BTRFS Patch Toolchain — Round 2 Findings

## 1. Overview

A second-round audit of the `feature/btrfs-support` branch confirmed that the on-disk math (field offsets, mirror offsets, CRC32C implementation, bit 62 target) is correct and that the `recover_btrfs.sh` dm-snapshot / dry-run discipline is the right shape. However, a small set of safety, ergonomic, and hygiene defects must be addressed before the toolchain is run on production NAS data. This document enumerates those defects and their required fixes.

Scope of files under audit:
- `scripts/patch_btrfs_ugos.py`
- `scripts/recover_btrfs.sh`
- `BTRFS_TESTING.md`
- `PRD_BUGS_BTRFS_PATCH.md`
- `PRD_UGREEN_OS_BTRFS_PATCH.md`

## 2. Severity Definitions

- **Critical** — Can cause data loss or silent corruption if triggered.
- **High** — Can leave the filesystem unmountable or mask pre-existing damage.
- **Medium** — Defeats a documented safety guarantee or user override.
- **Low** — Ergonomics, hygiene, or documentation gap with no direct data risk.

## 3. Open Bugs

### BUG-010: No verification of existing superblock CRC32C before patching
- **Severity:** High
- **File:** `scripts/patch_btrfs_ugos.py`
- **Symptom:** `find_valid_superblocks` accepts any 4 KiB block whose magic equals `_BHRfS_M`. It does not verify that the *currently stored* CRC32C matches the body. If a superblock is silently corrupt (bit rot, prior aborted write, controller glitch), the patcher will clear the UGREEN bit and write a freshly valid CRC over the corrupt body, masking the damage and making later recovery materially harder.
- **Required fix:**
  1. After reading each candidate superblock, compute `crc32c(block[0x20:0x1000])` with the stored 4-byte CRC field temporarily zeroed (mirroring the write path).
  2. Compare against the stored CRC at `block[0x00:0x04]`.
  3. On mismatch, print a per-mirror error including expected and observed CRCs and set `all_ok = False`. Treat this as a hard abort condition — do not patch any mirror if any mirror fails CRC.
  4. Surface the same check in `--check` and `--dump` modes.
- **Acceptance:** Running `--check` on a deliberately bit-flipped superblock (test fixture acceptable) must exit non-zero and name the failing mirror. Running `--check` on a clean UGREEN OS volume must still exit zero.

### BUG-011: `COW_DIR` user override silently discarded when `/tmp` is not tmpfs
- **Severity:** Medium
- **File:** `scripts/recover_btrfs.sh:39-44`
- **Symptom:**
  ```bash
  COW_DIR="${COW_DIR:-/var/tmp}"
  if df --type=tmpfs /tmp >/dev/null 2>&1; then
      echo "Note: /tmp is tmpfs, using $COW_DIR for COW file"
  else
      COW_DIR="/tmp"
  fi
  ```
  If the user exports `COW_DIR=/mnt/scratch` and `/tmp` is a regular directory, the else-branch unconditionally clobbers the override to `/tmp`.
- **Required fix:** Only consult the tmpfs heuristic when `COW_DIR` was not provided by the caller. Suggested pattern:
  ```bash
  if [ -z "${COW_DIR:-}" ]; then
      if df --type=tmpfs /tmp >/dev/null 2>&1; then
          COW_DIR="/var/tmp"
      else
          COW_DIR="/tmp"
      fi
  fi
  ```
  Print the resolved `COW_DIR` once, regardless of source.
- **Acceptance:** `COW_DIR=/some/path ./recover_btrfs.sh /dev/...` always places the COW image under `/some/path`, irrespective of `/tmp`'s filesystem type.

### BUG-012: Superblock backups written to current working directory
- **Severity:** Medium
- **File:** `scripts/patch_btrfs_ugos.py` — `write_backups`
- **Symptom:** `Path(backup_name).resolve()` writes backups into CWD. A user running `patch_btrfs_ugos.py --yes /dev/sdX` while CWD lives on `/dev/sdX` (or a logical volume backed by it) is writing the only rollback artefact onto the device being mutated. The `recover_btrfs.sh` flow does not hit this, but direct invocation does — and the rollback example printed at the end of a successful run actively encourages direct invocation later.
- **Required fix:**
  1. Add a `--backup-dir DIR` argument; default to `$PWD` for backward compatibility but document the risk.
  2. Before writing, resolve `DIR` and the target device to their underlying block device (`os.stat().st_dev` on `DIR` vs. `stat` of the device file's contained filesystem) and refuse to proceed if they coincide. Print a clear remediation hint (`--backup-dir /some/other/disk`).
  3. Mention `--backup-dir` in the rollback example printed at the end of `main()`.
- **Acceptance:** Attempting to back up onto the same physical device being patched must abort with a clear error. Supplying `--backup-dir` to a path on a different device must succeed and place backups there.

### BUG-013: Pre-flight requires UGREEN flag on *every* mirror, preventing resume after partial patch
- **Severity:** Low
- **File:** `scripts/patch_btrfs_ugos.py` — main loop around `verify_ugreen_flag_set`
- **Symptom:** If a previous run patched mirror 0 (64 KiB) and then crashed before mirror 1 (64 MiB), re-running `--check` or the patcher reports `all_ok = False` because the already-patched mirror lacks the flag. The user is left unable to finish the job without manual intervention.
- **Required fix:**
  1. Track per-mirror state: `needs_patch` (flag set, CRC valid) vs. `already_clean` (flag clear, CRC valid) vs. `error` (anything else).
  2. Allow runs in which every mirror is either `needs_patch` or `already_clean`, with at least one `needs_patch`. Print a summary table before proceeding.
  3. Skip writes on `already_clean` mirrors; only patch and re-CRC the `needs_patch` ones.
  4. In `--check` mode, exit zero only when all mirrors are `already_clean`; exit non-zero with a distinct message for the mixed state so the operator knows a resume is needed.
- **Acceptance:** Simulating a partial patch (manually clearing the flag and rewriting CRC on one mirror) and re-running the patcher must complete the job on the remaining mirrors without errors.

### BUG-014: Partial-write crash window across mirrors is undocumented
- **Severity:** Low (documentation only)
- **File:** `BTRFS_TESTING.md`
- **Symptom:** A SIGKILL / power loss between the `fsync` of mirror 0 and the write of mirror 1 leaves mirrors with mismatched `incompat_flags` at identical generation numbers. Kernel selection by newest generation usually makes this benign, but operators deserve to know it can happen and that BUG-013's resume path is the remediation.
- **Required fix:** Add a short "Crash safety" subsection to `BTRFS_TESTING.md` describing the window, the kernel's mirror-selection behaviour, and the resume procedure introduced in BUG-013.
- **Acceptance:** Reviewer can locate a paragraph in `BTRFS_TESTING.md` answering "what happens if the patcher is killed mid-run?".

### BUG-015: 1 GiB COW image may overflow during inspection
- **Severity:** Low
- **File:** `scripts/recover_btrfs.sh:73`
- **Symptom:** `truncate -s 1G "$COW_IMG"`. The verification step mounts the snapshot read-write for the operator to inspect. On a large array, ordinary mount-time writes (log replay, free-space cache, atime updates if `noatime` is absent) can approach or exceed 1 GiB during a long inspection session. A full COW invalidates the snapshot — confusing, though not destructive to the real disk.
- **Required fix:**
  1. Bump default COW size to 4 GiB.
  2. Mount the rw inspection step with `-o noatime,nodiratime` to minimise incidental writes.
  3. Expose `COW_SIZE` (default `4G`) as an env override alongside `COW_DIR`.
- **Acceptance:** A 30-minute inspection session with directory traversal completes without `dmsetup status` reporting the snapshot as `Invalid`.

## 4. Repo Hygiene

### HYG-001: Uncommitted PRD edits on the branch
- **Files:** `PRD_BUGS_BTRFS_PATCH.md`, `PRD_UGREEN_OS_BTRFS_PATCH.md`
- **Required fix:** Stage and commit (or revert) the working-tree modifications before this branch is merged. Whichever direction is taken, the branch tip must equal `git stash`-clean.

### HYG-002: Executable bit on `scripts/patch_btrfs_ugos.py`
- **File:** `scripts/patch_btrfs_ugos.py`
- **Required fix:** Ensure `git ls-files --stage scripts/patch_btrfs_ugos.py` shows mode `100755`. `recover_btrfs.sh:23` already tests `-x`; without the bit, the shepherded flow aborts before reaching the patcher.

## 5. Out of Scope

Confirmed correct during this audit and explicitly NOT to be touched:
- Field offsets in `patch_btrfs_ugos.py:42-52`.
- Mirror offsets `[64 KiB, 64 MiB, 256 GiB]`.
- CRC32C polynomial, seed, finalization, and coverage range `[0x20, 0x1000)`.
- The `0x4000000000000000` (bit 62) UGREEN target — outside the standard `BTRFS_FEATURE_INCOMPAT_*` allocation range.
- The dm-snapshot + loopback COW + read-only-then-read-write mount sequencing in `recover_btrfs.sh`.
- The `findmnt` guard before the permanent-patch step.

## 6. Definition of Done

- All bugs in §3 closed with code or documentation changes as specified.
- Both hygiene items in §4 resolved.
- A new "Resolved Bugs" block appended to `PRD_BUGS_BTRFS_PATCH.md` mirroring the existing BUG-001..BUG-009 entries, marking BUG-010..BUG-015 as RESOLVED with one-line impact summaries.
- `python3 scripts/patch_btrfs_ugos.py --check` exercised against (a) a clean UGREEN superblock fixture, (b) a corrupt-CRC fixture, and (c) a partially-patched fixture, with expected exit codes documented in `BTRFS_TESTING.md`.
- Branch passes `git status` clean and `scripts/patch_btrfs_ugos.py` is mode 100755 in the index.
