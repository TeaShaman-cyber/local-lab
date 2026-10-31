# Server provisioning over a Windows-only remote (no USB / no LAN / Wi-Fi only)

## When to Use

Use when installing a Linux/Proxmox server on a machine you can only touch via
Windows (RDP/SMB/WinRM) with **no USB drives, no wired LAN, Wi-Fi-only
network** — or any variant where the boot files must reach the disk without
removable media.

Goal for THIS user: turn an existing laptop/PC into a Linux/Proxmox server when
you can only talk to it through Windows (RDP), there are no flash drives, no
wired ethernet, and the only network is a phone Wi-Fi hotspot.

## Operational constraints (provisioning-specific)

These came from the original «interaction rules» list. They are about installing a
machine, not about how to talk, so they live here and not in the general contract
(`docs/agents/interaction-rules.md`).

- **The install must be fully OFFLINE.** Flagged twice («ты снова стал включать
  установку с подключением к сети»). No install step may depend on the network. Bake
  Wi-Fi/network configuration into the image at build time; never assume first-boot
  connectivity.
- **Batch privileged edits into ONE script, applied in ONE call.** Dribbling small
  `ssh … pct exec … <<'EOF'` snippets one at a time floods the user with approval
  prompts («слишком много от этого чата пошло запросов на разрешение команд»). Stage
  the script locally, push it once, run it once. Run read-only diagnostics separately
  from changes so the two do not interleave into an approval storm.
- **Standing scope:** «не трогай продовую машину» is absolute. «даже если винду
  запоришь не страшно» lifts the keep-Windows constraint, but the repaired machine's
  Windows stays the boot/load channel until the crossing point — that is technical,
  not a licence to break it early.

## Establish a command channel to Windows first (in order of preference)

1. **SSH** — best. `Add-WindowsCapability OpenSSH.Client~~~...` may stall on
   Windows Update; if so push a working sshd offline (a share the host can reach,
or over the LAN). Once port 22 is open you drive everything yourself and stop
   bothering the user.
2. **RDP `\tsclient` file bridge** — `\tsclient\C$...` is visible from the remote
   machine as its own drive, no network needed. Drop a `.cmd`/`.ps1` there and
   have a polling loop on the host execute it when it appears; write results
   back to your folder. Good for one-shot actions, clunky for many.
3. Mouse/keyboard over RDP — last resort, slow.

**Never** hand the user long paste-drive PowerShell lines repeatedly — they
break on quoting and he correctly got furious ("пошел нахуй с повершелом").
Prefer a file placed via `\tsclient` that he double-clicks (`.cmd`), or SSH
once channel 1 is up. When a CLI offers a non-interactive path (flags, config
file, device-flow curl), use it instead of driving prompts.

## Warp the installer around "no USB" instead of fighting the standard path

The blocker is only *how the boot files reach the disk*, not what the installer
does. The installer just lays files onto the disk; UEFI boots a *file* by path,
not a 'flash partition'. So:

- **Build a bootable entry on the machine's own ESP from Windows** (`bcdedit` or
a small grub/shim chain) pointing at a loader you staged there, rather than
assuming you need removable media.
- **Boot a Linux installer over the network / from a staged ESP file.** For
Proxmox the working path is auto-install via `prepare-iso` + a custom initrd
with the ISO embedded (see `server-provisioning-netboot/pve-auto-install.md`).
- **Don't conclude 'impossible' from a storage-format constraint.** Verifying
the actual loader capabilities (GRUB loopback, PXE, initrd embedding) is
cheaper than the argument; prove it before rejecting.

## Preferred path: write a native Linux partition directly from Windows (L0)

This is the user's ideal and it is reachable — no VHD, no WSL, no VirtualBox,
no ISO. Windows can open an unused partition of the live system disk as a raw
block device and write to it directly:

- Create the partition from Windows (Disk Management / `diskpart`), leave it
  RAW (no filesystem).
- Open it as a device: `\\.\GLOBALROOT\Device\Harddisk0\PartitionN` — opens
  read/write with no third-party driver. Verify with a write+read of the ext4
  magic (`53 EF` at offset 0x438) before trusting it. **After a reboot Windows
  may block the partition path (`UnauthorizedAccessException`); fall back to
  writing `\\.\PhysicalDrive0` at the partition's byte offset** (see
  `server-provisioning-netboot/native-partition-write.md`).
- Build the Linux rootfs in a container (debootstrap) on the build host, then
  write the finished ext4 image into that raw partition. Only the finished
  image crosses to the Windows host.
- Boot via a GRUB entry on the machine's own ESP that does
  `search --label <label>` + `linux /boot/vmlinuz-... root=LABEL=<label>`.

