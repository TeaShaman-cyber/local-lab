# local-alerts

Плагин из двух половин: чип и панель в Hermes Desktop и серверный маршрут, который
читает состояние правил Grafana. Токен живёт только на сервере.

```text
__init__.py             обязательный вход агентской половины
plugin.yaml             метаданные пакета
dashboard/manifest.json  описание вкладки; "api": "plugin_api.py"
dashboard/plugin_api.py  маршрут /api/plugins/local-alerts/alerts
desktop/plugin.js        чип statusBar.right + панель panes
```

## Как это доставляется в приложение

Установка — копирование каталога в `$HERMES_HOME/plugins/` и
`hermes plugins enable local-alerts`. Desktop-половину приложение читает **само** из
`plugins/<id>/desktop/plugin.js`: единый пакет — это тот же disk-путь, только каталог
внутри плагина.

Три ловушки, каждая уже стоила часа:

1. **Нет `__init__.py` с `def register(ctx)`** — загрузчик не регистрирует ничего
   («No __init__.py»), агентской строки нет, а desktop-половина привязана к ней:
   плагина нет нигде. Видно только через `hermes plugins doctor local-alerts`.
2. **Ручная копия в `desktop-plugins/<id>/` с самодельным `.hermes-package.json`** —
   маркер пишут установщик и reconcile-проход, посторонний отбрасывается: `plugin.js`
   исчезает, маркер остаётся. Копию руками не делать.
3. **Ожидание, что оно подхватится само после правки** — штатная перезагрузка:
   `Ctrl+K` → `Reload desktop plugins`.

## Проверка

```bash
hermes plugins doctor local-alerts
# OK: runtime discovery, manifest parsing, import, and registration passed

grep -i 'Mounted plugin API routes.*local-alerts' "$LOCALAPPDATA/hermes/logs/agent.log"
# маршрут монтируется на первом запросе (hot-mount)

python tools/contracts/check_hermes_plugin.py   # из корня local-lab
```

В приложении: чип `Alerts ok` в статус-баре, панель `ALERTS` справа.

## Переменные окружения

`GRAFANA_ALERTS_TOKEN` — токен service account с ролью Viewer, только в окружении
шлюза. Значение не коммитить и не выводить.
