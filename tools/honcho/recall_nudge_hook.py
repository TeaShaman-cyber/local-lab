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


def _history(payload: dict) -> list:
    extra = payload.get("extra") or {}
    history = extra.get("conversation_history") or []
    return history if isinstance(history, list) else []


def _text_of(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # multimodal / block content
        return " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def _last_recall_index(history: list) -> int:
    """Index of the newest message carrying a recall block, or -1."""
    for idx in range(len(history) - 1, -1, -1):
        text = _text_of(history[idx])
        if any(marker in text for marker in RECALL_MARKERS):
            # Ignore our own reminder text so it never counts as recall.
            if REMINDER not in text:
                return idx
    return -1


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    payload = json.loads(raw)
    if payload.get("hook_event_name") not in (None, "pre_llm_call"):
        return 0

    history = _history(payload)
    recall_at = _last_recall_index(history)
    if recall_at < 0:
        return 0

    session = payload.get("session_id") or "unknown"
    state = _load_state()
    already = state.get(session)
    # -1/None means "never reminded". A recall block we already covered means silence.
    if isinstance(already, int) and already >= recall_at:
        return 0

    state[session] = recall_at
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
