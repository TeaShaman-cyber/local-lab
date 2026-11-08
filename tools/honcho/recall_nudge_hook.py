#!/usr/bin/env python3
"""pre_llm_call shell hook: remind the agent to file a recall defect.

Why this exists: recall arrives auto-injected (the Honcho provider's
`system_prompt_block`), and nothing in that path notices when a recalled claim
contradicts the runtime. Hooks cannot judge meaning, but they can see WHEN
recall landed, and that is the only moment a defect can be caught.

Mechanism (docs/user-guide/features/hooks, `pre_llm_call`):
  - fires once per turn, payload on stdin carries `extra.conversation_history`;
  - any stdout JSON `{"context": "..."}` is joined into the user message;
  - the hook must be cheap and must never break the turn.

Throttle: the reminder is emitted once per recall refresh, not once per turn.
State is a per-session monotonically growing "recall seen at message index"
marker; a recall block already reminded about produces no output.

Any error path prints nothing and exits 0 on purpose: a broken reminder must
not cost the user a turn.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

RECALL_MARKERS = ("<memory-context>", "## Session Summary", "## User Representation")
REMINDER = (
    "Память приехала выше. Если что-то в ней противоречит текущему рантайму — "
    "проверь фактом и запиши дефект: "
    "`python tools/honcho/recall_defect.py \"<что сказала память>\" \"<что в реальности>\"`."
)
STATE_PATH = os.path.join(tempfile.gettempdir(), "hermes-recall-nudge-state.json")
DEBUG_PATH = os.path.join(tempfile.gettempdir(), "hermes-recall-nudge-payload.json")


def _debug_dump(payload: dict) -> None:
    """Opt-in payload capture: HERMES_RECALL_HOOK_DEBUG=1, so the real wire shape can
    be inspected instead of guessed. Best-effort; never affects the hook decision."""
    if os.environ.get("HERMES_RECALL_HOOK_DEBUG") not in ("1", "true", "yes"):
        return
    try:
        with open(DEBUG_PATH, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, default=str)
    except Exception:
        pass


def _load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, STATE_PATH)
    except Exception:
        pass


def _text_of(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # multimodal / block content
        return " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def _recall_text(payload: dict) -> str:
    """The newest text carrying a recall block, or "" if there is none.

    Both sources are checked because recall can land in two places: the provider's
    auto-injected block rides the *current* user message (`user_message`), while an
    explicitly fetched or replayed block is in `conversation_history`. Looking only
    at history was the first version's bug — it stayed silent in a live session.
    """
    extra = payload.get("extra") or {}
    candidates: list[str] = []
    user_message = extra.get("user_message")
    if isinstance(user_message, str) and user_message.strip():
        candidates.append(user_message)
    history = extra.get("conversation_history") or []
    if isinstance(history, list):
        candidates.extend(_text_of(m) for m in history if isinstance(m, dict))

    for text in reversed(candidates):
        if any(marker in text for marker in RECALL_MARKERS) and REMINDER not in text:
            return text
    return ""


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    payload = json.loads(raw)
    if payload.get("hook_event_name") not in (None, "pre_llm_call"):
        return 0
    _debug_dump(payload)

    recall = _recall_text(payload)
    if not recall:
        return 0

    session = payload.get("session_id") or "unknown"
    fingerprint = hashlib.sha1(recall.encode("utf-8", "replace")).hexdigest()
    state = _load_state()
    if state.get(session) == fingerprint:
        return 0  # this exact recall block was already reminded about

    state[session] = fingerprint
    _save_state(state)
    print(json.dumps({"context": REMINDER}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        raise SystemExit(0)
