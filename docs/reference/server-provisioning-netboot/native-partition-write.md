# Write a native Linux/Proxmox partition directly from Windows (no USB, no VHD)

The user's L0 ideal: a native Linux system on a physical partition of the
laptop's SSD, bootable from the machine's own ESP, Windows still present as a
second boot entry. Reachable without VHD/WSL/VirtualBox/ISO because Windows can
open an unused partition of the live system disk as a raw block device.

## 1. Create the partition from Windows, leave it RAW

- Disk Management or `diskpart`: shrink the Windows volume, create a new
  partition sized for the system, do NOT format it (no filesystem).
- Note the partition number (e.g. `p7`) and its device path.

## 2. Open the partition as a raw device (no drivers)

`\\.\GLOBALROOT\Device\Harddisk0\Partition7` opens read/write with no
third-party driver. From PowerShell, open with `[System.IO.File]::Open(path,
'Open','ReadWrite','None')` and seek/read/write. Verify before trusting:
write the ext4 superblock magic `53 EF` at offset 0x438 and read it back.

**Pitfall: the partition path is only reliable on the FIRST boot.** After a
reboot Windows may refuse it with `UnauthorizedAccessException` even after you
remove the drive letter (`Remove-PartitionAccessPath`) and confirm the disk is
not read-only — Windows holds the volume open. Do not fight it; fall back to
writing the whole disk by byte offset: open `\\.\PhysicalDrive0`
read/write, set `Position` to the partition's start offset (from
`Get-Partition -DiskNumber 0 -PartitionNumber N | select Offset`), and write
the image there. Same bytes, same result, no volume handle to block you. Verify
the ext4 magic at `offset + 0x438` after writing.

## 3. Build the rootfs in a container, ship only the finished image

- `debootstrap` the distro inside a container on the build host (Docker), then
  install the target packages (for Proxmox: the `pve` repo + `proxmox-ve`,
  plus `wpasupplicant` and `firmware-iwlwifi` — the base ships neither).
- **Pitfall: do not bind-mount the Windows filesystem into the build
  container.** `debootstrap` fails with `tar failed` on a Windows bind-mount
  (path/encoding mismatch). Build entirely inside the container and export
  only the finished image to the Windows host.
- Build the ext4 image with `mke2fs -d <rootfs> <image>`.
- **Pitfall: unmount `/proc`, `/sys`, `/dev`, `/run` inside the rootfs BEFORE
  `mke2fs -d`.** If they are still mounted, `mke2fs -d` walks them and reads
  the whole virtual filesystem — a 9.6 TB read loop that never finishes. Save
  the rootfs first (tar it), kill the loop, unmount, rebuild.
- Transfer the finished image to the Windows host (background scp), verify
  `sha256` end-to-end, then write it into the raw partition.

## 4. Boot via GRUB on the machine's own ESP

- Stage a signed shim/grub at `\EFI\BOOT\BOOTX64.EFI` and a `grub.cfg` on the
  ESP; add a `bcdedit` entry pointing at it, first in firmware order.
