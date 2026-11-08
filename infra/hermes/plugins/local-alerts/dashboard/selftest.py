"""Negative controls for the alerts backend. No network, no token, no secrets.

Run: python infra/hermes/plugins/local-alerts/dashboard/selftest.py

The point is not coverage. It is that a calm green light must never come from a
payload this code could not read: an empty answer, an unreadable state and a
missing token all have to land somewhere honest.
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys
import types

HERE = pathlib.Path(__file__).resolve().parent


def _load_module():
    """Import plugin_api.py the way the gateway does, with fastapi stubbed if absent."""
    try:
        import fastapi  # noqa: F401
    except ImportError:
        stub = types.ModuleType("fastapi")

        class APIRouter:  # noqa: D401 — minimal shape only
            def get(self, *_args, **_kwargs):
                return lambda fn: fn

        stub.APIRouter = APIRouter  # type: ignore[attr-defined]
        sys.modules["fastapi"] = stub

    spec = importlib.util.spec_from_file_location("local_alerts_plugin_api", HERE / "plugin_api.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


SAMPLE = {
    "data": {
        "groups": [
            {
                "name": "Theseus",
                "file": "Theseus",
                "rules": [
                    {
                        "state": "firing",
                        "name": "Skill packs: вендор ушёл вперёд",
                        "health": "ok",
                        "lastEvaluation": "2026-11-08T00:41:30Z",
                        "uid": "abc123",
                        "alerts": [
                            {
                                "state": "Alerting",
                                "activeAt": "2026-11-08T00:31:00Z",
                                "labels": {"severity": "warning"},
                                "annotations": {"summary": "langfuse отстал"},
                            }
                        ],
                    },
                    {"state": "inactive", "name": "Honcho dedup: нет успешного прогона 30 ч", "health": "ok", "uid": "def456", "alerts": []},
                    {"state": "inactive", "name": "Hindsight production: recall p95 > 90 с", "health": "nodata", "uid": "ghi789", "alerts": []},
                ],
            }
        ]
    }
}


def main() -> int:
    module = _load_module()
    failures = 0

    def check(label, condition, detail=""):
        nonlocal failures
        ok = bool(condition)
        print(f"  {'ok  ' if ok else 'FAIL'} {label}{'' if ok else f' — {detail}'}")
        failures += 0 if ok else 1

    result = module.normalize(SAMPLE, "https://example.grafana.net")
    counts = result["counts"]
    check("firing counted", counts["firing"] == 1, counts)
    check("normal counted", counts["normal"] == 2, counts)
    check("nodata counted", counts["nodata"] == 1, counts)
    check("firing sorts first", result["rules"][0]["state"] == "firing", result["rules"][0]["state"])
    check("severity from labels", result["rules"][0]["severity"] == "warning", result["rules"][0]["severity"])
    check("summary from annotations", result["rules"][0]["summary"] == "langfuse отстал", result["rules"][0]["summary"])
    check("grafana url built", result["rules"][0]["url"].endswith("/alerting/grafana/abc123/view"), result["rules"][0]["url"])
    check("rule without alerts has empty severity", result["rules"][2]["severity"] == "", result["rules"][2]["severity"])

    empty = module.normalize({}, "https://example")
    check("empty payload yields zeros", all(v == 0 for v in empty["counts"].values()), empty["counts"])
    check("empty payload yields no rules", empty["rules"] == [], empty["rules"])
    check("garbage payload does not raise", module.normalize({"data": None}, "https://example")["rules"] == [])

    # Envelope: an empty stack is a real answer, a broken one is not.
    check("valid empty stack accepted",
          module.envelope_problem({"status": "success", "data": {"groups": []}}) is None)
    check("missing envelope rejected", module.envelope_problem({}) == "bad_envelope")
    check("error envelope rejected",
          module.envelope_problem({"status": "error", "error": "query failed"}) == "bad_envelope")
    check("missing groups rejected",
          module.envelope_problem({"status": "success", "data": {}}) == "bad_envelope")
    check("groups of wrong type rejected",
          module.envelope_problem({"status": "success", "data": {"groups": {}}}) == "bad_envelope")
    check("non-object payload rejected", module.envelope_problem([]) == "bad_envelope")
    check("nodata health surfaces in counts", module.normalize(SAMPLE, "https://e")["counts"]["nodata"] == 1)

    saved = module.os.environ.pop("GRAFANA_ALERTS_TOKEN", None)
    module._cache["at"] = 0.0
    module._cache["payload"] = None
    no_token = asyncio.run(module.collect(force=True))
    check("missing token is reported, not hidden", no_token["status"] == "no_token", no_token["status"])
    check("missing token has no rules", no_token["rules"] == [])
    if saved is not None:
        module.os.environ["GRAFANA_ALERTS_TOKEN"] = saved

    print(f"self-test: {'PASS' if not failures else f'FAIL ({failures})'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
