"""Сборщик остатка лимитов подписки Codex (issue #28).

Слайс 1: снимок `account/rateLimits/read` → строки метрик для Loki.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.request

# Длительность окна в минутах — единственный надёжный признак смысла окна.
# Порядок primary/secondary провайдер может поменять; длительность — нет.
WINDOW_LABELS = {
    300: "session",    # 5 часов
    10080: "weekly",   # 7 дней
}


def _window_label(window: dict) -> str | None:
    minutes = window.get("windowDurationMins")
    if not isinstance(minutes, (int, float)):
        return None
    return WINDOW_LABELS.get(int(minutes))


def _num(value) -> float | None:
    """API смешивает числа и числовые строки ("balance": "0")."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def build_lines(snapshot: dict) -> list[str]:
    """Превращает снимок лимитов в logfmt-строки."""
    rate = snapshot.get("rateLimits") or {}
    lines: list[str] = []

    for key in ("primary", "secondary"):
        window = rate.get(key) or {}
        label = _window_label(window)
        used = window.get("usedPercent")
        if label is None or used is None:
            # Неизвестная длительность — окно не публикуется: лучше «нет данных»,
            # чем метрика с перепутанным смыслом.
            continue
        lines.append(f"metric=codex_{label}_used_percent value={float(used):.4f}")
        reset_at = _num(window.get("resetsAt"))
        if reset_at is not None:
            lines.append(f"metric=codex_{label}_reset_unix value={reset_at:.0f}")
        minutes = _num(window.get("windowDurationMins"))
        if minutes is not None:
            lines.append(f"metric=codex_{label}_window_minutes value={minutes:.0f}")

    credits = rate.get("credits") or {}
    balance = _num(credits.get("balance"))
    if balance is not None:
        lines.append(f"metric=codex_credits_balance value={balance:.4f}")
    if "hasCredits" in credits:
        lines.append(f"metric=codex_credits_has value={1 if credits.get('hasCredits') else 0}")

    plan = str(rate.get("planType") or "").strip().lower()
    if plan:
        # Тариф — label, а не значение: unwrap требует число.
        lines.append(f"metric=codex_plan value=1 plan={plan}")

    reached = rate.get("rateLimitReachedType")
    lines.append(f"metric=codex_limit_reached value={0 if reached is None else 1}")

    resets = snapshot.get("rateLimitResetCredits") or {}
    count = _num(resets.get("availableCount"))
    if count is not None:
        lines.append(f"metric=codex_reset_credits_available value={count:.0f}")

    return lines


CLIENT_NAME = "codex-usage"
CLIENT_VERSION = "1.0.0"

REQUEST_ID_INIT = 1
REQUEST_ID_READ = 2


def handshake_messages() -> list[dict]:
    """Три сообщения, которые app-server требует перед выдачей лимитов.

    Проверено probe: без `initialized` метод `account/rateLimits/read` молчит.
    `initialized` — нотификация, у неё нет `id`, иначе сервер ждёт ответа.
    """
    return [
        {
            "jsonrpc": "2.0",
            "id": REQUEST_ID_INIT,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
                "capabilities": {},
            },
        },
        {"jsonrpc": "2.0", "method": "initialized", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": REQUEST_ID_READ,
            "method": "account/rateLimits/read",
            "params": {},
        },
    ]


def _parse_json_line(line: str) -> dict | None:
    """app-server пишет в stdout JSON по строке, но может вклинить не-JSON."""
    text = line.strip()
    if not text.startswith("{"):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _codex_executable() -> str:
    """Windows: `codex` — npm-шим, Popen без расширения даёт WinError 2."""
    candidate = os.path.join(os.environ.get("APPDATA", ""), "npm", "codex.cmd")
    return candidate if os.path.exists(candidate) else "codex.cmd"


def _kill_tree(pid: int) -> None:
    """codex.cmd поднимает дочерний node: terminate() оставит его жить.

    Вывод taskkill не захватываем: в русской консоли он в cp866, и text-декодер
    падает на нём в фоновом потоке — падение не видно, но шумит в stderr.
    """
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except Exception:  # noqa: BLE001 - завершение не должно ронять сбор
        pass


