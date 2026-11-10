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

# Stateless MCP revision the hook must speak. Kept literal on purpose: a
# black-box test that imported the hook's constants would not notice the
# hook changing them.
PROTOCOL_VERSION = "2026-07-28"
META_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
META_CAPABILITIES_KEY = "io.modelcontextprotocol/clientCapabilities"


class FakeMCP:
    """Stateless MCP `2026-07-28` stand-in for the Honcho MCP server.

    Mirrors the two behaviours a client must get right: the per-request `_meta`
    envelope with its mandatory headers, and a reply that may be framed as
    server-sent events. `legacy_server=True` emulates a session-based server
    that speaks none of it, so "no silent legacy fallback" stays testable.
    """

    def __init__(self, inner: dict | None = None, broken_inner: bool = False,
                 fail_status: int = 200, require_auth: bool = True,
                 sse: bool = True, legacy_server: bool = False,
                 notify_first: bool = False, crlf: bool = False,
                 comment_first: bool = False, broken_frame_first: bool = False,
                 no_space_after_data: bool = False,
                 content_items: list | None = None):
        self.inner = inner
        self.broken_inner = broken_inner
        self.fail_status = fail_status
        self.require_auth = require_auth
        self.sse = sse
        self.legacy_server = legacy_server
        self.notify_first = notify_first
        self.crlf = crlf
        self.comment_first = comment_first
        self.broken_frame_first = broken_frame_first
        self.no_space_after_data = no_space_after_data
        # Overrides the whole `result.content` list, so non-text and multi-item
        # tool results are expressible.
        self.content_items = content_items
        self.tool_calls: list[dict] = []
        self.auth_headers: list[str] = []
        self.workspace_headers: list[str | None] = []
        self.protocol_headers: list[str | None] = []
        self.method_headers: list[str | None] = []
        self.name_headers: list[str | None] = []
        self.envelopes: list[dict | None] = []
        self.session_headers: list[str | None] = []
        self.requests = 0
        self._server = None
        self._thread = None

    def request_count(self) -> int:
        return self.requests

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "FakeMCP":
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):  # keep test output clean
                pass

            def _empty(self, status: int) -> None:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                outer.requests += 1
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")

                # Mirror the real gateway: a non-empty bearer is mandatory.
                auth = (self.headers.get("Authorization") or "").strip()
                if outer.require_auth and not auth.lower().startswith("bearer "):
                    self._empty(401)
                    return
                outer.auth_headers.append(auth)
                outer.workspace_headers.append(
                    self.headers.get("X-Honcho-Workspace-ID")
                )

                if outer.legacy_server:
                    # A session-based server has nothing to say to a stateless call.
                    self._empty(400)
                    return

                method = body.get("method")
                params = body.get("params") or {}
                meta = params.get("_meta")
                outer.protocol_headers.append(self.headers.get("MCP-Protocol-Version"))
                outer.method_headers.append(self.headers.get("Mcp-Method"))
                outer.name_headers.append(self.headers.get("Mcp-Name"))
                outer.session_headers.append(self.headers.get("Mcp-Session-Id"))
                outer.envelopes.append(meta)

                # The real server rejects a header/body disagreement instead of
                # guessing which request the caller meant; so does this stub.
                problem = None
                if self.headers.get("MCP-Protocol-Version") != PROTOCOL_VERSION:
                    problem = "protocol version header"
                elif self.headers.get("Mcp-Method") != method:
                    problem = "Mcp-Method header"
                elif not isinstance(meta, dict):
                    problem = "envelope _meta"
                elif meta.get(META_VERSION_KEY) != PROTOCOL_VERSION:
                    problem = "envelope protocolVersion"
                elif META_CAPABILITIES_KEY not in meta:
                    problem = "envelope clientCapabilities"
                elif method == "tools/call" and self.headers.get("Mcp-Name") != params.get("name"):
                    problem = "Mcp-Name header"
                if problem:
                    self._empty(400)
                    return

                if method != "tools/call":
                    self._empty(400)
                    return

                outer.tool_calls.append(params)
                if outer.fail_status != 200:
                    self._empty(outer.fail_status)
                    return
                text = "not json at all" if outer.broken_inner else json.dumps(
                    outer.inner or {}, ensure_ascii=False)
                content = outer.content_items
                if content is None:
                    content = [{"type": "text", "text": text}]
                payload = {"jsonrpc": "2.0", "id": body.get("id"),
                           "result": {"content": content}}
                outer._send(self, payload)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _send(self, handler, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if self.sse:
            # A stream may legally interleave notifications before the response.
            separator = b"\r\n" if self.crlf else b"\n"
            frames = []
            if self.comment_first:
                # Comment frames ARE the keep-alive mechanism; they carry no
                # `data:` line at all and must be skipped, not treated as the end.
                frames.append(b": keep-alive" + separator + separator)
            if self.broken_frame_first:
                frames.append(b"data: {not json" + separator + separator)
            if self.notify_first:
                note = json.dumps({"jsonrpc": "2.0", "method": "notifications/message",
                                   "params": {"level": "info"}}).encode("utf-8")
                frames.append(b"event: message" + separator + b"data: " + note + separator + separator)
            prefix = b"data:" if self.no_space_after_data else b"data: "
            frames.append(b"event: message" + separator + prefix + data + separator + separator)
            framed = b"".join(frames)
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Content-Length", str(len(framed)))
            handler.end_headers()
            handler.wfile.write(framed)
            return
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/mcp"


def _bounded_inner(*, format_marker: str = "honcho_search_bounded_v1",
                   messages: list | None = None, conclusions: list | None = None,
                   total_matches_status: str = "UNKNOWN_UPSTREAM", **coverage) -> dict:
    """A bounded search reply, with the parts each test cares about overridable."""
    base = {
        "retrieved_messages": 3, "returned_messages": 0, "omitted_messages": 0,
        "retrieved_conclusions": 4, "returned_conclusions": 0, "omitted_conclusions": 0,
        "total_matches": None, "total_matches_status": total_matches_status,
    }
    base.update(coverage)
    return {
        "format": format_marker,
        "messages": [] if messages is None else messages,
        "conclusions": [] if conclusions is None else conclusions,
        "coverage": base,
    }


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
        self.assertNotIn("max_result_json_bytes", params["arguments"])

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
        self.assertTrue(server.workspace_headers)
        self.assertEqual(set(server.workspace_headers), {"hermes"})
        self.assertEqual(server.tool_calls[0]["arguments"]["workspace_id"], "hermes")

    def test_query_is_truncated_at_the_documented_ceiling(self):
        # MAX_QUERY_CHARS is a request-shaping contract: an off-by-one either
        # leaks payload past the ceiling or silently drops a character.
        long_message = "я" * 500
        inner = {"messages": [{"id": "m1", "content": "x", "content_chars": 1,
                               "content_truncated": False}], "conclusions": []}
        with FakeMCP(inner=inner) as server:
            self.run_hook(self.payload(long_message), server.url)

        sent = server.tool_calls[0]["arguments"]["query"]
        self.assertEqual(len(sent), 400, f"query length {len(sent)} is not the ceiling")
        self.assertEqual(sent, long_message[:400])

    def test_unbounded_projection_drops_bounded_only_fields(self):
        # The two field projections are not interchangeable: the bounded one
        # carries metadata_omitted and the extra conclusion provenance. Swapping
        # them would silently change what the model is shown.
        inner = {"messages": [{"id": "m1", "content": "x", "metadata_omitted": True}],
                 "conclusions": [{"id": "c1", "content": "y", "source_ids": ["c0"],
                                  "level": "deductive"}]}
        with FakeMCP(inner=inner) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)

        block = self.decode(proc)
        self.assertIsNotNone(block)
        self.assertNotIn("metadata_omitted", block["messages"][0])
        self.assertNotIn("source_ids", block["conclusions"][0])
        self.assertNotIn("level", block["conclusions"][0])

    def test_omits_empty_workspace_header(self):
        inner = {"messages": [], "conclusions": []}
        with FakeMCP(inner=inner) as server:
            proc = self.run_hook(
                self.payload("расскажи про динамическую шпаргалку"),
                server.url,
                HONCHO_RECALL_WORKSPACE="",
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(server.workspace_headers)
        self.assertEqual(set(server.workspace_headers), {None})
        self.assertNotIn("workspace_id", server.tool_calls[0]["arguments"])

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

    def test_state_write_failure_does_not_drop_recall(self):
        state_directory = os.path.join(self._tmp.name, "state-directory")
        os.mkdir(state_directory)
        inner = {"messages": [{"id": "m1", "content": "x", "content_chars": 1,
                               "content_truncated": False}], "conclusions": []}
        with FakeMCP(inner=inner) as server:
            proc = self.run_hook(
                self.payload("расскажи про динамическую шпаргалку"),
                server.url,
                HERMES_HONCHO_RECALL_STATE=state_directory,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsNotNone(self.decode(proc))


class TestBoundedServerContract(HookTestCase):
    def test_opt_in_budget_preserves_source_provenance_and_coverage(self):
        inner = {
            "format": "honcho_search_bounded_v1",
            "messages": [{"id": "m1", "content": "evidence", "content_chars": 8,
                          "content_truncated": False}],
            "conclusions": [{
                "id": "c1", "content": "deduction", "level": "deductive",
                "source_ids": ["c0"], "source_message_ids": None,
                "times_derived": 2, "observer_id": "agent", "observed_id": "semyon",
                "session_id": "s1", "content_chars": 9, "content_truncated": False,
            }],
            "coverage": {
                "retrieved_messages": 3, "returned_messages": 1, "omitted_messages": 2,
                "retrieved_conclusions": 4, "returned_conclusions": 1, "omitted_conclusions": 3,
                "total_matches": None, "total_matches_status": "UNKNOWN_UPSTREAM",
            },
        }
        with FakeMCP(inner=inner) as server:
            proc = self.run_hook(
                self.payload("расскажи про динамическую шпаргалку"),
                server.url, HONCHO_RECALL_MAX_JSON_BYTES="2048",
            )
        block = self.decode(proc)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(server.tool_calls[0]["arguments"]["max_result_json_bytes"], 2048)
        self.assertEqual(block["format"], "honcho_search_bounded_v1")
        self.assertEqual(block["coverage"], inner["coverage"])
        self.assertEqual(block["conclusions"][0]["source_ids"], ["c0"])
        self.assertIsNone(block["conclusions"][0]["source_message_ids"])
        self.assertEqual(block["conclusions"][0]["times_derived"], 2)
        self.assertEqual(block["conclusions"][0]["observer_id"], "agent")
        self.assertEqual(block["conclusions"][0]["level"], "deductive")

    def test_single_omitted_record_is_still_reported(self):
        # Nothing came back, but the server says one record was dropped. The
        # hook must report incomplete retrieval instead of going silent, which
        # would read as "no matches".
        inner = _bounded_inner(omitted_messages=1, retrieved_messages=1)
        with FakeMCP(inner=inner) as server:
            proc = self.run_hook(
                self.payload("расскажи про динамическую шпаргалку"),
                server.url, HONCHO_RECALL_MAX_JSON_BYTES="2048",
            )

        block = self.decode(proc)
        self.assertIsNotNone(block, "a single omission was reported as no matches")
        self.assertEqual(block["messages"], [])
        self.assertEqual(block["coverage"]["omitted_messages"], 1)

    def test_zero_omissions_with_nothing_retrieved_stays_silent(self):
        # The mirror case: nothing retrieved and nothing omitted is genuinely
        # no matches, and inventing an empty envelope would be noise.
        inner = _bounded_inner()
        with FakeMCP(inner=inner) as server:
            proc = self.run_hook(
                self.payload("расскажи про динамическую шпаргалку"),
                server.url, HONCHO_RECALL_MAX_JSON_BYTES="2048",
            )

        self.assertIsNone(self.decode(proc), "empty result was dressed up as coverage")
        self.assertEqual(proc.stdout.strip(), "")

    def test_wrong_bounded_format_marker_is_rejected(self):
        # A server that ignored the budget answers in the legacy shape; that
        # must fail open rather than be projected as if it were bounded.
        # Both markers are tried: one sorts before the contract string and one
        # after, so an ordering comparison cannot masquerade as inequality.
        for marker in ("aaa_not_the_contract", "zzz_not_the_contract"):
            with self.subTest(marker=marker):
                inner = _bounded_inner(format_marker=marker,
                                       messages=[{"id": "m1", "content": "x"}])
                with FakeMCP(inner=inner) as server:
                    proc = self.run_hook(
                        self.payload("расскажи про динамическую шпаргалку"),
                        server.url, HONCHO_RECALL_MAX_JSON_BYTES="2048",
                    )

                self.assertIsNone(self.decode(proc), "an unrecognised format was accepted")

    def test_unexpected_total_matches_status_is_rejected(self):
        for status in ("AAA_NOT_THE_CONTRACT", "ZZZ_NOT_THE_CONTRACT"):
            with self.subTest(status=status):
                inner = _bounded_inner(total_matches_status=status,
                                       messages=[{"id": "m1", "content": "x"}])
                with FakeMCP(inner=inner) as server:
                    proc = self.run_hook(
                        self.payload("расскажи про динамическую шпаргалку"),
                        server.url, HONCHO_RECALL_MAX_JSON_BYTES="2048",
                    )

                self.assertIsNone(self.decode(proc), "an unrecognised coverage status was trusted")

    def test_invalid_budget_is_rejected_before_network_not_silently_ignored(self):
        for value in ("511", "65537", "invalid", "0"):
            with self.subTest(value=value), FakeMCP(inner={"messages": []}) as server:
                proc = self.run_hook(
                    self.payload("расскажи про динамическую шпаргалку"),
                    server.url, HONCHO_RECALL_MAX_JSON_BYTES=value,
                )
                self.assertEqual(proc.returncode, 0)
                self.assertIsNone(self.decode(proc))
                self.assertEqual(server.request_count(), 0)

    def test_budget_accepts_the_documented_bounds(self):
        # 512 and 65536 are inclusive boundaries: an off-by-one here silently
        # rejects a value the documented contract allows.
        for value in (512, 65536):
            with self.subTest(value=value), FakeMCP(inner={"messages": []}) as server:
                self.run_hook(
                    self.payload("расскажи про динамическую шпаргалку"),
                    server.url, HONCHO_RECALL_MAX_JSON_BYTES=str(value),
                )
                self.assertEqual(server.request_count(), 1, "boundary value was rejected")
                self.assertEqual(
                    server.tool_calls[0]["arguments"]["max_result_json_bytes"], value)

    def test_budget_requested_but_legacy_server_reply_fails_open(self):
        # Old MCP servers may ignore an unknown tool argument. That must not
        # silently bypass an explicitly requested whole-response budget.
        with FakeMCP(inner={"messages": [{"id": "m1", "content": "x"}]}) as server:
            proc = self.run_hook(
                self.payload("расскажи про динамическую шпаргалку"),
                server.url, HONCHO_RECALL_MAX_JSON_BYTES="2048",
            )
        self.assertEqual(proc.returncode, 0)
        self.assertIsNone(self.decode(proc))
        self.assertEqual(len(server.tool_calls), 1)

    def test_bounded_all_omitted_is_distinct_from_no_matches(self):
        inner = {
            "format": "honcho_search_bounded_v1",
            "messages": [], "conclusions": [],
            "coverage": {
                "retrieved_messages": 2, "returned_messages": 0, "omitted_messages": 2,
                "retrieved_conclusions": 0, "returned_conclusions": 0,
                "omitted_conclusions": 0, "total_matches": None,
                "total_matches_status": "UNKNOWN_UPSTREAM",
            },
        }
        with FakeMCP(inner=inner) as server:
            proc = self.run_hook(
                self.payload("расскажи про динамическую шпаргалку"),
                server.url, HONCHO_RECALL_MAX_JSON_BYTES="512",
            )
        block = self.decode(proc)
        self.assertEqual(block["messages"], [])
        self.assertEqual(block["coverage"]["omitted_messages"], 2)
        self.assertEqual(block["coverage"]["total_matches_status"], "UNKNOWN_UPSTREAM")


class TestModernTransport(HookTestCase):
    """The stateless revision is a contract, not an implementation detail."""

    INNER = {"messages": [{"id": "m1", "content": "x", "content_chars": 1,
                           "content_truncated": False}], "conclusions": []}

    def test_sends_stateless_envelope_and_matching_headers(self):
        with FakeMCP(inner=self.INNER) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)

        self.assertIsNotNone(self.decode(proc))
        # Exactly one request, and it is the tool call: no initialize handshake.
        self.assertEqual(server.method_headers, ["tools/call"])
        self.assertEqual(server.protocol_headers, [PROTOCOL_VERSION])
        self.assertEqual(server.name_headers, ["search"])
        # No session lifecycle at all.
        self.assertEqual(server.session_headers, [None])

        envelope = server.envelopes[0]
        self.assertEqual(envelope[META_VERSION_KEY], PROTOCOL_VERSION)
        self.assertEqual(envelope[META_CAPABILITIES_KEY], {})

    def test_parses_event_stream_reply(self):
        # The modern server answers text/event-stream, so a bare json.loads fails.
        with FakeMCP(inner=self.INNER, sse=True) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        block = self.decode(proc)
        self.assertIsNotNone(block)
        self.assertEqual(block["messages"][0]["id"], "m1")

    def test_parses_plain_json_reply(self):
        with FakeMCP(inner=self.INNER, sse=False) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)

        self.assertIsNotNone(self.decode(proc))

    def test_ignores_notifications_before_the_response(self):
        # Legal per the transport spec: a notification frame may precede the
        # response. Returning the first frame would drop valid recall.
        with FakeMCP(inner=self.INNER, sse=True, notify_first=True) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        block = self.decode(proc)
        self.assertIsNotNone(block, "notification was mistaken for the response")
        self.assertEqual(block["messages"][0]["id"], "m1")

    def test_parses_crlf_framed_event_stream(self):
        # CRLF separators are permitted; splitting on "\n\n" alone merges events.
        with FakeMCP(inner=self.INNER, sse=True, notify_first=True, crlf=True) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        block = self.decode(proc)
        self.assertIsNotNone(block, "CRLF framing was not normalised")
        self.assertEqual(block["messages"][0]["id"], "m1")

    def test_skips_a_frame_that_carries_no_data_line(self):
        # Comment frames (`: keep-alive`) are the normal SSE keep-alive and carry
        # no `data:` line. Treating one as the end of the stream aborts parsing
        # and drops recall silently.
        with FakeMCP(inner=self.INNER, sse=True, comment_first=True) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        block = self.decode(proc)
        self.assertIsNotNone(block, "a data-less frame ended the stream")
        self.assertEqual(block["messages"][0]["id"], "m1")

    def test_skips_a_frame_that_is_not_valid_json(self):
        # A truncated or unparsable frame must be skipped, not raise: the real
        # stream can carry one, and failing there loses the whole recall.
        with FakeMCP(inner=self.INNER, sse=True, broken_frame_first=True) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        block = self.decode(proc)
        self.assertIsNotNone(block, "an unparsable frame aborted the stream")
        self.assertEqual(block["messages"][0]["id"], "m1")

    def test_strips_exactly_the_data_prefix(self):
        # `data:` may arrive without the space. Off-by-one in the slice leaves a
        # stray quote in front of the JSON and the response is lost.
        with FakeMCP(inner=self.INNER, sse=True, no_space_after_data=True) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        block = self.decode(proc)
        self.assertIsNotNone(block, "the data: prefix was not stripped exactly")
        self.assertEqual(block["messages"][0]["id"], "m1")

    def test_session_based_server_gets_no_silent_fallback(self):
        # honcho#10: legacy framing is not supported. An old server must produce
        # silence (fail-open), not a second, session-based attempt.
        with FakeMCP(inner=self.INNER, legacy_server=True) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertEqual(server.request_count(), 1)
        self.assertEqual(server.tool_calls, [])


