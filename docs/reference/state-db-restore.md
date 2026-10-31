# Восстановление `state.db` из снапшота

Инструкция для случая, когда база Hermes повреждена, потеряна или её нужно
откатить. Написана так, чтобы её выполнил агент без доступа к истории обсуждений:
все пути, команды и критерии проверки указаны явно.

## Что где лежит

| Что | Путь |
|---|---|
| Живая база | `C:\Users\s.poklad\AppData\Local\hermes\state.db` |
| Снапшоты | `E:\theseus-backups\state-db\snapshots\state-YYYYmmdd-HHMMSS.db` |
| Лог снапшотов | `E:\theseus-backups\state-db\snapshot.log` |
| Лог проверок | `E:\theseus-backups\state-db\drill.log` |
| Инструменты | `E:\local-lab\tools\state_db_backup\{snapshot,verify}.py` |
| Ночной бэкап вне машины | restic, `D:/theseus-backups` + S3 |

Снапшоты снимаются задачей `Theseus State DB Snapshot` каждые 6 часов, хранятся
**7 последних** (ротация автоматическая). Каждый — полная консистентная копия,
а не инкремент: для восстановления нужен ровно один файл.

## Шаг 1. Выбрать снапшот

```bash
ls -lt /e/theseus-backups/state-db/snapshots/ | head -10
```

Берите **самый свежий**, который проходит проверку (шаг 2). Если свежий не
проходит — следующий по порядку.

Не берите снапшот, если в `snapshot.log` у него `"ok": false`:

```bash
tail -5 /e/theseus-backups/state-db/snapshot.log
```

## Шаг 2. Проверить выбранный снапшот

```bash
cd /e/local-lab
python tools/state_db_backup/verify.py --json
```

`verify.py` проверяет **последний** снапшот. Ожидаемый результат:

```json
{"ok": true, "integrity": "ok", "messages": 166719, "sessions": 424,
 "gap_hours": 0.1, "detail": "snapshot is usable"}
```

Обязательные условия: `integrity == "ok"`, `messages > 0`, `gap_hours < 26`.

Проверить конкретный файл, а не последний:

```bash
python - <<'PY'
import sqlite3, pathlib
p = pathlib.Path("E:/theseus-backups/state-db/snapshots/state-20261031-192405.db")
con = sqlite3.connect(f"file:{p.as_posix()}?immutable=1", uri=True)
print(con.execute("PRAGMA integrity_check").fetchone()[0])
print("messages", con.execute("SELECT count(*) FROM messages").fetchone()[0])
PY
```

Если `integrity_check` вернул не `ok` — файл непригоден, берите предыдущий.
Восстановление из повреждённого снапшота хуже, чем откат на сутки назад.

## Шаг 3. Остановить всё, что пишет в базу

Это обязательный шаг. Копирование поверх работающей базы даёт второй дефект
вместо исправления первого.

```bash
# 1. Hermes desktop и gateway
powershell -NoProfile -Command "Get-Process Hermes, pythonw -ErrorAction SilentlyContinue | Stop-Process -Force"

# 2. Убедиться, что никто не держит файл
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -like '*hermes*' } | Select-Object ProcessId, Name"
```

Пустой вывод второго запроса — то, что нужно.

## Шаг 4. Сохранить текущее состояние

Даже повреждённую базу не выбрасывайте до успешного восстановления: иногда из неё
можно вытащить то, чего нет в снапшоте.

```bash
TS=$(date +%Y%m%d-%H%M%S)
mkdir -p E:/theseus-backups/state-db/broken
mv "$LOCALAPPDATA/hermes/state.db"     "E:/theseus-backups/state-db/broken/state-$TS.db"
mv "$LOCALAPPDATA/hermes/state.db-wal" "E:/theseus-backups/state-db/broken/state-$TS.db-wal" 2>/dev/null
mv "$LOCALAPPDATA/hermes/state.db-shm" "E:/theseus-backups/state-db/broken/state-$TS.db-shm" 2>/dev/null
```

`-wal` и `-shm` **обязательно** убрать вместе с базой. Оставленный старый `-wal`
рядом с новым файлом — это способ получить испорченную базу из корректного
снапшота: SQLite применит к новой базе чужие страницы журнала.

## Шаг 5. Положить снапшот на место

```bash
cp "E:/theseus-backups/state-db/snapshots/state-20261031-192405.db" \
   "$LOCALAPPDATA/hermes/state.db"
```

Права и владелец при копировании сохраняются; отдельная настройка ACL не нужна.

## Шаг 6. Проверить ДО запуска Hermes

```bash
python - <<'PY'
import sqlite3, os, pathlib
db = pathlib.Path(os.environ["LOCALAPPDATA"]) / "hermes" / "state.db"
con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
print("integrity:", con.execute("PRAGMA integrity_check").fetchone()[0])
print("messages :", con.execute("SELECT count(*) FROM messages").fetchone()[0])
print("sessions :", con.execute("SELECT count(*) FROM sessions").fetchone()[0])
print("fts      :", con.execute("SELECT count(*) FROM messages_fts_trigram").fetchone()[0])
PY
```

Ожидается: `integrity: ok`, `messages` совпадает с тем, что показывал шаг 2,
`fts` больше нуля. Если `fts` = 0 — индекс не восстановился, см. ниже.

## Шаг 7. Запустить Hermes и убедиться

```bash
powershell -NoProfile -Command "Start-Process 'C:\Users\s.poklad\AppData\Local\Programs\Hermes\Hermes.exe'"
```

Проверка в интерфейсе: история сессий открывается, поиск по истории находит
известное слово (например `merge-intent`).

## Если FTS-индекс пуст

Снапшот содержит FTS-таблицы (`messages_fts`, `messages_fts_trigram` и их
служебные части), поэтому в норме индекс переезжает вместе с базой. Если он пуст:

```bash
python - <<'PY'
import sqlite3, os, pathlib
db = pathlib.Path(os.environ["LOCALAPPDATA"]) / "hermes" / "state.db"
con = sqlite3.connect(str(db))
con.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
con.execute("INSERT INTO messages_fts_trigram(messages_fts_trigram) VALUES('rebuild')")
con.commit()
print("rebuild выполнен")
print(con.execute("SELECT count(*) FROM messages_fts_trigram").fetchone()[0])
PY
```

`rebuild` занимает минуты на базе такого размера. Это восстановление индекса из
данных, а не восстановление данных.

## Если снапшоты кончились

1. **Ночной restic-бэкап**: `D:/theseus-backups`, восстановление — `restic restore`.
   Он уходит и в S3, поэтому переживает потерю диска.
2. **Старая реплика Litestream** (`E:\theseus-backups\state-db\litestream`) —
   **не использовать**. Она остановилась 31 октября в 02:01, дефект восстановления
   описан в `infrastructure.md` (RULE-015). Читать оттуда бессмысленно.

## Чего не делать

- Не копировать снапшот поверх живой базы при работающем Hermes.
- Не оставлять старые `-wal`/`-shm` рядом с восстановленной базой.
- Не удалять повреждённую базу до успешной проверки восстановленной.
- Не запускать `VACUUM` на восстановленной базе «для порядка» — `VACUUM INTO`
  делает копию, обычный `VACUUM` перезаписывает файл без запасной копии.
