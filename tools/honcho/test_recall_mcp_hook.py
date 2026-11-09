#!/usr/bin/env python3
"""Deterministic QA for `recall_mcp_hook.py`.

No network, no Docker, no model: a stdlib HTTP server impersonates the Honcho
MCP endpoint, and the hook is exercised as a subprocess over its real stdin /
stdout contract. That keeps every claim below reproducible in CI.

Run: python -m unittest tools/honcho/test_recall_mcp_hook.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOOK = Path(__file__).with_name("recall_mcp_hook.py")


class FakeMCP:
    """Minimal JSON-RPC over HTTP stand-in for the Honcho MCP server."""

    def __init__(self, inner: dict | None = None, broken_inner: bool = False,
                 init_status: int = 200, require_auth: bool = True):
        self.inner = inner
        self.broken_inner = broken_inner
        self.init_status = init_status
        self.require_auth = require_auth
        self.tool_calls: list[dict] = []
        self.auth_headers: list[str] = []
        self.initialize_calls = 0
        self._server = None
        self._thread = None

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "FakeMCP":
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):  # keep test output clean
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                method = body.get("method")

                # Mirror the real gateway: a non-empty bearer is mandatory.
                auth = (self.headers.get("Authorization") or "").strip()
                if outer.require_auth and not auth.lower().startswith("bearer "):
                    self.send_response(401)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                outer.auth_headers.append(auth)

                if method == "initialize":
                    outer.initialize_calls += 1
                    if outer.init_status != 200:
                        self.send_response(outer.init_status)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    payload = {"jsonrpc": "2.0", "id": body.get("id"),
                               "result": {"protocolVersion": "2025-06-18", "capabilities": {}}}
                    outer._send(self, payload, session="test-session")

                elif method == "notifications/initialized":
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()

                elif method == "tools/call":
                    outer.tool_calls.append(body.get("params") or {})
                    text = "not json at all" if outer.broken_inner else json.dumps(
                        outer.inner or {}, ensure_ascii=False)
                    payload = {"jsonrpc": "2.0", "id": body.get("id"),
                               "result": {"content": [{"type": "text", "text": text}]}}
                    outer._send(self, payload)

                else:
                    self.send_response(400)
                    self.send_header("Content-Length", "0")
                    self.end_headers()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    @staticmethod
    def _send(handler, payload: dict, session: str | None = None) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        if session:
            handler.send_header("Mcp-Session-Id", session)
        handler.end_headers()
        handler.wfile.write(data)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/mcp"


class HookTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self._tmp.name, "state.json")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_hook(self, payload: dict, url: str, **env_overrides: str):
        env = {
            **os.environ,
            "HERMES_HONCHO_RECALL_STATE": self.state,
            "HONCHO_RECALL_PEER_ID": "semyon",
            "HONCHO_RECALL_MCP_URL": url,
            "HONCHO_RECALL_TIMEOUT": "5",
            **env_overrides,
        }
        return subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=60,
        )

    @staticmethod
    def payload(message: str, first_turn: bool = True, session: str = "s-1") -> dict:
        return {
            "hook_event_name": "pre_llm_call",
            "session_id": session,
            "extra": {"user_message": message, "is_first_turn": first_turn},
        }

    @staticmethod
    def decode(proc: subprocess.CompletedProcess) -> dict | None:
        if not proc.stdout.strip():
            return None
        envelope = json.loads(proc.stdout)
        assert set(envelope) == {"context"}, envelope
        head, _, body = envelope["context"].partition("\n")
        assert head.startswith("[honcho-recall]"), head
        return json.loads(body)


class TestDelivers(HookTestCase):
    def test_first_turn_delivers_projected_strict_json(self):
        inner = {
            "messages": [{
                "id": "m1", "session_id": "hs", "peer_id": "semyon",
                "created_at": "2026-11-09T00:00:00Z",
                "content": "полный текст",
                "content_chars": 15738, "content_truncated": True,
                "metadata": {"source": "must-not-leak"},
            }],
            "conclusions": [{"id": "c1", "content": "вывод", "created_at": "2026-11-08T00:00:00Z",
                             "level": "explicit"}],
        }
        with FakeMCP(inner=inner) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        block = self.decode(proc)
        self.assertEqual(block["source"], "honcho-mcp:search")
        self.assertEqual(block["peer_id"], "semyon")
        self.assertEqual(len(server.tool_calls), 1)

        params = server.tool_calls[0]
        self.assertEqual(params["name"], "search")
        self.assertEqual(params["arguments"]["peer_id"], "semyon")
        self.assertEqual(params["arguments"]["message_limit"], 3)
        self.assertEqual(params["arguments"]["conclusion_top_k"], 3)

        # projection: no unlisted fields leak through
        self.assertEqual(set(block["messages"][0]),
                         {"id", "session_id", "peer_id", "created_at", "content",
                          "content_chars", "content_truncated"})
        self.assertEqual(set(block["conclusions"][0]), {"id", "content", "created_at"})
        # verbatim text and honest provenance
        self.assertEqual(block["messages"][0]["content"], "полный текст")
        self.assertEqual(block["messages"][0]["content_chars"], 15738)
        self.assertIs(block["messages"][0]["content_truncated"], True)

    def test_sends_bearer_and_per_call_workspace(self):
        inner = {"messages": [{"id": "m1", "content": "x", "content_chars": 1,
                               "content_truncated": False}], "conclusions": []}
        with FakeMCP(inner=inner) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"),
                                 server.url,
                                 HONCHO_RECALL_TOKEN="custom-token",
                                 HONCHO_RECALL_WORKSPACE="hermes")

        self.assertIsNotNone(self.decode(proc))
        self.assertTrue(server.auth_headers, "hook sent no Authorization header")
        for header in server.auth_headers:
            self.assertTrue(header.lower().startswith("bearer "), header)
        self.assertIn("Bearer custom-token", server.auth_headers)
        self.assertEqual(server.tool_calls[0]["arguments"]["workspace_id"], "hermes")

    def test_delivers_every_record_untouched(self):
        # Sizing belongs to the MCP formatter: the hook must not drop or trim
        # records to fit a budget of its own (boundary from local-lab#231).
        long_text = "ж" * 900
        inner = {
            "messages": [{"id": f"m{i}", "content": long_text,
                          "content_chars": 900, "content_truncated": False} for i in range(5)],
            "conclusions": [],
        }
        with FakeMCP(inner=inner) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)

        block = self.decode(proc)
        self.assertEqual(len(block["messages"]), 5)
        for index, message in enumerate(block["messages"]):
            self.assertEqual(message["id"], f"m{index}")
            self.assertEqual(message["content"], long_text)
            self.assertEqual(message["content_chars"], 900)

    def test_content_is_verbatim(self):
        text = 'кавычки "x" и бэкслеш \\ и перевод\nстроки — 😀'
        inner = {"messages": [{"id": "m1", "content": text, "content_chars": len(text),
                               "content_truncated": False}], "conclusions": []}
        with FakeMCP(inner=inner) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)

        block = self.decode(proc)
        self.assertEqual(block["messages"][0]["content"], text)
        # The payload must survive a cp866/cp1251 console: ASCII-only stdout.
        self.assertTrue(proc.stdout.isascii(), "stdout is not ASCII-escaped JSON")

    def test_stdout_survives_a_legacy_console_encoding(self):
        text = "кириллица и 😀"
        inner = {"messages": [{"id": "m1", "content": text, "content_chars": len(text),
                               "content_truncated": False}], "conclusions": []}
        with FakeMCP(inner=inner) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"),
                                 server.url, PYTHONIOENCODING="cp1251")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        block = self.decode(proc)
        self.assertEqual(block["messages"][0]["content"], text)


class TestStaysSilent(HookTestCase):
    def test_second_identical_turn_is_throttled(self):
        with FakeMCP(inner={"messages": [{"id": "m1", "content": "x", "content_chars": 1,
                                          "content_truncated": False}], "conclusions": []}) as server:
            first = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)
            second = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)

        self.assertIsNotNone(self.decode(first))
        self.assertIsNone(self.decode(second))
        self.assertEqual(len(server.tool_calls), 1)

    def test_continuation_turn_is_not_searched(self):
        with FakeMCP(inner={"messages": [{"id": "m1", "content": "x"}], "conclusions": []}) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку", first_turn=False),
                                 server.url)
        self.assertIsNone(self.decode(proc))
        self.assertEqual(len(server.tool_calls), 0)
        self.assertEqual(server.initialize_calls, 0)

    def test_short_prompt_is_not_searched(self):
        with FakeMCP(inner={"messages": [{"id": "m1", "content": "x"}], "conclusions": []}) as server:
            proc = self.run_hook(self.payload("привет"), server.url)
        self.assertIsNone(self.decode(proc))
        self.assertEqual(server.initialize_calls, 0)

    def test_missing_peer_id_is_not_searched(self):
        with FakeMCP(inner={"messages": [{"id": "m1", "content": "x"}], "conclusions": []}) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"),
                                 server.url, HONCHO_RECALL_PEER_ID="")
        self.assertIsNone(self.decode(proc))
        self.assertEqual(server.initialize_calls, 0)

    def test_empty_store_produces_no_context(self):
        with FakeMCP(inner={"messages": [], "conclusions": []}) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)
        self.assertIsNone(self.decode(proc))
        self.assertEqual(proc.returncode, 0)

    def test_broken_tool_payload_fails_open(self):
        with FakeMCP(broken_inner=True) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)
        self.assertIsNone(self.decode(proc))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_unreachable_server_fails_open(self):
        proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"),
                             "http://127.0.0.1:1/mcp")
        self.assertIsNone(self.decode(proc))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_initialize_failure_fails_open(self):
        with FakeMCP(inner={"messages": []}, init_status=500) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)
        self.assertIsNone(self.decode(proc))
        self.assertEqual(proc.returncode, 0)

    def test_other_hook_event_is_ignored(self):
        payload = self.payload("расскажи про динамическую шпаргалку")
        payload["hook_event_name"] = "post_llm_call"
        with FakeMCP(inner={"messages": [{"id": "m1", "content": "x"}]}) as server:
            proc = self.run_hook(payload, server.url)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertEqual(server.initialize_calls, 0)

    def test_garbage_stdin_exits_clean(self):
        proc = subprocess.run([sys.executable, str(HOOK)], input="{not json", capture_output=True,
                              text=True, encoding="utf-8",
                              env={**os.environ, "HERMES_HONCHO_RECALL_STATE": self.state},
                              timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
