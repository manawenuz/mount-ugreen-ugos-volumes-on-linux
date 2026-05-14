# Product Requirements Document (PRD): e2fsprogs Patch for UGOS Data Recovery

## 1. Objective
Enable seamless data recovery and native mounting of UGREEN NASync (UGOS) ext4 storage volumes on standard Linux distributions, permanently bypassing the undocumented `0x20000000` (`FEATURE_I29`) incompat feature flag added by UGREEN.

## 2. Background
UGREEN modified the GPLv2 Linux kernel to include a proprietary, undocumented ext4 incompat feature flag (`0x20000000`). This flag prevents vanilla Linux kernels (e.g., Ubuntu, Debian) from mounting the volume. Previous workaround attempts, such as manually zeroing the bit using a hex editor, failed because it invalidates the `metadata_csum` (CRC32c) superblock checksum, causing `e2fsck` and the kernel to reject the mount. The current workaround requires spinning up a QEMU VM with the extracted UGOS kernel, which is highly complex and resource-intensive. 

Since the proprietary `ugacl` kernel module fails to load but the volume still mounts and reads fine in the VM, we know the `0x20000000` flag does not fundamentally alter the on-disk data structures (inodes, extents, etc.). It acts solely as a software lock.

## 3. Proposed Solution
Modify the standard `e2fsprogs` (ext2/ext3/ext4 file system utilities) source code to recognize `0x20000000` as a supported incompat feature. 

This enables two recovery paths:
1. **Permanent Native Fix (`tune2fs`)**: Using the patched `tune2fs`, users can strip the incompat flag from the superblock (`tune2fs -O ^ugreen_acl`). Because `tune2fs` natively understands the ext4 structure, it will safely clear the bit, **automatically recalculate all CRC32c checksums**, and update all backup superblocks.
2. **Read-Only Access (`fuse2fs`)**: Users who prefer not to alter the disk can use the patched `fuse2fs` to mount the partition read-only in userspace. 

## 4. Implementation Steps

### 4.1 Clone `e2fsprogs`
Clone the latest stable release of `e2fsprogs` from the official repository:
```bash
git clone https://git.kernel.org/pub/scm/fs/ext2/e2fsprogs.git
cd e2fsprogs
```

### 4.2 Patch the Source Code
Modify the following source files to teach `e2fsprogs` about the `0x20000000` flag. Let's refer to it as `EXT4_FEATURE_INCOMPAT_UGREEN`.

**File 1: `lib/ext2fs/ext2fs.h`**
* Define the feature flag macro:
  ```c
  #define EXT4_FEATURE_INCOMPAT_UGREEN 0x20000000
  ```
* Add `EXT4_FEATURE_INCOMPAT_UGREEN` to the `EXT2_LIB_FEATURE_INCOMPAT_SUPP` macro.

**File 2: `lib/e2p/feature.c`**
* Add an entry for `EXT4_FEATURE_INCOMPAT_UGREEN` mapping to the string `"ugreen_acl"` in the feature table. This allows `tune2fs` to recognize the string argument.

### 4.3 Compile the Toolkit
Run the standard build process:
```bash
./configure
make -j$(nproc)
```

### 4.4 Provide User Instructions
Document the commands the user must execute to recover their volume.

**To permanently fix the disk:**
```bash
./misc/tune2fs -O ^ugreen_acl /dev/mapper/<your-volume>
```
*Note: This makes the volume standard ext4 forever.*

**To perform a zero-touch read-only mount:**
```bash
./misc/fuse2fs -o ro /dev/mapper/<your-volume> /mnt/recovery
```

## 5. Risk Mitigation & Safe Testing (Zero-Risk Validation)

Before executing the patched `tune2fs` on the live data, we will validate the process on a perfect logical clone without risking a single byte of the original disk.

### Option A: Device Mapper Copy-On-Write (COW) Overlay (Recommended)
This method creates a virtual block device where all **reads** come from your real NAS disks, but all **writes** (such as the `tune2fs` modifications) are safely trapped in a temporary file in `/tmp`.

1. Create a loopback file to hold our writes:
   ```bash
   truncate -s 1G /tmp/cow_writes.img
   losetup /dev/loop99 /tmp/cow_writes.img
   ```
2. Get the exact sector size of the real volume:
   ```bash
   SIZE=$(blockdev --getsz /dev/mapper/ug_<your-vg-name>-volume1)
   ```
3. Create the virtual snapshot device:
   ```bash
   echo "0 $SIZE snapshot /dev/mapper/ug_<your-vg-name>-volume1 /dev/loop99 P 8" | dmsetup create ugos_safe_test
   ```
4. Run the patched `tune2fs` on the virtual device:
   ```bash
   ./misc/tune2fs -O ^ugreen_acl /dev/mapper/ugos_safe_test
   ```
5. Mount it natively and extract data safely:
   ```bash
   mount /dev/mapper/ugos_safe_test /mnt/recovery
   ```
*(When done, simply `dmsetup remove ugos_safe_test` and delete the `/tmp` file. The real disk remains 100% untouched).*

### Option B: `e2image` Metadata Clone
If we only want to test that the checksum recalculation and `e2fsck` pass cleanly, we can clone just the filesystem metadata (superblocks and inodes) into a sparse raw image, skipping the terabytes of actual file data:
```bash
e2image -r /dev/mapper/ug_<your-vg-name>-volume1 /tmp/fs_metadata.raw
./misc/tune2fs -O ^ugreen_acl /tmp/fs_metadata.raw
e2fsck -f /tmp/fs_metadata.raw
```

## 6. Success Metrics
* **No Corruption:** `tune2fs` executes successfully without invalidating `metadata_csum`, and `e2fsck` reports the filesystem is clean.
* **Ease of Use:** The entire process takes less than 5 minutes for a competent Linux user, significantly faster than the current VM approach.

## 6. Future Work
* Create an automated bash script that downloads the `e2fsprogs` tarball, uses `sed` or patch files to apply the modifications automatically, runs `make`, and executes the `tune2fs` command.
* Submit a formal write-up to the broader UGREEN recovery community.
