# PRD: e2fsprogs Patch for UGOS Data Recovery

## 1. Objective

Enable native mounting of UGREEN NASync (UGOS) ext4 volumes on standard
Linux distributions by teaching `e2fsprogs` about the undocumented
`0x20000000` ext4 incompat feature flag UGREEN added to their kernel,
and stripping it cleanly via `tune2fs`.

## 2. Background

UGOS ships a modified Linux 6.12.30+ kernel that sets an undocumented
ext4 incompat feature flag at bit `0x20000000` on every volume it
creates. Vanilla Linux kernels refuse to mount such volumes:

```
EXT4-fs (dm-X): Couldn't mount because of unsupported optional features (20000000)
```

Manual hex-edit of the superblock fails because ext4's `metadata_csum`
(CRC32c) covers the feature flags. Clearing the bit invalidates the
checksum, and the kernel rejects the mount with a checksum error
instead.

**Empirical observation** (from the QEMU recovery path):
when the volume mounts under the UGOS kernel, the proprietary `ugacl`
kernel module fails to load (`ugacl_vfs request_module failed`) yet the
filesystem mounts and reads/writes correctly. This is strong evidence
that bit `0x20000000` is purely a **provenance marker** — it does not
alter on-disk layout (inodes, extents, htree, etc.). It is a software
lock with no semantic on-disk effect.

## 3. Proposed Solution

Patch `e2fsprogs` to accept `0x20000000` as a known incompat feature so
`tune2fs` will operate on the volume and use its built-in ext4 routines
to:

- Clear the bit from `s_feature_incompat`
- Recalculate the CRC32c `s_checksum` on the superblock
- Propagate the change to **every** backup superblock

After `tune2fs` finishes, the volume is standard ext4 and mounts on any
vanilla kernel.

Two recovery paths emerge from one patch:

1. **Permanent fix** (`tune2fs -O ^ugreen_proprietary`): one-way
   conversion to vanilla ext4.
2. **Read-only access** (`fuse2fs -o ro`): userspace FUSE mount, never
   writes to the source disk.

## 4. Naming

Use `ugreen_proprietary` (or equivalently `ugreen_incompat29`) — **not**
`ugreen_acl`. The `ugacl` kernel module that UGOS ships is a separate
component; we have no evidence that bit `0x20000000` semantically
relates to ACLs. Honest naming prevents future confusion if the bit
turns out to mean something else.

## 5. Implementation

### 5.1 Clone e2fsprogs

```bash
git clone https://git.kernel.org/pub/scm/fs/ext2/e2fsprogs.git
cd e2fsprogs
```

### 5.2 Patch

**`lib/ext2fs/ext2fs.h`** — add the feature macro and include it in the
library's supported incompat mask:

```c
#define EXT4_FEATURE_INCOMPAT_UGREEN_PROPRIETARY 0x20000000

/* … existing EXT2_LIB_FEATURE_INCOMPAT_SUPP definition … */
#define EXT2_LIB_FEATURE_INCOMPAT_SUPP \
    ( /* existing flags */ | \
      EXT4_FEATURE_INCOMPAT_UGREEN_PROPRIETARY )
```

**`lib/e2p/feature.c`** — add the bit-to-string mapping so users can
reference it on the command line:

```c
{ EXT4_FEATURE_INCOMPAT_UGREEN_PROPRIETARY, "ugreen_proprietary" },
```

### 5.3 Build

```bash
./configure
make -j$(nproc)
```

No `make install` — we run the patched binaries directly out of the
build tree (`misc/tune2fs`, `misc/fuse2fs`) to avoid clobbering the
distro's `e2fsprogs`.

### 5.4 User commands

Permanent fix (one-way):
```bash
./misc/tune2fs -O ^ugreen_proprietary /dev/mapper/<volume>
mount /dev/mapper/<volume> /mnt/recovery   # any vanilla kernel
```

Read-only FUSE mount (no writes to source):
```bash
mkdir -p /mnt/recovery
./misc/fuse2fs -o ro /dev/mapper/<volume> /mnt/recovery
```

## 6. Risk Mitigation: Validate on a COW Snapshot First

We validate the patch against a snapshot of the real device. Reads come
from the real disk; writes are trapped in a temp file. Source is never
modified.

### Option A — dm-snapshot (recommended)

