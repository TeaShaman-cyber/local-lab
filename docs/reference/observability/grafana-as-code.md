# Grafana как код: gcx

Дашборды и алерты ведутся файлами в репозитории и применяются официальным CLI
`gcx` (grafana/gcx). Ручная правка в UI допустима для эксперимента, но не
считается изменением, пока не попала в git.

Контур зарегистрирован как `INFRA-014` в `infra/registry.yaml`.

## Установка

Бинарь: `C:/Users/s.poklad/AppData/Local/gcx/bin/gcx.exe`, версия `1.7.1`.

**Не в PATH.** Вызывать полным путём или добавить каталог в PATH:

```bash
GCX="C:/Users/s.poklad/AppData/Local/gcx/bin/gcx.exe"
"$GCX" --version
```

Ставится из релиза, а не скриптом установки:

```bash
curl -LO https://github.com/grafana/gcx/releases/download/v1.7.1/gcx_1.7.1_windows_amd64.zip
curl -LO https://github.com/grafana/gcx/releases/download/v1.7.1/gcx_1.7.1_checksums.txt
sha256sum -c <(grep gcx_1.7.1_windows_amd64.zip gcx_1.7.1_checksums.txt)
```

Официальный `install.ps1` на этой машине падает на `Get-FileHash` — рабочий путь
только через архив и сверку контрольной суммы. Бинарь без сверки не запускать:
CLI с правами записи в дашборды — не то, что стоит брать из недоверенного места.

## Авторизация

```bash
"$GCX" login tealaphid71 --server https://tealaphid71.grafana.net --oauth
```

- Контекст: `tealaphid71`, сервер `https://tealaphid71.grafana.net`.
- Токены user-scoped: права как в UI, то есть запись дашбордов есть сразу.
- **В agent-режиме браузер не открывается.** `gcx` печатает URL и код
  подтверждения; открыть ссылку и сверить код должен человек. Процесс ждёт на
  локальном порту, `--oauth` без этого шага не завершится.

Проверка состояния:

```bash
"$GCX" config current-context      # ожидается tealaphid71
"$GCX" config list-contexts
"$GCX" dashboards list --json name # пустой ответ тоже ответ: список читается
```

`gcx config check` показывает предупреждение про контекст `default` — он пустой и
не используется, это нормально.

## Чего не делать

**`GRAFANA_API_TOKEN` (`glc_...`) для дашбордов не годится.** Это access policy
для логов и метрик; `/api/dashboards/db` с ним отдаёт `401`. Нужен OAuth-логин или
service-account token с ролью Editor. Записано и в `tools/finops/README.md`.

## Workflow: дашборд как код

```bash
# 1. выгрузить текущее состояние из Grafana в файлы
"$GCX" resources pull dashboards/hermes-providers -p ./infra/grafana/dashboards

# 2. проверить файлы перед применением
"$GCX" resources validate -p ./infra/grafana/dashboards

# 3. сухой прогон, затем применение
"$GCX" resources push -p ./infra/grafana/dashboards --dry-run
"$GCX" resources push -p ./infra/grafana/dashboards

# 4. доказать, что панель действительно рисует данные
"$GCX" dashboards snapshot hermes-providers --panel 14 --output-dir ./.snap
```

Шаг 4 не декоративный: `push` отвечает `succeeded: 1` и на изменение, которое
ломает панель. Рендер в PNG — единственная проверка, что запрос возвращает
строки, а не пустоту.

Чтение после записи обязательно:

```bash
"$GCX" dashboards get hermes-providers -o json | jq '.metadata["grafana.app/updatedTimestamp"]'
```

## Файлы в репозитории

```
infra/grafana/dashboards/
  dashboards.v0alpha1.dashboard.grafana.app/hermes-providers.json
```

Имя каталога — версия API, её не переименовывать: `gcx resources push` ищет
структуру по этому пути.

## Правила

1. Дашборд, изменённый в UI и не выгруженный в git, — незакоммиченная работа.
   При следующем `push` она будет перезаписана.
2. `push` без `validate` и без read-back не считается применением.
3. Панель без рендера не считается работающей: пустой график отдаёт успешный push.
4. Инструмент, установленный вне PATH, обязан быть в реестре — иначе следующий
   агент решит, что его нет (ровно это и произошло 31.10: `command -v gcx` дал
   пусто, и полчаса ушло на «CLI не установлен», хотя он стоял с 19:25).
