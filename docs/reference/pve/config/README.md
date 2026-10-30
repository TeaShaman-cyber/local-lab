# Снимок конфигурации PVE-хоста T490

Это версионируемая копия конфигурации лабораторного гипервизора: то, из чего
сервер можно собрать заново, если диск умрёт.

Не заменяет бэкап данных (VM, контейнеры, образы) — только конфигурацию.

## Что внутри

| Путь | Что это |
|---|---|
| `interfaces`, `hosts`, `hostname`, `resolv.conf` | сеть хоста |
| `fstab`, `sysctl.d/` | монтирование и форвардинг |
| `systemd/` | наши юниты: `alloy`, `awg-quick@`, `pve-cpufreq`, `pve-wifi`, `pve-diag` |
| `pve-wifi-up`, `pve-diag` | скрипты Wi-Fi и диагностики загрузки |
| `config.alloy` | конфиг коллектора метрик PVE и логов LXC Hindsight |
| `amneziawg/awg0.conf` | туннель хоста, ключи вырезаны |
| `pve/lxc/*.conf` | конфигурация контейнеров |
| `vpn-gw/` | шлюз: `dnsmasq` и его туннель, ключи вырезаны |
| `default/alloy` | заглушка вместо файла с токеном Grafana |
| `STATE.md` | версии, список LXC, состояние storage на момент снимка |

## Чего здесь нет и не должно быть

- приватных ключей WireGuard/AmneziaWG;
- токена Grafana;
- SSH host keys и `authorized_keys`;
- `/etc/pve/priv/*`;
- данных и образов дисков.

Скрипт сбора сам прерывается, если находит приватный ключ в снимке.

## Доступ

```text
host      10.67.163.198
user      root
ключ      C:\Users\s.poklad\.ssh\pve_t490
алиас     ssh pve   (прописан в ~/.ssh/config)
```

Пароль root — в переменной окружения Hermes `PVE_ROOT_PASS`. В репозиторий и в
чат не попадает.

Без алиаса:

```bash
ssh -i C:/Users/s.poklad/.ssh/pve_t490 root@10.67.163.198
```

Веб-интерфейс: `https://10.67.163.198:8006` (сертификат самоподписанный).

Шлюз для гостей — отдельный контейнер, в него заходят с хоста:

```bash
ssh pve
pct exec 100 -- bash
```

## Как обновить

```bash
ssh root@10.67.163.198 'bash -s' < tools/pve_config/collect.sh \
  > /tmp/pve-snapshot.tar
rm -rf docs/reference/pve-t490-config
tar -xzf /tmp/pve-snapshot.tar -C docs/reference
```

Затем просмотреть diff, убедиться, что секретов нет, и закоммитить.

## Как восстановить

Снимок — это набор файлов, а не автоматический инсталлятор. Порядок:

1. Развернуть базовую систему (см. `docs/reference/proxmox-t490-native-install.md`).
2. Скопировать `interfaces`, `hosts`, `fstab`, `sysctl.d/` на место.
3. Вернуть юниты из `systemd/` и скрипты из корня снимка в `/usr/local/sbin/`.
4. Восстановить `config.alloy`, подставить токен Grafana в `/etc/default/alloy`.

Важно: snapshot намеренно содержит `<GRAFANA_TOKEN>` вместо секрета. Не копировать
этот файл в live буквально: в рабочем `/etc/alloy/config.alloy` обе `password`
строки должны быть `sys.env("GRAFANA_TOKEN")`, а `/etc/default/alloy` должен
содержать `GRAFANA_TOKEN` из root-only secret file. Значение не печатать и не
класть в Git или issue.

`config.alloy` отправляет `/var/log/lxc-103.log` в Grafana Cloud Loki с labels
`host=pve-t490`, `service=hindsight-stand`, `container=lxc-103`, `log_type=lxc`.
Для Loki endpoint нужен внешний host `logs-prod-012.grafana.net`; внутренний
`*-local-nlb.internal.grafana.net` с PVE не используется. Маршрут этого IP
должен присутствовать в узком `AllowedIPs` AmneziaWG route.
5. Положить `amneziawg/awg0.conf` и подставить реальный `PrivateKey`.
6. Пересоздать контейнеры по `pve/lxc/*.conf`, внутри шлюза — `vpn-gw/`.

Секреты при восстановлении берутся из хранилища секретов, не из репозитория.
