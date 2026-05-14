# Escaping UGOS: Mounting UGREEN NAS Volumes on Standard Linux

A practical guide to recovering data from UGREEN NASync devices (DXP4800 Plus and
friends) when you've replaced UGOS with a standard Linux distro — and discovered
your existing UGOS-formatted ext4 volumes refuse to mount.

## The Problem (TL;DR)

UGREEN's UGOS is Debian 12 + a custom Linux 6.12.30+ kernel that adds an
**undocumented ext4 incompat feature flag** (`0x20000000`, reported by upstream
e2fsprogs as `FEATURE_I29`). Any vanilla Linux kernel — Ubuntu 24.04, 26.04,
even mainline 7.x — refuses to mount volumes formatted by UGOS:

```
EXT4-fs (dm-X): Couldn't mount because of unsupported optional features (20000000)
```

This is a **GPL violation**: UGREEN ships modifications to the Linux kernel
(GPLv2) but does not publish the corresponding source modifications.

## Why Patching the Superblock Doesn't Work

The obvious approach — clear the unknown feature bit with a hex editor — fails
because ext4's `metadata_csum` is enabled. After patching the feature bit:

```
ext2fs_open2: Superblock checksum does not match superblock
e2fsck: Superblock invalid, trying backup blocks...
/dev/... has unsupported feature(s): FEATURE_I29
e2fsck: Get a newer version of e2fsck!
```

You'd need to:
1. Clear bit `0x20000000` in `s_feature_incompat`
2. Recalculate the CRC32c superblock checksum
3. Also patch every **backup superblock** scattered across the disk

Even if you do all of that, you have no idea whether the feature affects
on-disk layout or just metadata — risking silent corruption.

## The Solution: Boot the UGOS Kernel in QEMU

The UGOS firmware update package contains everything we need:
- `vmlinuz` — the kernel that knows about `FEATURE_I29`
- `initrd.img` — initramfs
- `kernel.squashfs` — kernel modules (incl. ext4)
- `fw.squashfs` — UGOS userspace (not useful here)
- `apt.squashfs` — UGOS apt cache (not useful here)
- `ugreen.bz2` — application packages (.upk files in a tar)

The firmware `.img` is a **POSIX tar archive**, not a bootable disk image.
Don't `dd` it to your NVMe — you'll destroy your partition table.

We boot the UGOS kernel in QEMU paired with a standard Debian 12 cloud image
as the userspace (where `mdadm`, `lvm2`, etc. live).

## Prerequisites

