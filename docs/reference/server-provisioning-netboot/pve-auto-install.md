# Proxmox VE auto-install booted from Windows (no USB)

Verified working path for booting the Proxmox VE installer on a machine you can
only reach through Windows, with no flash drive. The core trick: the installer's
*job* (lay files on disk) is normal — only the *boot delivery* is unusual, and
UEFI boots a file by path, so you can boot a staged loader on the machine's own
ESP. A plain ISO is not bootable this way because `prepare-iso` must embed the
answer-file retrieval mode into the image; a stock ISO has none.

## Pieces you stage on the Windows machine's ESP

- `bcdedit /create /d "PVE" /application bootloader` -> assign a `{guid}`;
  `bcdedit /set {guid} device partition=<esp>`; `bcdedit /set {guid} path
  \EFI\BOOT\BOOTX64.EFI`; `bcdedit /displayorder {guid} /addfirst`. Put the
  entry first in firmware order so it boots without a boot-menu keypress.
- A signed shim/grub at `\EFI\BOOT\BOOTX64.EFI` (Secure Boot) -> `grubx64.efi`
  -> `grub.cfg`. `grub.cfg` boots the custom initrd.
- The custom initrd (contains the installer) plus a config partition the
  installer mounts (`search --label <label>` in grub.cfg).

## Auto-install pipeline (`prepare-iso` on a Linux host)

1. Write `answer.toml` (proxmox-auto-install-assistant.toml): `install-media =
   [...iso]`, `disk-list = ["sda"]` (whole-disk wipe — confirms Windows can
   still be surrendered), `[network]` static IP + gateway + DNS, `[global-key]`
   or `[global-password]`.
2. `proxmox-auto-install-assistant prepare-iso --answer-file answer.toml
   proxmox-ve_<v>.iso` — MUST run before embedding; this writes the
   answer-file retrieval mode into the image (stock ISO has none).
3. Build a custom `initrd` embedding the prepared ISO (`cpoint`/unmkinitramfs
   + `update-initramfs`-style repack), verified by `sha256`.
4. Transfer the initrd to the Windows host (background scp) and to the
   installer partition; verify size on the host before booting.
5. Reboot into the new EFI entry; the installer auto-runs.

### `--pxe` mode: skip the manual initrd repack

`prepare-iso` has a `--pxe` flag that emits a ready-to-boot `vmlinuz` +
`initrd.img` + a recompressed `*-auto-from-partition.iso` into an `--output`
dir — no hand-built initrd needed. The emitted initrd is a **gzip-compressed
cpio**; to inject extra files (e.g. the ISO itself, a first-boot hook) decompress
with `gzip -d`, add entries with a cpio tool, recompress with `zstd` (the
installer expects zstd). Verify by listing the recompressed archive before
booting.

### Run `prepare-iso` in a Docker container on the Windows host

No separate Linux box needed: `docker run --rm -v <workdir>:/work -w /work
debian:trixie-slim bash /work/container-wifi.sh` where the script does
`apt-get install proxmox-auto-install-assistant` (add the `pve` repo with
`deb [trusted=yes] http://download.proxmox.com/debian/pve trixie
pve-no-subscription`), then runs `prepare-iso`. Mount the ISO, answer file and
hook into `/work` so the outputs land back on the Windows host. The OpenPGP
signature warning on the pve repo is harmless with `[trusted=yes]`.

Optionally: `--on-first-boot <file>` at `prepare-iso` time to run a hook in the
installed system (see networking note in SKILL.md).

## Post-install: Wi-Fi-only box comes up OFFLINE

Proxmox base (`pve-base.squashfs`, list with 7-Zip) ships **no `wpasupplicant`
and no `firmware-iwlwifi`**. On a machine whose only network is Wi-Fi, the
installed PVE can neither associate nor bridge link. Options, cheapest first:
- Phone USB tethering at the console -> `apt install firmware-iwlwifi
  wpasupplicant` -> write `wpa-ssid`/`wpa-psk` config + `ifreload -a`.
- A first-boot hook, but `source = "from-url"` needs network (chicken-and-egg);
  `from-iso` needs `--on-first-boot` decided at `prepare-iso` time.

### Concrete first-boot hook (Wi-Fi bootstrap)

Pass the hook with `--on-first-boot <script>` at `prepare-iso` time and set in
`answer.toml`:
```toml
[first-boot]
source = "from-iso"
ordering = "before-network"
```
The hook script should: (1) install `wpasupplicant`, `firmware-iwlwifi` and
`isc-dhcp-client` from **packages baked into the install artifact at build time**,
never by `apt-get` over the network. Downloading them in a first-boot hook is a
chicken-and-egg on a machine whose only link is the Wi-Fi it is trying to bring up,
and it contradicts the offline rule at the top of
`docs/reference/server-provisioning-netboot.md`. Stage the `.deb` files on the
Windows host while it still has internet, carry them inside the image, and install
from the local path:
```sh
dpkg -i /root/wifi/*.deb || apt-get -f install --no-download
```
(2) write `/etc/wpa_supplicant/wpa_supplicant.conf`
with the SSID/PSK (escape quotes/backslashes when templating the values in);
(3) install a `systemd` oneshot `pve-wifi-connect.service` that brings up the
`wl*` interface, runs `wpa_supplicant -B -i <iface> -c <conf>`, then
`dhclient -v <iface>`. Bake the SSID/PSK into the hook at build time (they come
from the Windows host's own Wi-Fi config — read them out of Windows, don't ask
the user to retype).

## Read-only inventory first (always)

Before any write: disk layout + partition table (GPT?), firmware mode (UEFI),
Secure Boot on/off, free space, whether WSL exists (a route for loopback/VHD
work), and reachable ports (22/3389/445/5985). Use it to pick the channel
(SSH vs `\tsclient` bridge) and the boot scheme (shim if Secure Boot).
