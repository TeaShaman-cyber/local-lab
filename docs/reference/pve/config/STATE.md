# Снимок конфигурации PVE T490
date: 2026-10-28T11:10:11+00:00
host: pve.lab
kernel: 7.0.14-26-pve
pve-manager/9.2.24/66bc407c6d90a67a (running kernel: 7.0.14-26-pve)

## Пакеты (ключевые)
lvm2 2.03.31-2+pmx1
proxmox-archive-keyring 4.0
proxmox-backup-client 4.2.8-1
proxmox-backup-file-restore 4.2.8-1
proxmox-backup-restore-image 1.0.0
proxmox-default-kernel 2.1.0
proxmox-enterprise-support-keyring 1.1
proxmox-firewall 1.2.3
proxmox-firewall-data 0.1
proxmox-kernel-7.0 7.0.14-26
proxmox-kernel-7.0.14-26-pve-signed 7.0.14-26
proxmox-kernel-helper 9.2.1
proxmox-mail-forward 1.0.3
proxmox-mini-journalreader 1.7
proxmox-offline-mirror-docs 0.8.0
proxmox-offline-mirror-helper 0.8.0
proxmox-termproxy 2.1.0
proxmox-ve 9.2.0
proxmox-websocket-tunnel 1.0.0
proxmox-widget-toolkit 5.2.10
pve-cluster 9.1.7
pve-container 6.1.15
pve-docs 9.2.13
pve-edk2-firmware-legacy 4.2026.08-1
pve-edk2-firmware-ovmf 4.2026.08-1
pve-esxi-import-tools 1.0.1
pve-firewall 6.0.6
pve-firmware 3.18-8
pve-ha-manager 5.2.5
pve-i18n 3.11.0
pve-lxc-syscalld 2.0.2
pve-manager 9.2.24
pve-nvidia-vgpu-helper 0.3.1
pve-qemu-kvm 11.0.3-4
pve-xtermjs 6.0.0-3
pve-yew-mobile-gui 0.8.0
pve-yew-mobile-i18n 3.11.0
wireguard-tools 1.0.20210914-3
zfs-zed 2.4.4-pve1
zfsutils-linux 2.4.4-pve1

## LXC
VMID       Status     Lock         Name                
100        running                 vpn-gw              
101        running                 test-gw             

## Storage
Name         Type     Status     Total (KiB)      Used (KiB) Available (KiB)        %
local         dir     active        82447304         6468848        72522820    7.85%