class TestStaysSilent(HookTestCase):
    def test_second_identical_turn_is_throttled(self):
        with FakeMCP(inner={"messages": [{"id": "m1", "content": "x", "content_chars": 1,
                                          "content_truncated": False}], "conclusions": []}) as server:
            first = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)
            second = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)

        self.assertIsNotNone(self.decode(first))
        self.assertIsNone(self.decode(second))
        self.assertEqual(second.returncode, 0)
        self.assertEqual(len(server.tool_calls), 1)

    def test_same_query_in_different_sessions_is_not_throttled(self):
        query = "расскажи про динамическую шпаргалку"
        inner = {"messages": [{"id": "m1", "content": "x", "content_chars": 1,
                               "content_truncated": False}], "conclusions": []}
        with FakeMCP(inner=inner) as server:
            first = self.run_hook(self.payload(query, session="s-1"), server.url)
            second = self.run_hook(self.payload(query, session="s-2"), server.url)

        self.assertIsNotNone(self.decode(first))
        self.assertIsNotNone(self.decode(second))
        self.assertEqual(len(server.tool_calls), 2)

    def test_continuation_turn_is_not_searched(self):
        with FakeMCP(inner={"messages": [{"id": "m1", "content": "x"}], "conclusions": []}) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку", first_turn=False),
                                 server.url)
        self.assertIsNone(self.decode(proc))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(len(server.tool_calls), 0)
        self.assertEqual(server.request_count(), 0)

    def test_first_turn_requires_json_boolean_true(self):
        payload = self.payload("расскажи про динамическую шпаргалку")
        payload["extra"]["is_first_turn"] = 1
        with FakeMCP(inner={"messages": [{"id": "m1", "content": "x"}]}) as server:
            proc = self.run_hook(payload, server.url)

        self.assertIsNone(self.decode(proc))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(server.request_count(), 0)

    def test_minimum_query_length_boundary(self):
        inner = {"messages": [{"id": "m1", "content": "x", "content_chars": 1,
                               "content_truncated": False}], "conclusions": []}
        with FakeMCP(inner=inner) as server:
            below = self.run_hook(self.payload("x" * 11, session="below"), server.url)
            exact = self.run_hook(self.payload("x" * 12, session="exact"), server.url)

        self.assertIsNone(self.decode(below))
        self.assertIsNotNone(self.decode(exact))
        self.assertEqual(len(server.tool_calls), 1)

    def test_positive_integer_environment_boundary(self):
        inner = {"messages": [{"id": "m1", "content": "x", "content_chars": 1,
                               "content_truncated": False}], "conclusions": []}
        with FakeMCP(inner=inner) as server:
            zero = self.run_hook(self.payload("x" * 11, session="zero"), server.url,
                                 HONCHO_RECALL_MIN_QUERY="0")
            one = self.run_hook(self.payload("x", session="one"), server.url,
                                HONCHO_RECALL_MIN_QUERY="1")
            five = self.run_hook(self.payload("x" * 5, session="five"), server.url,
                                 HONCHO_RECALL_MIN_QUERY="5")

        self.assertIsNone(self.decode(zero))
        self.assertIsNotNone(self.decode(one))
        self.assertIsNotNone(self.decode(five))
        self.assertEqual(len(server.tool_calls), 2)

    def test_short_prompt_is_not_searched(self):
        with FakeMCP(inner={"messages": [{"id": "m1", "content": "x"}], "conclusions": []}) as server:
            proc = self.run_hook(self.payload("привет"), server.url)
        self.assertIsNone(self.decode(proc))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(server.request_count(), 0)

    def test_missing_peer_id_is_not_searched(self):
        with FakeMCP(inner={"messages": [{"id": "m1", "content": "x"}], "conclusions": []}) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"),
                                 server.url, HONCHO_RECALL_PEER_ID="")
        self.assertIsNone(self.decode(proc))
        self.assertEqual(server.request_count(), 0)

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

    def test_server_error_fails_open(self):
        with FakeMCP(inner={"messages": []}, fail_status=500) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)
        self.assertIsNone(self.decode(proc))
        self.assertEqual(proc.returncode, 0)

    def test_non_text_tool_content_is_not_delivered(self):
        # Only a text content block carries the search payload. A different
        # block type that happens to hold JSON must never be parsed as recall:
        # loosening the guard turns it into fabricated context.
        carried = json.dumps({"messages": [{"id": "img", "content": "from an image block"}],
                              "conclusions": []})
        with FakeMCP(content_items=[{"type": "image", "text": carried}]) as server:
            proc = self.run_hook(self.payload("расскажи про динамическую шпаргалку"), server.url)

        self.assertIsNone(self.decode(proc), "non-text content was delivered as recall")
        self.assertEqual(proc.stdout.strip(), "")

    def test_other_hook_event_is_ignored(self):
        payload = self.payload("расскажи про динамическую шпаргалку")
        payload["hook_event_name"] = "post_llm_call"
        with FakeMCP(inner={"messages": [{"id": "m1", "content": "x"}]}) as server:
            proc = self.run_hook(payload, server.url)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertEqual(server.request_count(), 0)

    def test_empty_stdin_exits_clean(self):
        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input="",
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "HERMES_HONCHO_RECALL_STATE": self.state},
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_garbage_stdin_exits_clean(self):
        proc = subprocess.run([sys.executable, str(HOOK)], input="{not json", capture_output=True,
                              text=True, encoding="utf-8",
                              env={**os.environ, "HERMES_HONCHO_RECALL_STATE": self.state},
                              timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