- **Pitfall: GRUB does not auto-read `\EFI\BOOT\grub.cfg`.** It looks for its
  config at the compiled `prefix`, typically `(hd0,gpt1)/boot/grub`. If you
  only drop the config at `\EFI\BOOT\`, you land in a bare `grub>` console
  every boot and must hand-type `search --file --set=root /EFI/BOOT/grub.cfg`
  + `configfile`. Put the same `grub.cfg` in BOTH `\EFI\BOOT\grub.cfg` AND
  `\boot\grub\grub.cfg` on the ESP so auto-boot works. Keep menuentry names
  ASCII (no em-dash/UTF-8) to avoid parser edge cases.
- **Boot by LABEL, not UUID** — more robust when the image is rewritten:
  `search --no-floppy --label <label> --set=root` then
  `linux /boot/vmlinuz-... root=LABEL=<label> ro`. Set the label with
  `e2label`/`tune2fs` and confirm it after writing the image.
- Add a `Windows 11` entry (`chainloader /EFI/Microsoft/Boot/bootmgfw.efi`)
  so the user can still get back to Windows.

## 5. Secure Boot is the likely first-boot blocker

An unsigned Proxmox kernel will not boot under Secure Boot: the signed shim
loads GRUB, but GRUB refuses the unsigned kernel (`bad shim signature` /
`Secure Boot violation`). If the box does not appear on the network after
reboot, check Secure Boot first. Fix: disable Secure Boot in firmware
(`Security -> Secure Boot -> Disabled`), or enroll a MOK for the kernel.

**Symptom signature when SB is on but the kernel still loads:** the kernel
boots, then systemd services that need unsigned modules fail in a cluster —
`systemd-rfkill`, `systemd-backlight`, `systemd-logind`, `pvefw-logger` all
`[FAILED]`. Because `logind` is down, Ctrl+Alt+F2 does NOT switch TTY, so you
cannot reach a console to fix it. This is a Secure Boot / unsigned-module
problem, not a Wi-Fi problem — disable SB and reboot rather than debugging
network. (Proxmox's own signed path is the `proxmox-secure-boot-support`
package, which installs a signed shim.)

## After first successful boot: two more blockers before the box is usable

**1. `/etc/fstab` ships as a stub (`# UNCONFIGURED FSTAB`).** If you build the
rootfs by hand (debootstrap + mke2fs) rather than via the installer, the image
has no real fstab, so the root filesystem is never remounted `rw` and systemd
cannot write — services fail in a cluster (`systemd-rfkill`, `pve-cluster`,
`pveproxy`) even with Secure Boot off. This is a DIFFERENT failure from the
Secure Boot one above and it is the usual reason the box never reaches
`multi-user`. Fix: write a real `/etc/fstab` with the root entry
(`LABEL=<label> / ext4 defaults 0 1`) into the image before shipping it, and
confirm root mounts `rw` on first boot. A diagnostic oneshot that writes a
marker file to a FAT32 partition readable from Windows (`P:`) tells you how far
the boot got when the box is otherwise unreachable.

**2. `/etc/hosts` must map the hostname to the real IP, not loopback.** If it
says `pve 127.0.1.1`, `pmxcfs`/`pve-cluster` refuses to start because it needs
the node's actual address. Point the hostname at the static IP and restart the
cluster services.

## Wi-Fi-only host: VMs need a NAT bridge, not a plain bridge

A Linux bridge cannot carry a Wi-Fi client link (`wlp*`), so the default
`vmbr0` bridged to the wireless NIC will not work for VMs. On a Wi-Fi-only
Proxmox host, create `vmbr0` with `bridge-ports none` and NAT the VM subnet out
through the Wi-Fi interface:
```
auto vmbr0
iface vmbr0 inet static
    address 10.10.10.1/24
    bridge-ports none
    bridge-stp off
    bridge-fd 0
    post-up   iptables -t nat -A POSTROUTING -s 10.10.10.0/24 -o wlp5s0 -j MASQUERADE
    post-down iptables -t nat -D POSTROUTING -s 10.10.10.0/24 -o wlp5s0 -j MASQUERADE
```
plus `net.ipv4.ip_forward=1` in `/etc/sysctl.d/`. Apply with `ifreload -a` and
verify `ip_forward=1` and a `curl` from the node before trusting it. VMs on
`10.10.10.0/24` then reach the internet through the phone hotspot.

## Windows-side gotchas

- **CRLF breaks scripts.** A `.ps1`/`.sh` written with `\n` line endings fails
  on Windows with confusing errors. Write with `\r\n` (or strip CR on the
  target) before running.
- **`ipconfig` output is cp866-encoded** on this Windows; decode with
  `out.decode('cp866','ignore')` before grepping for IPv4/gateway, or the
  lines come back as binary garbage.
- After issuing `shutdown /r`, the host stops answering ping — that is
  expected. Find the box on the Wi-Fi subnet with a port scan (22/8006 for
  PVE) rather than assuming it is gone.
