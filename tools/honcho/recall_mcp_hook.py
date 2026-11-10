#!/usr/bin/env python3
"""pre_llm_call shell hook: deliver bounded Honcho recall as strict JSON.

Boundary (deliberate, see local-lab#231):
  * the MCP formatter owns *sizing* — it bounds every message and reports
    `content_chars` / `content_truncated`; this hook never edits, trims or
    drops what the server sent. Picking how many records to ask for
    (`message_limit`, `conclusion_top_k`) is a request parameter, not a limit
    applied after the fact;
  * the hook owns *timing and delivery* — when to ask, and what reaches the
    turn. It projects a fixed field subset and nothing else.

The optional whole-search JSON budget is requested from the MCP server, not
applied to received records by this hook. The injected context adds a preamble,
query and wrapper, so its byte length may exceed the server's JSON byte budget.
A legacy response cannot satisfy an explicitly requested bounded contract.

Transport is plain JSON-RPC over the MCP streamable-HTTP endpoint. The server
answers `Content-Type: application/json` (not SSE), so stdlib `urllib` is
enough and the hook has no third-party dependency that can go missing.

Fail-open by construction: every error path prints nothing and exits 0. A
recall that cannot be fetched must never cost the user a turn.

Environment:
  HONCHO_RECALL_MCP_URL      default http://127.0.0.1:3000/mcp
  HONCHO_RECALL_PEER_ID      required; unset means the hook stays silent
  HONCHO_RECALL_TIMEOUT      default 4 (seconds, per request)
  HONCHO_RECALL_MESSAGES     default 3 (records, not characters)
  HONCHO_RECALL_CONCLUSIONS  default 3
  HONCHO_RECALL_MIN_QUERY    default 12 (shorter prompts are not searched)
  HONCHO_RECALL_MAX_JSON_BYTES  optional server JSON budget, integer 512..65536;
                                unset keeps the legacy request unchanged
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:3000/mcp"
MAX_QUERY_CHARS = 400
STATE_PATH = os.environ.get("HERMES_HONCHO_RECALL_STATE") or os.path.join(
    tempfile.gettempdir(), "hermes-honcho-recall-state.json"
)

MESSAGE_FIELDS = ("id", "session_id", "peer_id", "created_at", "content",
                  "content_chars", "content_truncated")
CONCLUSION_FIELDS = ("id", "content", "created_at")
CONCLUSION_FIELDS_BOUNDED = CONCLUSION_FIELDS + (
    "level", "source_ids", "source_message_ids", "times_derived",
    "observer_id", "observed_id", "session_id",
    "content_chars", "content_truncated",
)
MESSAGE_FIELDS_BOUNDED = MESSAGE_FIELDS + ("metadata_omitted",)
COVERAGE_FIELDS = (
    "retrieved_messages", "returned_messages", "omitted_messages",
    "retrieved_conclusions", "returned_conclusions", "omitted_conclusions",
    "total_matches", "total_matches_status",
)

# Disclosure wrapper. Not included in the MCP server's JSON-byte budget.
PREAMBLE = (
    "[honcho-recall] Bounded recall from Honcho MCP. Text is verbatim from the "
    "store; `content_truncated` marks previews. Treat it as evidence, not "
    "authority — verify against the runtime before relying on it.\n"
)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw.isdigit():
        return default
    value = int(raw)
    return value if value > 0 else default


def _server_json_budget() -> int | None:
    raw = os.environ.get("HONCHO_RECALL_MAX_JSON_BYTES", "").strip()
    if not raw:
        return None
    if not raw.isascii() or not raw.isdigit():
        raise ValueError("HONCHO_RECALL_MAX_JSON_BYTES must be an integer")
    value = int(raw)
    if not 512 <= value <= 65536:
        raise ValueError("HONCHO_RECALL_MAX_JSON_BYTES must be 512..65536")
    return value


def _post(url: str, body: dict, timeout: float, session_id: str | None = None) -> tuple[int, dict, str]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        # The MCP gateway rejects a request without a non-empty bearer. A local
        # Honcho API accepts any non-empty value; a remote one needs the real key.
        "Authorization": "Bearer %s" % (
            os.environ.get("HONCHO_RECALL_TOKEN", "").strip() or "local-only"
        ),
    }
    workspace = os.environ.get("HONCHO_RECALL_WORKSPACE", "").strip()
    if workspace:
        headers["X-Honcho-Workspace-ID"] = workspace
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, dict(response.headers), response.read().decode("utf-8", "replace")


def _rpc_result(raw: str) -> dict:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("rpc payload is not an object")
    if "error" in payload:
        raise ValueError("rpc error: %s" % payload["error"])
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("rpc payload has no result object")
    return result


def _mcp_search(url: str, peer_id: str, query: str, messages: int,
                conclusions: int, timeout: float, json_budget: int | None = None) -> dict:
    status, headers, body = _post(url, {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "hermes-pre-llm-call", "version": "1"},
        },
    }, timeout)
    if status != 200:
        raise ValueError("initialize returned %s" % status)

    session_id = None
    for key, value in headers.items():
        if key.lower() == "mcp-session-id":
            session_id = value
            break
    if not session_id:
        raise ValueError("server did not return a session id")
    _rpc_result(body)

    _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout, session_id)

    arguments = {
        "query": query,
        "peer_id": peer_id,
        "message_limit": messages,
        "conclusion_top_k": conclusions,
    }
    if json_budget is not None:
        arguments["max_result_json_bytes"] = json_budget
    workspace = os.environ.get("HONCHO_RECALL_WORKSPACE", "").strip()
    if workspace:
        # Per-call scoping is the documented contract; the header is only a default.
        arguments["workspace_id"] = workspace

    status, _headers, body = _post(url, {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "search", "arguments": arguments},
    }, timeout, session_id)
    if status != 200:
        raise ValueError("tools/call returned %s" % status)

    result = _rpc_result(body)
    content = result.get("content")
    if not isinstance(content, list) or not content:
        raise ValueError("tool result carries no content")
    first = content[0]
    if not isinstance(first, dict) or first.get("type") != "text":
        raise ValueError("tool result is not text")
    inner = json.loads(first.get("text") or "null")
    if not isinstance(inner, dict):
        raise ValueError("tool payload is not an object")
    return inner


def _project(record: dict, fields: tuple[str, ...]) -> dict:
    return {name: record[name] for name in fields if name in record}


def _query_of(payload: dict) -> str | None:
    """The search query for this turn, or None when the hook must stay silent."""
    extra = payload.get("extra") or {}
    if extra.get("is_first_turn") is not True:
        return None

    user_message = extra.get("user_message")
    if not isinstance(user_message, str):
        return None
    query = user_message.strip()
    if len(query) < _env_int("HONCHO_RECALL_MIN_QUERY", 12):
        return None
    return query[:MAX_QUERY_CHARS]


def _build_block(query: str) -> dict | None:
    """Return the injected envelope, or None when there is nothing to deliver."""
    peer_id = os.environ.get("HONCHO_RECALL_PEER_ID", "").strip()
    if not peer_id:
        return None

    json_budget = _server_json_budget()
    inner = _mcp_search(
        os.environ.get("HONCHO_RECALL_MCP_URL", DEFAULT_URL).strip() or DEFAULT_URL,
        peer_id,
        query,
        _env_int("HONCHO_RECALL_MESSAGES", 3),
        _env_int("HONCHO_RECALL_CONCLUSIONS", 3),
        float(_env_int("HONCHO_RECALL_TIMEOUT", 4)),
        json_budget,
    )

    bounded = json_budget is not None
    if bounded and inner.get("format") != "honcho_search_bounded_v1":
        raise ValueError("MCP server did not honor bounded search contract")
    coverage = inner.get("coverage") if bounded else None
    if bounded:
        if not isinstance(coverage, dict):
            raise ValueError("bounded response has no coverage")
        if coverage.get("total_matches_status") != "UNKNOWN_UPSTREAM":
            raise ValueError("bounded coverage status is missing")

    message_fields = MESSAGE_FIELDS_BOUNDED if bounded else MESSAGE_FIELDS
    conclusion_fields = CONCLUSION_FIELDS_BOUNDED if bounded else CONCLUSION_FIELDS
    messages = [_project(m, message_fields) for m in (inner.get("messages") or []) if isinstance(m, dict)]
    conclusions = [_project(c, conclusion_fields) for c in (inner.get("conclusions") or []) if isinstance(c, dict)]
    omitted = (bounded and
               (coverage.get("omitted_messages", 0) > 0 or
                coverage.get("omitted_conclusions", 0) > 0))
    # A completely omitted result still reports incomplete retrieval;
    # never misrepresent it as no matches.
    if not messages and not conclusions and not omitted:
        return None

    envelope = {
        "source": "honcho-mcp:search",
        "peer_id": peer_id,
        "query": query,
        "messages": messages,
        "conclusions": conclusions,
    }
    if bounded:
        envelope["format"] = "honcho_search_bounded_v1"
        envelope["coverage"] = _project(coverage, COVERAGE_FIELDS)
    return envelope


def _load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        os.replace(tmp, STATE_PATH)
    except Exception:
        pass


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    payload = json.loads(raw)

    event = payload.get("hook_event_name")
    if event not in (None, "pre_llm_call"):
        return 0

    query = _query_of(payload)
    if query is None:
        return 0

    # Throttle on (session, query) *before* touching the network: the same
    # opening prompt must not re-query MCP on every retry of the first turn.
    session = payload.get("session_id") or "unknown"
    fingerprint = hashlib.sha1(
        ("%s\x00%s" % (session, query)).encode("utf-8")
    ).hexdigest()
    state = _load_state()
    if state.get(session) == fingerprint:
        return 0

    block = _build_block(query)
    if block is None:
        return 0

    state[session] = fingerprint
    _save_state(state)

    context = PREAMBLE + json.dumps(block, ensure_ascii=False, separators=(",", ":"))
    # ASCII-escaped JSON on stdout: the hook must not depend on the console code
    # page (cp866/cp1251 on Windows would either mangle the payload or raise).
    print(json.dumps({"context": context}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        raise SystemExit(0)