Full recipe in `server-provisioning-netboot/native-partition-write.md`. Prefer this over the
ISO/auto-install pipeline in `server-provisioning-netboot/pve-auto-install.md` when the goal is a
native Linux/Proxmox on a physical partition.

## Post-install networking reality check (do before you lose the box)

- Confirm what the base image actually ships before relying on it. Example:
  **Proxmox base has no `wpasupplicant` and no `firmware-iwlwifi`** (verified by
  listing `pve-base.squashfs`), so a Wi-Fi-only box boots *offline* after
  install. Plan the way back in this order:
  1. **first-boot hook with the packages baked into the image at build time** —
     the only route that needs no network during the install. Must be
     `source = "from-iso"` plus `--on-first-boot` at `prepare-iso` time;
     `from-url` does not qualify, it needs network at first boot.
  2. **USB tethering at the console** — a *post-install rescue*, not an install
     step. It needs a person physically at the machine and, treated as part of
     the install, contradicts the offline rule above (see
     `Operational constraints`).
- If the installer writes a static IP on an interface that cannot physically
  link (e.g. ethernet NIC with no cable, wlan without association), the box is
  unreachable until someone is at the console. State that honestly and hand
  4-6 console lines rather than pretending remote control continues.
- **Wi-Fi-only host: VMs need a NAT bridge, not a plain bridge.** A Linux
  bridge cannot carry a `wlp*` client link, so give VMs a `vmbr0` with
  `bridge-ports none` + MASQUERADE out through the Wi-Fi NIC (recipe in
  `server-provisioning-netboot/native-partition-write.md`).

## Post-install: make a hand-built image actually reach multi-user

A native rootfs built with debootstrap and packaged as an ext4 image boots into
a wall of `[FAILED]` services unless these four things are right. They cost the
bulk of this session's debugging — fix them at image-build time, not by console
archaeology after reboot.

- **An `# UNCONFIGURED FSTAB` placeholder is fatal.** With no `root` entry the
  root filesystem never remounts `rw`, so systemd cannot write and rfkill,
  backlight, logind, pvefw-logger and friends all fail, and even Ctrl+Alt+Fx
  stops working (TTY switch is logind's job). Write a real `/etc/fstab` with the
  root partition **before** packaging the image.
- **Secure Boot rejects unsigned kernel modules → the same service cliff.**
  With SB on, the PVE kernel refuses to load a non-`proxmox-secure-boot-support`
  stack, so the same rfkill/backlight/logind/pvefw set drops. Either install
  that signed shim package or confirm SB is off in firmware before the first
  boot — do this *before* you hand the box to the user for a boot test.
- **GRUB drops to `grub>` because it looks for config by its `prefix`, not our
  `\EFI\BOOT\grub.cfg`.** A hand-placed config under `EFI/BOOT` is not found
  automatically; the second boot still lands in the rescue prompt. Replace the
  shim-derived chain so GRUB finds its config, or place a `grub.cfg` at the
  path GRUB actually probes, before declaring boot routine.
- **PVE's `pmxcfs` fails if `/etc/hosts` names the host `hostname` → loopback.**
  `pve-cluster` needs the real IP in `/etc/hosts`; a loopback entry for the
  hostname is a misconfiguration, not a quirk: point the hostname at its real
  static address.
- **Wi-Fi-only host: don't build a `vmbr0` bridge with `bridge-ports` set to the
  Wi-Fi NIC.** A Linux bridge cannot carry a `wlp*` client link. Use
  `bridge-ports none` + MASQUERADE, or for guest outbound traffic run the VPN
  egress gateway (see `server-provisioning-netboot/vpn-egress-gateway.md`).

## Large-file transfer & verification

- Use background scp for multi-GB ISOs; poll progress on the host with
  `(Get-Item P:\file).Length` and compare to the local size for ETA, don't block.
- `sha256` end-to-end after transfer; a partial copy that 'looks done' is the
  classic silent failure.
- Install `aria2` on the target for big downloads when the origin is slow/
  resumable; reuse the local download helper if it's already on the machine.

## Success = verified postcondition, not "exit 0"

- File present + sha match + port open + NVRAM entry with correct `BootNext` /
  firmware order — check each observable before telling the user it's ready.
- A service 'installed' (OpenSSH) is not 'working with my key' until a
  connection succeeds; an HTTP 200 / health check is not proof the read path
  works.

See `server-provisioning-netboot/pve-auto-install.md` for the concrete Proxmox recipe, and
`server-provisioning-netboot/vpn-egress-gateway.md` for giving guests outbound internet behind a
provider that blocks plain WireGuard.