Pre-conditions: the UGOS RAID/LVM is assembled on the host, i.e.
`/dev/mapper/ug_*_pool*_volume1` exists as a block device (it exists at
the *block* layer even though no kernel can *mount* its filesystem).

```bash
# 1. Backing file for COW writes (1G is plenty for tune2fs metadata changes)
truncate -s 1G /tmp/cow_writes.img
losetup /dev/loop99 /tmp/cow_writes.img

# 2. Size of the real volume
SIZE=$(blockdev --getsz /dev/mapper/ug_<vg-name>_pool1-volume1)

# 3. Create the snapshot device
#    chunk size 8 = 8 sectors = 4 KiB (matches ext4 block size)
#    mode N = non-persistent (COW is destroyed when device is removed)
echo "0 $SIZE snapshot /dev/mapper/ug_<vg-name>_pool1-volume1 /dev/loop99 N 8" \
    | dmsetup create ugos_safe_test

# 4. Run patched tune2fs against the snapshot
./misc/tune2fs -O ^ugreen_proprietary /dev/mapper/ugos_safe_test

# 5. fsck the snapshot to confirm clean filesystem
./misc/e2fsck -fn /dev/mapper/ugos_safe_test

# 6. Mount the snapshot with the vanilla host kernel — proves the fix
mkdir -p /mnt/recovery_test
mount /dev/mapper/ugos_safe_test /mnt/recovery_test
ls /mnt/recovery_test
umount /mnt/recovery_test

# 7. Teardown — source disk is untouched
dmsetup remove ugos_safe_test
losetup -d /dev/loop99
rm /tmp/cow_writes.img
```

If step 6 mounts cleanly and shows your data, you can confidently run
the same `tune2fs -O ^ugreen_proprietary` on the **real** device.

### Option B — e2image metadata clone

A complement, not a replacement. Once the patched tools exist, this
produces a small sparse file containing only filesystem metadata
(superblocks, group descriptors, inode tables) for offline fsck
verification. The file is small enough to back up before any
destructive operation.

```bash
./misc/e2image -r /dev/mapper/ug_<vg-name>_pool1-volume1 /tmp/fs_metadata.raw
./misc/tune2fs -O ^ugreen_proprietary /tmp/fs_metadata.raw
./misc/e2fsck -f /tmp/fs_metadata.raw   # expect 0 errors
```

Note: `e2image` itself uses `libext2fs`, so this can only run *after*
the patch is built. Don't expect it as a pre-build sanity check.

## 7. Project Workflow (Recovery → Conversion)

The patch is the *second* tool we apply, not the first. Critical data
gets the proven path; the patch is then validated and used for the
remaining volumes.

| Phase | What | How |
|---|---|---|
| **A** | Recover pool1 (18TB RAID1) — high-value data | QEMU + UGOS kernel + rsync to freshly-formatted disk (see main README) |
| **B** | Build & validate patched e2fsprogs | This document, Option A snapshot test against either pool |
| **C** | Recover pool2 (2×1.8TB JBOD/linear) | Patched `tune2fs` directly on host — no VM needed |
| **D** | Rebuild as vanilla ext4 + standard RAID1 / linear MD | `wipefs` → `mdadm --create` → `mkfs.ext4` → restore data |

By Phase D every volume is standard Linux storage with no UGREEN
proprietary bits anywhere on disk. Future kernel upgrades will mount it
forever without any of this song and dance.

## 8. Success Metrics

- `tune2fs -O ^ugreen_proprietary` completes with exit code 0
- `e2fsck -fn` reports a clean filesystem on the patched device
- The patched device mounts on a vanilla host kernel (Ubuntu / Debian /
  mainline 7.x) without `unsupported optional features (20000000)`
- Source disk hash is identical before and after the snapshot test
  (proves Option A's safety)
- End-to-end pool2 recovery completes in under 5 minutes (vs. hours of
  setup for the QEMU path)

## 9. Future Work

- Automate the entire flow as `recover.sh`: clones e2fsprogs, applies
  the patch, builds, performs the dm-snapshot test, and prompts before
  touching the real disk.
- Submit the patch as a downstream/forked binary in the recovery repo.
  Upstream `e2fsprogs` will not accept an undocumented vendor feature,
  and rightly so.
- File a GPL source-code request with UGREEN for their kernel
  modifications. Their refusal to provide a bootable UGOS image,
  combined with kernel patches whose source they do not publish, is
  exactly the violation the GPL exists to prevent.
