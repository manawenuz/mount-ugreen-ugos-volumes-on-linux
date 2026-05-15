# PRD: Bugs and Issues — Final Audit

Audit date: 2026-05-15

## All Issues Resolved

Every bug identified across two audit rounds has been verified fixed.

### First Audit (9 issues)

| ID | Sev | Summary | Status |
|---|---|---|---|
| BUG-1 | P0 | `fix_pool1.sh` raw hex edit without checksum recalculation | Fixed — deleted |
| BUG-2 | P0 | `flash_and_boot_ugreen_os.sh` dd's tar to block device | Fixed — deleted |
| BUG-3 | P1 | Hardcoded device paths, IPs, credentials | Fixed — scripts deleted |
| BUG-4 | P2 | Patch fragile against upstream changes | Fixed — pinned to `v1.47.1` |
| BUG-5 | P2 | Build script working directory assumption | Fixed — `REPO_ROOT` from script path |
| BUG-6 | P2 | COW file in `/tmp` exhausts RAM on tmpfs | Fixed — defaults to `/var/tmp` |
| BUG-7 | P2 | Duplicate divergent READMEs | Fixed — top-level is symlink |
| BUG-8 | P3 | Stale `.crdownload` file | Fixed — deleted |
| BUG-9 | P3 | Missing binary verification in build script | Fixed — loop checks all 4 binaries |

### Second Audit (7 issues)

| ID | Sev | Summary | Status |
|---|---|---|---|
| BUG-10 | P0 | Tag `v1.47.0` didn't exist; `|| true` built unvalidated binaries | Fixed — uses `v1.47.1`, no `|| true` |
| BUG-11 | P1 | No post-verification after permanent patch | Fixed — e2fsck check added |
| BUG-12 | P1 | PRD Section 5.2 showed unsafe direct tune2fs | Fixed — STOP warning added |
| BUG-13 | P2 | No pre-check that target has the flag | Fixed — `tune2fs -l` check added |
| BUG-14 | P2 | No mount check before permanent patch | Fixed — `findmnt` guard added |
| BUG-15 | P3 | Trap missing SIGHUP — resource leak on SSH drop | Fixed — HUP added to trap |
| BUG-16 | P3 | Configure output suppressed | Fixed — logs to file with error tail |

### Remaining Notes (not bugs, informational)

1. **The patch line numbers are pinned to v1.47.1.** If e2fsprogs changes those
   files in future releases, `git apply --check` will catch it and the build
   will fail safely. No silent failure path exists.

2. **The QEMU recovery guide (README) is read-only** — it only mounts and rsyncs,
   never writes to UGREEN OS volumes. No data loss vectors there.

3. **The `e2fsck -fn || true` in recover.sh line 83** is intentionally tolerant —
   `-n` means read-only, and pre-existing filesystem errors (unrelated to the flag)
   should not block the mount test. This is correct behavior.