def _run_app_server(messages: list[dict], timeout: float = 60.0) -> list[str]:
    """Запускает app-server, проигрывает handshake и отдаёт строки stdout.

    Таймаут общий: процесс, который не ответил, убивается целиком.
    stderr читается с errors="replace": app-server пишет не-UTF8 (0x93).
    """
    proc = subprocess.Popen(
        [_codex_executable(), "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        # Байтовый режим намеренно: text=True с utf-8 падает на байте 0x93
        # (cmd-шим печатает в OEM-кодировке), причём падает в фоновом reader,
        # а не там, где это видно. Декодируем сами, с заменой.
        stderr=subprocess.DEVNULL,
    )
    lines: list[str] = []

    def read_line() -> str:
        raw = proc.stdout.readline()
        return raw.decode("utf-8", errors="replace") if raw else ""

    def write_message(message: dict) -> None:
        proc.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        proc.stdin.flush()

    try:
        for message in messages:
            write_message(message)
            if message.get("id") == REQUEST_ID_INIT:
                deadline = time.time() + 15.0
                while time.time() < deadline:
                    line = read_line()
                    if not line:
                        break
                    lines.append(line)
                    parsed = _parse_json_line(line)
                    if parsed and parsed.get("id") == REQUEST_ID_INIT:
                        break

        deadline = time.time() + timeout
        while time.time() < deadline:
            line = read_line()
            if not line:
                break
            lines.append(line)
            parsed = _parse_json_line(line)
            if parsed and parsed.get("id") == REQUEST_ID_READ:
                break
    finally:
        _kill_tree(proc.pid)
    return lines


def collect_from_app_server(runner=None) -> dict | None:
    """Возвращает результат `account/rateLimits/read` или None.

    ``runner`` инъектируется: тест отдаёт фикстуру, работа запускает CLI.
    """
    run = runner or _run_app_server
    try:
        stream = run(handshake_messages())
    except Exception as exc:  # noqa: BLE001 - отказ должен быть виден снаружи
        print(f"app-server недоступен: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None

    for line in stream:
        message = _parse_json_line(line)
        if message and message.get("id") == REQUEST_ID_READ:
            result = message.get("result")
            return result if isinstance(result, dict) else None
    return None


STREAM_LABELS = {
    "service_name": "openai-codex",
    "log_type": "subscription",
    "job": "codex-usage",
}

ALLOY_CONFIG = pathlib.Path(r"C:\Program Files\GrafanaLabs\Alloy\config.alloy")


def _loki_credentials() -> tuple[str, tuple[str, str]]:
    """url + basic auth из существующего loki.write блока Alloy.

    Своих секретов экспортёр не заводит: он пользуется тем же контуром,
    что и остальные сборщики.
    """
    text = ALLOY_CONFIG.read_text(encoding="utf-8", errors="ignore")
    block = re.search(r'loki\.write\s+"[^"]+"\s*\{(.*?)\n\}', text, re.DOTALL)
    if not block:
        raise RuntimeError("loki.write block not found in Alloy config")
    body = block.group(1)
    url = re.search(r'url\s*=\s*"([^"]+)"', body)
    user = re.search(r'username\s*=\s*"([^"]+)"', body)
    password = re.search(r'password\s*=\s*"([^"]+)"', body)
    if not (url and user and password):
        raise RuntimeError("loki.write url/username/password not all present")
    return url.group(1), (user.group(1), password.group(1))


def _post_loki(url: str, auth: tuple[str, str], payload: dict) -> int:
    credentials = f"{auth[0]}:{auth[1]}".encode()
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Basic " + base64.b64encode(credentials).decode(),
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status


def publish_lines(lines: list[str], sender=None, url=None, auth=None) -> int:
    """Отправляет строки одним стримом. 0 — принято, 3 — нечего слать, 4 — не ушло."""
    if not lines:
        # Пустая публикация читалась бы как «всё хорошо» при мёртвом источнике.
        return 3

    values = [[str(time.time_ns()), line] for line in lines]
    payload = {"streams": [{"stream": dict(STREAM_LABELS), "values": values}]}

    send = sender or _post_loki
    try:
        if sender is not None:
            send(url or "", auth or ("", ""), payload)
        else:
            resolved_url, resolved_auth = _loki_credentials()
            send(resolved_url, resolved_auth, payload)
    except Exception as exc:  # noqa: BLE001 - код возврата и есть сигнал
        print(f"Loki: публикация не удалась: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4
    return 0


def main(collect, publish) -> int:
    """Собрать снимок, превратить в строки, опубликовать.

    ``collect`` и ``publish`` передаются снаружи: так граница «CLI без сети»
    проверяется в тесте, а живой сбор и запись в Loki остаются заменяемыми.
    Возвращает код: 0 — опубликовано, 2 — нет доступа.
    """
    snapshot = collect()
    if snapshot is None:
        # Нет доступа — явный отказ. Ноль здесь читался бы как «всё свободно».
        return 2

    result = publish(build_lines(snapshot))
    # Если публикатор умеет сообщать код (Loki отказал), он важнее успеха сбора.
    return result if isinstance(result, int) else 0


def cli() -> int:
    """Точка входа: боевые исполнители, без инъекции."""
    return main(
        collect=lambda: collect_from_app_server(),
        publish=lambda lines: publish_lines(lines),
    )


if __name__ == "__main__":
    raise SystemExit(cli())
