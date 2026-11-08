"""Alert state for the Hermes status bar, read server-side.

Why a backend and not a fetch in plugin.js: Grafana sends no CORS headers, so a
renderer request is blocked before it starts, and a read token must not live in
a file the renderer executes. This router runs inside the gateway, where neither
applies.

Export `router` on purpose — the dashboard mount imports this module and mounts
its routes under /api/plugins/local-alerts/.

Environment:
    GRAFANA_URL           stack base URL (default: the lab stack)
    GRAFANA_ALERTS_TOKEN  read-only service-account token, Viewer role

With no token the route still answers 200 with status="no_token", so the UI can
say "нет доступа" instead of showing a permanent error or, worse, a green light
that means nothing.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from fastapi import APIRouter

router = APIRouter()

DEFAULT_URL = "https://tealaphid71.grafana.net"
RULES_PATH = "/api/prometheus/grafana/api/v1/rules?limit_alerts=0"
TIMEOUT_SECONDS = 10
# Every open window polls the same route; one upstream read per window would
# multiply the same request. Short cache, not a stale-data shortcut.
CACHE_TTL_SECONDS = 20

_cache: dict[str, Any] = {"at": 0.0, "payload": None}


def base_url() -> str:
    return (os.environ.get("GRAFANA_URL") or DEFAULT_URL).rstrip("/")


def token() -> str | None:
    return os.environ.get("GRAFANA_ALERTS_TOKEN") or None


def _get_json(url: str, bearer: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {bearer}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _reason_for(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return "unauthorized"
        return f"http_{exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return "unreachable"
    if isinstance(exc, TimeoutError):
        return "timeout"
    return "unexpected"


def envelope_problem(payload: Any) -> str | None:
    """Reason the answer cannot be trusted as a ruleset, or None.

    An empty `data.groups` list is a real answer: a stack with no rules. A
    missing envelope is not — Grafana also answers 200 with {"status":"error"}
    or a truncated body, and reading those as "no rules" would paint a calm
    green dot over a stack that never answered.
    """
    if not isinstance(payload, dict):
        return "bad_envelope"
    if str(payload.get("status") or "").lower() != "success":
        return "bad_envelope"
    data = payload.get("data")
    if not isinstance(data, dict) or "groups" not in data or not isinstance(data.get("groups"), list):
        return "bad_envelope"
    return None


def normalize(payload: Any, grafana: str) -> dict[str, Any]:
    """Flatten Grafana's grouped rules into one list plus counts.

    Grafana answers /api/prometheus/grafana/api/v1/rules with
    {"data": {"groups": [{"name": <folder>, "rules": [...]}]}}. A rule carries
    state (firing|pending|inactive), health, lastEvaluation and its own alerts[]
    with labels/annotations. `inactive` is what "normal" looks like here.
    """
    counts = {"firing": 0, "pending": 0, "normal": 0, "nodata": 0}
    rules: list[dict[str, Any]] = []

    groups = ((payload or {}).get("data") or {}).get("groups") or []
    for group in groups:
        folder = group.get("name") or group.get("file") or ""
        for rule in group.get("rules") or []:
            state = str(rule.get("state") or "").lower()
            health = str(rule.get("health") or "").lower()
            active = rule.get("alerts") or []
            first = active[0] if active else {}

            if state == "firing":
                counts["firing"] += 1
            elif state == "pending":
                counts["pending"] += 1
            else:
                counts["normal"] += 1
            if health in ("nodata", "error"):
                counts["nodata"] += 1

            labels = first.get("labels") or {}
            annotations = first.get("annotations") or {}
            uid = rule.get("uid") or ""
            rules.append(
                {
                    "name": rule.get("name") or uid or "?",
                    "folder": folder,
                    "state": state or "unknown",
                    "health": health or "unknown",
                    "severity": labels.get("severity") or "",
                    "summary": annotations.get("summary") or "",
                    "description": annotations.get("description") or "",
                    "active_at": first.get("activeAt") or "",
                    "last_evaluated": rule.get("lastEvaluation") or "",
                    "uid": uid,
                    "url": f"{grafana}/alerting/grafana/{uid}/view" if uid else "",
                }
            )

    rules.sort(key=lambda r: (0 if r["state"] == "firing" else 1 if r["state"] == "pending" else 2, r["name"]))
    return {"counts": counts, "rules": rules}


async def collect(force: bool = False) -> dict[str, Any]:
    now = time.time()
    cached = _cache.get("payload")
    if not force and cached and now - float(_cache.get("at") or 0) < CACHE_TTL_SECONDS:
        return cached

    grafana = base_url()
    bearer = token()
    if not bearer:
        payload = {"status": "no_token", "reason": "no_token", "grafana": grafana,
                   "fetched_at": None, "counts": {"firing": 0, "pending": 0, "normal": 0, "nodata": 0},
                   "rules": []}
    else:
        try:
            raw = await asyncio.to_thread(_get_json, f"{grafana}{RULES_PATH}", bearer)
            problem = envelope_problem(raw)
            if problem:
                payload = {"status": "error", "reason": problem, "grafana": grafana,
                           "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "counts": {"firing": 0, "pending": 0, "normal": 0, "nodata": 0},
                           "rules": []}
            else:
                payload = {"status": "ok", "reason": None, "grafana": grafana,
                           "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **normalize(raw, grafana)}
        except Exception as exc:  # noqa: BLE001 — the reason is the payload, not a crash
            payload = {"status": "error", "reason": _reason_for(exc), "grafana": grafana,
                       "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       "counts": {"firing": 0, "pending": 0, "normal": 0, "nodata": 0},
                       "rules": []}

    _cache["at"] = now
    _cache["payload"] = payload
    return payload


@router.get("/alerts")
async def alerts(refresh: bool = False) -> dict[str, Any]:
    return await collect(force=refresh)


@router.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "token": bool(token()), "grafana": base_url()}
