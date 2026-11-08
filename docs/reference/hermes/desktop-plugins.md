# Desktop-плагины Hermes: доставка, загрузка, диагностика

Дополнение к upstream-документации (`hermes-agent/website/docs/developer-guide/desktop-plugin-sdk.md`)
и репозиторию примеров `NousResearch/hermes-example-plugins`. Здесь — то, что проверено
на живом приложении и стоило нескольких часов неверных догадок. Upstream не копируется:
оригинал читается в профиле, тут — границы и ловушки.

## Три способа доставки UI

| Способ | Где лежит | Кто использует |
|---|---|---|
| Disk (рекомендуется) | `$HERMES_HOME/desktop-plugins/<id>/plugin.js` | только UI, без Python |
| **Единый пакет** | `$HERMES_HOME/plugins/<id>/desktop/plugin.js` | плагин с агентской половиной |
| Bundled | `apps/desktop/src/plugins/<id>/plugin.tsx` | только внутри приложения |

Единый пакет — это тот же disk-путь, только каталог внутри агентского плагина:
приложение сканирует `plugins/<id>/desktop/` само. Имя каталога обязано совпадать с `id`.

## Ловушка 1: нет `__init__.py` — не появляется ничего

Агентская половина регистрируется только при наличии `__init__.py` с
`def register(ctx)`. Без него загрузчик не регистрирует пакет, агентской строки не
существует, а desktop-половина привязана к этой строке — поэтому в
Capabilities → Plugins нет **ни одной** половины. Снаружи это выглядит как «приложение
проигнорировало плагин», а не как отсутствующий вход.

Диагностика одной командой:

```bash
hermes plugins doctor <id>
# ERROR: Plugin registration failed: No __init__.py in .../plugins/<id>
# OK: runtime discovery, manifest parsing, import, and registration passed
```

`hermes plugins doctor` — первая команда при любом «плагина нет», раньше любых
действий в UI.

## Ловушка 2: самодельный `.hermes-package.json`

Upstream, раздел про пейринг агентской и desktop-половин:

> The `.hermes-package.json` marker is written by the installer and the reconcile
> pass; one shipped inside a desktop-only repository is dropped on install.

Практическое следствие, наблюдённое на живом приложении: если положить desktop-половину
руками в `desktop-plugins/<id>/` вместе с самодельным маркером, reconcile-проход
удаляет файл, помеченный чужим маркером, — `plugin.js` исчезает, маркер остаётся, чип
не появляется. Симптом ровно такой: «папка есть, файла нет».

Копию в `desktop-plugins/` руками не создавать. Для единого пакета достаточно
`plugins/<id>/desktop/plugin.js`.

## Ловушка 3: перезагрузка — это команда, а не рестарт приложения

Штатный путь: `Ctrl+K` → **Reload desktop plugins**. Disk-плагины приложение стережёт и
подхватывает само, единый пакет — по этой команде.

## Проверка, что половины действительно поднялись

```bash
# 1. пакет регистрируется
hermes plugins doctor local-alerts

# 2. маршрут бэкенда смонтирован (hot-mount на первом запросе)
grep -i 'Mounted plugin API routes.*local-alerts' "$LOCALAPPDATA/hermes/logs/agent.log"

# 3. включён ли Python-бэкенд (отдельно от тумблера в UI)
grep -n 'local-alerts' "$LOCALAPPDATA/hermes/config.yaml"   # plugins.enabled

# 4. контракт пакета в репозитории
python tools/contracts/check_hermes_plugin.py
```

Маршрут смонтирован + панель показывает данные ⇒ обе половины живы, и если чипа нет,
дело в самой contribution, а не в доставке.

## Реальная форма: `local-alerts`

```text
infra/hermes/plugins/local-alerts/
  __init__.py            вход агентской половины (register → None)
  plugin.yaml            метаданные пакета
  dashboard/manifest.json  "api": "plugin_api.py"
  dashboard/plugin_api.py  GET /api/plugins/local-alerts/alerts
  desktop/plugin.js      statusBar.right (чип) + panes (панель ALERTS)
```

Токен Grafana читается только на сервере из окружения шлюза
(`GRAFANA_ALERTS_TOKEN`, service account с ролью Viewer); в рендерер значение не
попадает. Состояние: чип `Alerts ok` в статус-баре, панель `ALERTS` справа с
`firing`/`pending` и списком правил.

## Замечания по зонам

- Константы зон: `apps/desktop/src/sdk/areas.ts:6` —
  `STATUSBAR_AREAS = { left: 'statusBar.left', right: 'statusBar.right' }`,
  `PANES_AREA = 'panes'`. Строковые литералы совпадают с константами, но брать надо
  константы: литерал не поймает переименование.
- Отдельные элементы статус-бара можно прятать (`store/statusbar-prefs.ts`,
  `STATUSBAR_HIDDEN_BY_DEFAULT`), и вся полоса скрывается предпочтением
  `workbench.statusBar.visible`. Если чип «пропал» — сначала проверять эти два
  места, а не код плагина.
- UI собирается вызовами `jsx()`/`jsxs()`: файл грузится без компиляции, JSX не
  распарсится. Импортировать можно только `@hermes/plugin-sdk`, `react`,
  `react/jsx-runtime`.
