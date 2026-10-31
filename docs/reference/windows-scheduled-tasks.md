# Задачи Планировщика на Windows: pythonw, venv и мигающие окна

Записано после двух часов отладки 31 октября 2026. Инцидент стоил дорого ровно
потому, что каждый шаг выглядел правдоподобно, а проверялся только последний.

## Симптом

Во время работы Hermes на секунду всплывало консольное окно `Windows Terminal`
размером 1168×624. Заголовок — путь к `pythonw.exe` из venv Hermes. Пользователь
описывал это как «мигает, как PowerShell».

## Причина

В venv Hermes `python.exe` и `pythonw.exe` — **один и тот же файл**:

```
venv python.exe   size=45568  sha256=51c61ca5c74591ba
venv pythonw.exe  size=45568  sha256=51c61ca5c74591ba   ← побайтово идентичны
```

Venv создан `uv` 0.11.31, и он кладёт оба имени на один консольный лаунчер.
PE-заголовок подтверждает:

| Файл | Subsystem |
|---|---|
| `Python313\pythonw.exe` | `2` — **GUI**, окна нет |
| `Python313\python.exe` | `3` — CONSOLE |
| `hermes-agent\venv\Scripts\pythonw.exe` | `3` — **CONSOLE**, окно есть |
| `hermes-agent\venv\Scripts\python.exe` | `3` — CONSOLE |

То есть задача, настроенная «на `pythonw.exe`, чтобы без окна», создавала консоль
при каждом запуске. Это не настройка задачи и не поведение Hermes — это свойство
самого файла.

Вторая половина причины — наша: 30 октября задачи `Theseus *` перевели на скрипты
`local-lab`, но интерпретатор взяли из **venv продукта Hermes**. Это привязка к
рантайму, который пересоздаётся при обновлении: задачи отвалились бы молча.

## Как проверить любой `pythonw.exe`, прежде чем на него ставить задачу

```bash
python - <<'PY'
import struct, pathlib
def subsystem(p):
    d = pathlib.Path(p).read_bytes()
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    return struct.unpack_from("<H", d, pe + 24 + 68)[0]
for p in (r"C:\Python313\pythonw.exe",):
    print(p, "->", {2: "GUI", 3: "CONSOLE"}.get(subsystem(p), subsystem(p)))
PY
```

`GUI` — окна не будет. `CONSOLE` — будет, как ни назови файл.

## Правила

1. **Не привязывать задачи к venv продукта.** Интерпретатор задачи — системный
   `Python313\pythonw.exe` или отдельный venv вне каталога продукта. Обновление
   Hermes не должно ломать расписание.
2. **Проверять `subsystem`, а не имя файла.** `pythonw.exe` — это имя, не гарантия.
3. **Задавать `WorkingDirectory`.** Планировщик запускает процесс в
   `C:\Windows\System32`, и скрипт, читающий файл относительно cwd, падает с
   `result=3` (`ERROR_PATH_NOT_FOUND`).
4. **Зависимости — явно.** `push_nous_usage.py` импортирует `hermes_cli`, тому нужен
   `ruamel.yaml`; в системном Python его не было, и задача молча отдавала
   `metric=read_ok value=0` с кодом 3.
5. **Проверять код возврата задачи (`LastTaskResult`) и лог скрипта.** Задача
   `Ready` с `result=0` — не доказательство, что данные дошли.

## Как искали (и почему это заняло два часа)

Каждая версия ловушки отвечала на свой вопрос, и каждая следующая опровергала
предыдущую гипотезу:

| Ловушка | Что ловила | Почему этого мало |
|---|---|---|
| v1–v2 | создание процессов | 3 364 `conhost` за полчаса — процесс ≠ окно |
| v3 | `IsWindowVisible` | ConPTY и Electron отдают `true` для невидимых окон |
| v4 | размер + `WS_EX_TOOLWINDOW` | верно отделяла служебные, но не сказала, **кто** создаёт |
| v5 | `SetWinEventHook` | поймала момент показа, но без родителя |
| **v6** | **+ родитель (`NtQueryInformationProcess`)** | **`parent = svchost -k DcomLaunch`** → Планировщик |

Ложные гипотезы, которые пришлось отбросить фактами: handoff при перезапуске
(окна шли чаще перезапусков), `rsvd.exe` / Red Shield (процесс существует, но окон
не создаёт), плагины `hermes_otel` и `langfuse` (единственный запуск — `rocm-smi`
с `capture_output`).

Вывод: **окно привязывается к родителю, а не к имени процесса**. Без `parentPid`
любая версия ловушки даёт правдоподобный, но неверный ответ.

## Текущее состояние

| Задача | Интерпретатор | Рабочий каталог |
|---|---|---|
| `Theseus FinOps session names` | `Python313\pythonw.exe` | `E:\local-lab` |
| `Theseus Hindsight LLM usage` | `Python313\pythonw.exe` | `E:\local-lab` |
| `Theseus Nous credits` | `Python313\pythonw.exe` | `E:\local-lab` |
| `Theseus Timeweb billing` | `Python313\pythonw.exe` | `E:\local-lab` |
| `Theseus State DB Snapshot` | `Python313\pythonw.exe` | `E:\local-lab` |
| `Theseus Restore Drill` | `Python313\pythonw.exe` | `E:\local-lab` |
| `Theseus Provider Metrics` | `wscript` → VBS, `Run(cmd, 0)` | — |
| `Theseus Litestream` | **отключена** | — |

Проверка после перевода: ловушка v6 за 30 минут не поймала ни одного нового окна
(все четыре записи — до переключения).
