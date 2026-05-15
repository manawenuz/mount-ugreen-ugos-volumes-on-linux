# Escaping UGOS: Native UGREEN NAS Data Recovery for Standard Linux

A practical guide to recovering data from UGREEN NASync devices (DXP4800 Plus, DXP6800 Pro, DXP8800, etc.) when you've replaced UGOS with a standard Linux distro (TrueNAS, Unraid, Debian, Ubuntu) — and discovered your existing UGOS-formatted ext4 volumes refuse to mount.

## The Problem

UGREEN's UGOS is Debian 12 + a custom Linux 6.12.30+ kernel that adds an **undocumented ext4 incompat feature flag** (`0x20000000`, reported by upstream `e2fsprogs` as `FEATURE_I29`). Any vanilla Linux kernel will refuse to mount volumes formatted by UGOS:

```
EXT4-fs (dm-X): Couldn't mount because of unsupported optional features (20000000)
```

This is essentially vendor lock-in. 

### Why Hex-Editing the Superblock Fails
If you try to clear the `0x20000000` bit manually with a hex editor, ext4's `metadata_csum` safety feature will catch it. The checksum becomes invalid, and `e2fsck` will fail:
```
ext2fs_open2: Superblock checksum does not match superblock
```

## The Solution: Native Ext4 Patching

Instead of the tedious process of booting a virtual machine (QEMU) with the proprietary kernel, we have created a **native, 100% safe Linux utility**. 

This repository patches the standard Linux `e2fsprogs` toolkit to recognize the `0x20000000` flag (which we've named `ugreen_proprietary`). We then use this patched `tune2fs` to strip the flag off your disks and correctly recalculate all ext4 CRC32c checksums, permanently converting your drive back into standard, mainline-compatible `ext4`.

### 100% Safe Validation (Zero-Risk)
We do not touch your real data immediately. Our `recover.sh` script utilizes Linux Device Mapper (dm-snapshot) to create a RAM-backed Copy-on-Write (COW) overlay. 
1. Reads come from your real disk.
2. Writes (the metadata patch) go to a temporary file in `/var/tmp`.
3. You get to mount the test volume and verify your files mathematically (`sha1sum`) before committing anything to the real disk!

## Usage

**Prerequisites:** 
- Your NAS booted into standard Linux (Debian, Ubuntu, etc.)
- Basic build tools (`apt install build-essential git autoconf libtool pkg-config libfuse-dev libblkid-dev uuid-dev`)

### Step 1: Build the Patched Utilities
```bash
git clone <this-repo> ugos-recovery
cd ugos-recovery
sudo ./scripts/build_patched_e2fsprogs.sh
```

### Step 2: Assemble Your Disks
Make sure your RAID/LVM disks are assembled (`mdadm --assemble --scan`, `vgchange -ay`). Locate your target volume (e.g., `/dev/mapper/ug_A5AEB6_1767706191_pool1-volume1`).

### Step 3: Run the Safe Recovery Wizard
```bash
sudo ./scripts/recover.sh /dev/mapper/<your-volume>
```

The interactive script will:
1. Verify the `ugreen_proprietary` flag exists.
2. Provision a temporary COW snapshot.
3. Patch the snapshot's superblock and run `e2fsck`.
4. Mount the test snapshot for you to inspect.
5. Safely clean up the snapshot.
6. Ask for final confirmation before permanently fixing the real disk.

## Notes & Gotchas
* **ugacl_vfs Warning:** Once mounted on standard Linux, you may see `ugacl_vfs request_module failed` in `dmesg`. This is just Linux ignoring UGREEN's proprietary Access Control List (ACL) tags and safely falling back to standard POSIX permissions. It does not affect data integrity.
* **Firmware Images:** UGREEN firmware `.img` files are actually POSIX tar archives, not bootable ISOs. Never `dd` them to your NVMe.
* **Post-Patch Verification:** You can use `./scripts/verify_hashes.sh` during the test phase to randomly hash 100 large files and compare them against your backups to prove mathematically that the ext4 layout is completely intact.