- Your UGREEN NAS booted into a regular Linux distro (Ubuntu, Debian, etc.)
- Root access
- The UGOS firmware image — download from
  [nas.ugreen.com/pages/downloads](https://nas.ugreen.com/pages/downloads).
  Filename looks like `release_YYYYMMDD-firmware_image-X.Y.Z.W-intel_release_amd64_6.12.30+.img`
- KVM enabled CPU + `qemu-system-x86`, `wget`, `qemu-utils`

```bash
apt install -y qemu-system-x86 qemu-utils wget
```

## Step 1 — Extract the UGOS Firmware

```bash
mkdir -p /root/ugos-extract
cd /root/ugos-extract
tar -xf /path/to/release_*-firmware_image-*.img
ls
# vmlinuz initrd.img kernel.squashfs fw.squashfs apt.squashfs ugreen.bz2 ...
```

## Step 2 — Get a Debian 12 Userspace

```bash
cd /root
wget https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2
qemu-img convert -O raw debian-12-genericcloud-amd64.qcow2 debian12.img
qemu-img resize -f raw debian12.img 8G
```

## Step 3 — Free the RAID Disks From the Host

The host's `md`/`lvm` will probably have already grabbed the UGOS RAID
disks (it sees the MD superblock even if it can't mount the filesystem).
Stop them so QEMU can use the raw disks:

```bash
vgchange -an ug_<your-vg-name> 2>/dev/null || true
mdadm --stop /dev/md126 2>/dev/null || true
mdadm --stop /dev/md127 2>/dev/null || true
```

Adjust device names to match your system.

## Step 4 — Boot the Recovery VM

```bash
qemu-system-x86_64 \
  -enable-kvm -m 4G -smp 4 \
  -kernel /root/ugos-extract/vmlinuz \
  -initrd /root/ugos-extract/initrd.img \
  -append "break=top console=ttyS0,115200" \
  -drive file=/root/debian12.img,format=raw,if=virtio \
  -drive file=/dev/sda,format=raw,if=virtio,cache=none \
  -drive file=/dev/sdb,format=raw,if=virtio,cache=none \
  -drive file=/root/ugos-extract/kernel.squashfs,format=raw,if=virtio,readonly=on \
  -netdev user,id=n1 -device virtio-net-pci,netdev=n1 \
  -nographic
```

The UGOS initrd drops into a BusyBox shell because of `break=top`.

**Device map inside the VM:**
| guest | host | role |
|---|---|---|
| `vda` | `debian12.img` | Debian 12 rescue root |
| `vdb` | `/dev/sda` | RAID1 disk #1 |
| `vdc` | `/dev/sdb` | RAID1 disk #2 |
| `vdd` | `kernel.squashfs` | UGOS kernel modules |

Exit with `Ctrl-A X`.

## Step 5 — Pivot Into Debian 12 With UGOS Modules

In the `(initramfs)` shell:

```sh
modprobe virtio_blk
modprobe virtio_net
modprobe ext4
modprobe squashfs

mkdir /debian /modules
mount /dev/vda1 /debian
mount /dev/vdd /modules

mkdir -p /debian/lib/modules
cp -a /modules/usr/lib/modules/6.12.30+ /debian/lib/modules/

mount --bind /dev  /debian/dev
mount --bind /proc /debian/proc
mount --bind /sys  /debian/sys
mount -t devpts devpts /debian/dev/pts

chroot /debian /bin/bash
```

## Step 6 — Assemble RAID and Mount

In the chroot:

```bash
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

# Re-link Debian's view of the running UGOS kernel's modules
depmod -a 6.12.30+
modprobe raid1
modprobe md_mod
modprobe ext4

# Bring up networking so we can apt install
IFACE=$(ls /sys/class/net | grep -v lo | head -1)
ip link set $IFACE up
dhclient $IFACE
rm -f /etc/resolv.conf
echo "nameserver 8.8.8.8" > /etc/resolv.conf

apt update
apt install -y mdadm lvm2

# Assemble the RAID and activate LVM
mdadm --assemble --scan
cat /proc/mdstat
vgchange -ay
ls /dev/mapper/

# Mount your pool (the `ugacl` warning in dmesg is harmless)
mkdir -p /mnt/pool
mount /dev/mapper/ug_<your-vg-name>-volume1 /mnt/pool
ls /mnt/pool
```

You'll see your UGOS folders (`@FileManager`, `@docker`, `@home`, `@thumbnail`,
plus your shares) — all your data is there.

## Step 7 — Extract Your Data

Easiest path: SSH/rsync to another machine, or push to a fresh disk also
attached to the VM:

```bash
apt install -y openssh-server rsync
rsync -avh --progress /mnt/pool/<share>/ user@otherhost:/backup/path/
```

## Step 8 (Optional) — Escape UGOS Permanently

Once your data is safely elsewhere, wipe and recreate as **standard** Linux
RAID1 + ext4 (no UGREEN proprietary garbage):

```bash
# On the host, with disks freed from any array:
wipefs -a /dev/sda /dev/sdb
mdadm --create /dev/md0 --level=1 --raid-devices=2 /dev/sda /dev/sdb
mkfs.ext4 /dev/md0
# add to /etc/fstab and /etc/mdadm/mdadm.conf as usual
```

Now your storage mounts natively on any Linux kernel forever.

## Notes and Gotchas

- **The firmware `.img` is a TAR, not a disk image.** Do not `dd` it to a
  block device — you'll wipe whatever's there. (Ask me how I know.)
- **PIKVM mount of the `.img` as a flash drive doesn't boot it either** —
  the file is a Clonezilla-style restore package handled by UGREEN's
  `ugupdate` binary, not a bootable USB image.
- **Versions reported in `os-release`:** `Debian GNU/Linux 12 (bookworm)`,
  `OS_VERSION=1.15.1.0127`, kernel `6.12.30+`.
- **The `ugacl_vfs request_module failed` warning** when you mount the
  filesystem is UGREEN's proprietary ACL kernel module, which we don't have.
  The filesystem still mounts and reads fine without it.
- **Don't trust Ubuntu 26.04's mdadm against the UGOS kernel** — glibc is
  too new and you'll get `__tunable_is_initialized undefined symbol`
  errors. The whole point of dropping into Debian 12 is matching glibc.

## Why GPL Matters Here

UGREEN modified the Linux kernel (GPLv2) to add their own ext4 incompat
feature flag. Section 3 of GPLv2 requires them to provide the corresponding
source code on request. They do not publish kernel source, and the feature
is undocumented — meaning users who buy their hardware cannot read their
own data after replacing the OS. This guide exists because the GPL was
ignored.

If you've bought UGREEN hardware, consider [requesting the kernel
source](https://www.gnu.org/licenses/gpl-faq.html#GPLRequireSourcePostedPublic)
under the GPL.
