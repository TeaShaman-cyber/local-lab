"""Сборщик остатка лимитов подписки Codex (issue #28).

Слайс 1: снимок `account/rateLimits/read` → строки метрик для Loki.
"""

from __future__ import annotations

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

    publish(build_lines(snapshot))
    return 0
