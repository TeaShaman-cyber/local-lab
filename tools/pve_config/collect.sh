#!/bin/bash
# Собирает конфигурацию PVE-хоста в tar-архив на stdout.
# Запуск: ssh root@10.67.163.198 'bash -s' < tools/pve_config/collect.sh > snapshot.tar
# Секреты маскируются: приватные ключи, токены, host keys, authorized_keys.
set -euo pipefail

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
OUT="$WORK/pve-t490-config"
mkdir -p "$OUT"

copy() {
    local src="$1"
    local dst="$OUT/${2:-$(basename "$src")}"
    [ -e "$src" ] || return 0
    mkdir -p "$(dirname "$dst")"
    cp -a "$src" "$dst" 2>/dev/null || true
}

# Сеть и базовые файлы
copy /etc/network/interfaces
copy /etc/hosts
copy /etc/hostname
copy /etc/fstab
copy /etc/resolv.conf
copy /etc/apt/sources.list
copy /etc/apt/sources.list.d/pve.list
for f in /etc/sysctl.d/*.conf; do copy "$f" "sysctl.d/$(basename "$f")"; done

# Наши systemd-юниты
for u in alloy.service awg-quick@.service pve-cpufreq.service pve-wifi.service pve-diag.service; do
    copy "/etc/systemd/system/$u" "systemd/$u"
done

# Конфиги сервисов
copy /etc/alloy/config.alloy
copy /usr/local/sbin/pve-wifi-up
copy /usr/local/sbin/pve-diag

# PVE
copy /etc/pve/storage.cfg
for c in /etc/pve/lxc/*.conf; do copy "$c" "pve/lxc/$(basename "$c")"; done

# WireGuard/AmneziaWG — с маскированием ключей
if [ -f /etc/amnezia/amneziawg/awg0.conf ]; then
    mkdir -p "$OUT/amneziawg"
    sed -E 's/^(PrivateKey|PresharedKey)[[:space:]]*=.*/\1 = <redacted>/' \
        /etc/amnezia/amneziawg/awg0.conf > "$OUT/amneziawg/awg0.conf"
fi

# Grafana token — не включаем содержимое
if [ -f /etc/default/alloy ]; then
    mkdir -p "$OUT/default"
    printf 'GRAFANA_TOKEN=<redacted, см. PVE_ROOT_PASS/GRAFANA_API_TOKEN в env Hermes>\n' \
        > "$OUT/default/alloy"
fi

# DNS-сервер шлюза (лежит в LXC 100)
if command -v pct >/dev/null 2>&1 && pct status 100 >/dev/null 2>&1; then
    mkdir -p "$OUT/vpn-gw"
    pct exec 100 -- cat /etc/dnsmasq.d/lab.conf > "$OUT/vpn-gw/dnsmasq-lab.conf" 2>/dev/null || true
    sed -E 's/^(PrivateKey|PresharedKey)[[:space:]]*=.*/\1 = <redacted>/' \
        <(pct exec 100 -- cat /etc/amnezia/amneziawg/awg0.conf 2>/dev/null) \
        > "$OUT/vpn-gw/awg0.conf" 2>/dev/null || true
fi

# Версии и состояние
{
    echo "# Снимок конфигурации PVE T490"
    echo "date: $(date -Is)"
    echo "host: $(hostname)"
    echo "kernel: $(uname -r)"
    pveversion 2>/dev/null | head -1 || true
    echo
    echo "## Пакеты (ключевые)"
    dpkg -l 2>/dev/null | awk '/^ii/ && $2 ~ /^(proxmox|pve-|wireguard|amnezia|lldpd|zfs|lvm)/ {print $2" "$3}'
    echo
    echo "## LXC"
    pct list 2>/dev/null || true
    echo
    echo "## Storage"
    pvesm status 2>/dev/null || true
} > "$OUT/STATE.md"

# Проверка: в архиве не должно быть приватных ключей
if grep -rqiE 'BEGIN (OPENSSH|RSA|EC) PRIVATE KEY|^PrivateKey = [A-Za-z0-9+/=]{20,}' "$OUT" 2>/dev/null; then
    echo "ERROR: обнаружен секрет в снимке, сборка прервана" >&2
    exit 1
fi

tar -C "$WORK" -czf - pve-t490-config
