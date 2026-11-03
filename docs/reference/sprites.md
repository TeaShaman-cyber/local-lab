# Fly.io Sprites: хост для агентов

Спрайт — это Linux-машина с постоянным диском. Спит, когда её никто не трогает; просыпается
от команды или входящего HTTP-запроса. Всё, что записано на диск и установлено, остаётся.

Запись в реестре — `INFRA-016`. Контур и история — `local-lab#166`.

## Установка CLI

Официальный установщик на этой машине **не работает**: PowerShell падает на `Get-FileHash`
(командлет отсутствует). Рабочий путь — манифест и сверка хеша:

```bash
V=$(curl -s https://sprites.dev/dl/client/latest)
curl -s "https://sprites.dev/dl/client/$V/manifest.json" -o manifest.json
# взять platforms["windows-x86_64"]: digest и path
curl -sL -o sprite.exe "https://sprites.dev/dl/client/$V/sprite-windows-x86_64.exe"
sha256sum sprite.exe    # сверить с digest ПЕРЕД установкой
```

Дальше положить в `%LOCALAPPDATA%\Programs\Sprite\sprite.exe` и добавить каталог в PATH
пользователя. Проверка: `sprite --version`.

## Вход

```bash
sprite login     # откроет браузер; человек подтверждает
```

Токен создаётся один раз и живёт в профиле CLI. Организация печатается в выводе; при
нескольких организациях нужен `-o <org>`.

## Основные команды

```bash
sprite list                       # все спрайты
sprite info -s <name>             # URL, статус, id
sprite use <name>                 # сделать по умолчанию в этом каталоге
sprite exec -s <name> --no-stdin -- bash -c 'команда'
sprite destroy -s <name> --force  # необратимо: диск, сервисы, чекпоинты, URL
```

**Обязательно указывать `--stdin` или `--no-stdin`.** Без флага `exec` печатает
предупреждение и отключает переподключение при обрыве — выглядит как ошибка, хотя команда
выполнилась.

## Сон, пробуждение, адрес

```text
running  — машина работает, биллится
warm     — процесс заморожен, диск на месте, биллинга нет
cold     — память сброшена, диск на месте, биллинга нет
```

Пробуждение от входящего запроса: 100–500 мс из warm, 1–2 с из cold.
URL спрайта требует токен, пока не включено `sprite config update --url-auth public` —
**публичным не делать**: на спрайте лежит авторизация.

Открытые TCP-соединения при паузе рвутся. Долгие стриминговые вызовы сон не переживают.

## Сервисы

Долгоживущий процесс, который должен быть запущен при пробуждении, оформляется сервисом:

```bash
sprite exec -s <name> -- sprite-env services create web --cmd python3 --args server.py --http-port 8080
sprite exec -s <name> --no-stdin -- sprite-env services list
sprite exec -s <name> --no-stdin -- tail -n 50 /.sprite/logs/services/web.log
```

`--cmd` — только бинарь, аргументы в `--args` через запятую. HTTP-порт может держать
только один сервис.

## gcloud OAuth для Drive

Маршрут проверен на `theseus-cloud-shell-probe` 2026-11-03. Устройство:

```text
/home/sprite/.config/gcloud-theseus/auth.in   FIFO (mkfifo -m 600)
сервис gcloud-drive-auth                      gcloud auth login ... <&3, читает FIFO
сервис gcloud-code-receiver                   HTTP-форма, пишет присланный код в тот же FIFO
профиль                                       /home/sprite/.config/gcloud-theseus/
```

Порядок:

1. Убедиться, что оба сервиса `running` и FIFO на месте (`ls -l .../auth.in` → `prw-------`).
2. Взять актуальную ссылку из `login.log`: `grep -oE 'https://accounts\.google\.com/[^ ]+'`.
3. Человек открывает ссылку, разрешает Drive, получает код.
4. Код попадает в FIFO **не через чат**: либо формой `gcloud-code-receiver`, либо файлом,
   который уходит потоком:

```bash
head -n1 code.txt | sprite exec -s <name> --stdin -- bash -c \
  'IFS= read -r C; printf "%s\n" "$C" > /home/sprite/.config/gcloud-theseus/auth.in'
```

5. Проверить результат, не печатая токен:

```bash
sprite exec -s <name> --no-stdin -- bash -c \
  'export CLOUDSDK_CONFIG=/home/sprite/.config/gcloud-theseus; \
   gcloud auth list --format="value(account,status)"; \
   T=$(gcloud auth print-access-token); echo token_len=${#T}'
```

Ожидаемо: `pokladsemen@gmail.com *` и длина токена около 258.

## Грабли

| симптом | причина | что делать |
|---|---|---|
| `Get-FileHash` не найден | старый PowerShell | ставить по манифесту + `sha256sum` |
| `executable file ... not found` | строка целиком ушла как имя файла | `-- bash -c '...'`, а не `-- '...'` |
| предупреждение про stdin | нет `--stdin`/`--no-stdin` | добавить флаг |
| gcloud упал, сервис перезапустился | код протух, пока открывали ссылку | взять **новую** ссылку из `login.log` |
| `exit.code` не появляется, но вход прошёл | процесс висит на FIFO | безвредно; смотреть `gcloud auth list`, а не код возврата |
| спрайт в статусе `cold` | спал | первый `exec` его разбудит, это норма |

## Проверка состояния (без изменений)

```bash
sprite list
sprite info -s theseus-cloud-shell-probe
sprite exec -s theseus-cloud-shell-probe --no-stdin -- bash -c \
  'export CLOUDSDK_CONFIG=/home/sprite/.config/gcloud-theseus; gcloud auth list --format="value(account,status)"'
```

## Что не делать

- не включать `--url-auth public` на спрайте с авторизацией;
- не класть секреты в спрайт — для внешних API есть Connectors, токен остаётся в организации;
- не удалять чужие спрайты: `destroy` необратим.
