# Proxmox VE на ThinkPad T490: нативная установка без USB и ISO

Как развернуть Proxmox VE на машине, где нет флешки, нет проводной сети и
нельзя рассчитывать на загрузочный ISO. Описывает фактически выполненный и
проверенный маршрут, а не теоретическую схему.

Связанный issue: `theseus-local-lab` [#83](https://github.com/TeaShaman-cyber/theseus-local-lab/issues/83) (historical, in the archive: живой трекер — `local-lab`).

Это не Docker-стек: запись в `infrastructure-registry.yaml` для него не
требуется. Здесь описан отдельный физический гипервизор.

## Итоговое состояние (проверено)

| Параметр | Значение |
|---|---|
| Модель | ThinkPad T490, i5-8265U, 8 ГБ RAM |
| Диск | SSD 256 ГБ, GPT, UEFI |
| Proxmox | `pve-manager 9.2.24`, ядро `7.0.14-26-pve` |
| Root | `/dev/sda4`, ext4, `LABEL=pveroot`, 79 ГБ |
| Адрес | `10.67.163.198` (статический, Wi-Fi `wlp5s0`) |
| Веб | `https://10.67.163.198:8006` |
| Сеть VM | `vmbr0` `10.10.10.1/24`, NAT в `wlp5s0` |
| Windows | сохранена, пункт в GRUB |

Пароль root не хранится в этом документе. Он лежит в переменной окружения
Hermes `PVE_ROOT_PASS`.

## Запуск Docker-образов: нативный OCI-путь

С версии **9.1** Proxmox умеет запускать OCI-образ (то есть любой Docker-образ)
как LXC-контейнер — **без Docker-демона** и без вложенности. Проверено на нашем
хосте: `pve-container 6.1.15`, `skopeo` в `/usr/bin/skopeo`, в коде есть
`archive_is_oci_format` и `restore_oci_archive` (`/usr/share/perl5/PVE/LXC/Create.pm`).

Отдельной команды `pct oci` нет: путь идёт через `pct restore`, который сам
распознаёт OCI-архив по `oci-layout`, `index.json` и `blobs`.

```bash
# 1. Образ из реестра в OCI-архив (skopeo уже стоит на хосте)
#    Пишем в /var/tmp, не в /tmp: /tmp на этом хосте — tmpfs в RAM (3.8 ГБ),
#    а образ Hindsight занимает 3.79 ГБ. Это инцидент #38: «дефицит памяти»,
#    который оказался нашими же архивами в памяти.
skopeo copy docker://ghcr.io/vectorize-io/hindsight:1.1.0 oci-archive:/var/tmp/hindsight-oci.tar

# 2. Развернуть как контейнер; entrypoint и ENV переносятся из образа
pct restore 103 /var/tmp/hindsight-oci.tar \
  --rootfs local:12 --memory 3072 --cores 4 \
  --net0 name=eth0,bridge=vmbr0,ip=10.10.10.13/24,gw=10.10.10.2

# 3. Данные и модели — примонтировать с хоста
pct set 103 -mp0 /srv/hindsight-pg0,mp=/home/hindsight/.pg0
pct set 103 -mp1 /srv/models/l6,mp=/models/l6,ro=1
```

Почему этот путь предпочтительнее Docker внутри LXC:

- нет Docker-демона и вложенных уровней (`nesting`, `keyctl`) — меньше точек отказа;
- тестируется **тот же образ**, что в продакшене, а не пересборка;
- хост PVE остаётся чистым: ни Docker, ни compose на гипервизоре.

Ограничение: проверено наличие механизма и его код, но **не проверен** сквозной
прогон конкретного образа Hindsight — при первом использовании смотреть `pct
start 103` и журнал контейнера. Если образ не поднимется как LXC, остаётся
проверенный запасной путь: Docker внутри отдельного LXC (`--features
nesting=1,keyctl=1`).

## Почему не ISO и не VHD

- **PVE ISO с внутреннего раздела** — штатный initrd не умеет читать ISO как
  файл; обход через вложение образа в initrd даёт ~1.7 ГБ initrd и хрупкую
  цепочку из GRUB, shim и answer-файла.
- **VHD/VHDX + native boot** — Microsoft документирует native boot только для
  Windows. Для Linux нужен кастомный initramfs с loop-монтированием VHD.
- **VirtualBox raw VMDK по разделу** — на Windows raw-доступ к разделам
  системного диска ограничен (`VERR_ACCESS_DENIED`); требуется перевести диск
  в offline, что для системного невозможно.
- **WSL `--mount`** — по документации Microsoft не подключает диск, на котором
  установлена Windows.

## Рабочий маршрут

### 1. Раздел под Linux

Windows умеет ужать `C:` и создать раздел; форматировать в ext4 — нет.

```powershell
$p = Get-Partition -DriveLetter C
Resize-Partition -DriveLetter C -Size ($p.Size - 80GB)
New-Partition -DiskNumber 0 -UseMaximumSize -DriveLetter L
```

Раздел оставляется RAW: Windows не должен его монтировать.

### 2. Сборка образа в Linux-контейнере

Сборка идёт вне целевой машины, поэтому на ноутбук не ставится ни WSL, ни
VirtualBox, ни Hyper-V.

```bash
debootstrap --variant=minbase \
  --include=systemd,systemd-sysv,dbus,kmod,initramfs-tools,locales,tzdata \
  trixie /build/rootfs http://deb.debian.org/debian

# репозиторий Proxmox
echo 'deb [trusted=yes] http://download.proxmox.com/debian/pve trixie pve-no-subscription' \
  > /build/rootfs/etc/apt/sources.list.d/pve.list

chroot /build/rootfs apt-get install -y proxmox-ve postfix \
  openssh-server wpasupplicant firmware-iwlwifi iw dhcpcd5

mke2fs -q -t ext4 -F -d /build/rootfs -L pveroot /build/root.img
```

Критично: перед `mke2fs -d` размонтировать и очистить `/proc`, `/sys`, `/dev`
внутри rootfs. Иначе `mke2fs` уходит в бесконечное копирование псевдо-ФС.

Обязательный минимум в образе:

```text
/etc/fstab      LABEL=pveroot / ext4 defaults,errors=remount-ro 0 1
/etc/hosts      127.0.0.1 localhost + <реальный IP> pve.lab pve
```

Без первой строки root остаётся `ro` и systemd валит службы.
Без второй `pmxcfs` отказывается стартовать: «Unable to resolve node name
'pve' to a non-loopback IP address».

### 3. Запись образа в раздел

Windows позволяет открыть неиспользуемый раздел как устройство и писать
побайтово. Путь собирается через `[char]92`, иначе PowerShell нормализует
`\\.\` в текущий диск.

```powershell
$d = [char]92
$dev = $d + $d + '.' + $d + 'GLOBALROOT' + $d + 'Device' + $d + 'Harddisk0' + $d + 'Partition7'
$dst = New-Object System.IO.FileStream($dev, [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::ReadWrite)
```

Если запись отклоняется (`UnauthorizedAccessException`), писать в
`\\.\PhysicalDrive0` по смещению раздела:

```powershell
$dst.Position = 164652646400   # Offset раздела из Get-Partition
```

Проверка после записи: сигнатура ext4 `53 EF` по смещению `0x438` и метка
`pveroot`.

### 4. Загрузчик

Отдельный signed shim/GRUB на существующем ESP (взят из PVE ISO), конфиг в
двух местах — иначе автозагрузка не находит его:

```text
\EFI\BOOT\grub.cfg
\boot\grub\grub.cfg
```

```text
set timeout=3
set default=0

menuentry 'Proxmox VE 9.2' {
    search --no-floppy --label pveroot --set=root
    linux /boot/vmlinuz-7.0.14-26-pve root=LABEL=pveroot rw quiet
    initrd /boot/initrd.img-7.0.14-26-pve
}

menuentry 'Windows 11' {
    search --no-floppy --file /EFI/Microsoft/Boot/bootmgfw.efi --set=root
    chainloader /EFI/Microsoft/Boot/bootmgfw.efi
}
```

Опираться на метку или UUID, не на номер раздела: раздел `p7` в Windows — это
`/dev/sda4` в Linux.

### 5. Первая загрузка

```bash
resize2fs /dev/sda4          # 8 ГБ образа -> 79 ГБ раздела
```

## Secure Boot

При включённом Secure Boot ядро Proxmox не загружает неподписанные модули:
падают `systemd-rfkill`, `systemd-backlight`, `pvefw-logger`, `systemd-logind`,
не поднимается Wi-Fi.

Два пути:

- отключить Secure Boot в BIOS (`Security` → `Secure Boot` → `Disabled`);
- установить `proxmox-secure-boot-support` (подписанный shim и ядро). Учесть
  известный дефект: на 9.2.2 `apt full-upgrade` может снести пакет и заменить
  shim на Debian-овский, после чего загрузка падает с `security violation`.

## Wi-Fi вместо Ethernet

Proxmox не поддерживает Wi-Fi штатно: в базовой системе нет `wpasupplicant` и
`firmware-iwlwifi`, а бридж поверх Wi-Fi не работает.

Рабочая схема — статический адрес на Wi-Fi плюс NAT для гостей:

```text
/etc/network/interfaces
auto vmbr0
iface vmbr0 inet static
    address 10.10.10.1/24
    bridge-ports none
    bridge-stp off
    bridge-fd 0
    post-up   iptables -t nat -A POSTROUTING -s 10.10.10.0/24 -o wlp5s0 -j MASQUERADE
    post-down iptables -t nat -D POSTROUTING -s 10.10.10.0/24 -o wlp5s0 -j MASQUERADE
```

```bash
printf 'net.ipv4.ip_forward=1\n' > /etc/sysctl.d/99-pve-forward.conf
sysctl -q -p /etc/sysctl.d/99-pve-forward.conf
ifreload -a
```

Wi-Fi поднимается отдельным oneshot-сервисом: `wpa_supplicant` по сохранённому
профилю, затем статический адрес и маршрут по умолчанию. DHCP на хотспоте
смартфона не даёт резервирования адресов, поэтому адрес фиксируется на машине.

## Диагностика

Если система не догружается, лог можно снять на FAT32-раздел, читаемый из
Windows. Сервис `pve-diag` монтирует раздел по метке `PVEBOOT` и пишет
`boot-<timestamp>.txt` с `dmesg`, `journalctl`, `ip a`, `systemctl --failed`.

## Проверки после развёртывания

```bash
pveversion
systemctl --failed
ip -br a
curl -s -o /dev/null -w '%{http_code}\n' http://deb.debian.org/
curl -sk -o /dev/null -w '%{http_code}\n' https://127.0.0.1:8006/
```

Ожидаемо: `0 units` в failed, `200` на обоих запросах.
