#!/usr/bin/env python3
"""Apply the Hindsight recall patch to the installed plugin, idempotently.

The plugin lives outside this repository, so a patch file alone is not enough:
after any plugin or Hermes update the change is gone and nobody notices, because
the memory block silently stops showing dates and starts repeating facts.

Two behaviours are applied together:
  1. each recalled memory is rendered with its date and, when known, its session;
  2. repeated text is collapsed, keeping the copy that carries provenance.

The script refuses to guess. It stops when the upstream text it expects is
missing, writes a timestamped backup, re-reads the result and verifies it before
reporting success, and restores the backup when verification fails.

Newlines are handled explicitly: the installed plugin uses CRLF while generated
references use LF, and mixing them breaks both the replacement and the check.

Exit codes: 0 = applied or already present, 1 = upstream changed shape or
verification failed, 2 = plugin file missing.

Usage:
    python deploy/hermes-patches/apply_hindsight_patch.py
    python deploy/hermes-patches/apply_hindsight_patch.py --path <plugin_dir>
    python deploy/hermes-patches/apply_hindsight_patch.py --check
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import shutil
import sys

DEFAULT_PLUGIN_DIR = pathlib.Path.home() / "AppData/Local/hermes/plugins/hindsight"

MARKER = "def _fmt_recall_line("
DEDUPE_MARKER = "def _dedupe_recall("
WIRED = "self._dedupe_recall("

BS = chr(92)

HELPER = '''    def _fmt_recall_line(self, r, prefix: str = "- ") -> str:
        """Render one recalled memory with its date and, when known, its source session.

        Hindsight returns occurred_start / occurred_end / mentioned_at, but the block used to
        render only ``r.text`` - a fact from yesterday looked identical to one from three weeks
        ago, and there was no way back to the conversation it came from.

        Raw ``experience`` facts carry provenance (metadata.session_id, or a ``session:`` tag);
        consolidated ``world`` / ``observation`` facts currently lose it, so the session marker
        is simply omitted for those. Dates and provenance are metadata: no model call involved.
        """
        when = (
            getattr(r, "occurred_start", None)
            or getattr(r, "occurred_end", None)
            or getattr(r, "mentioned_at", None)
        )
        stamp = str(when)[:10] if when else ""

        sid = None
        meta = getattr(r, "metadata", None) or {}
        if isinstance(meta, dict):
            sid = meta.get("session_id")
        if not sid:
            for tag in getattr(r, "tags", None) or []:
                if isinstance(tag, str) and tag.startswith("session:"):
                    sid = tag.split(":", 1)[1]
                    break

        if sid:
            marker = f"{stamp} · {str(sid)[:8]}" if stamp else str(sid)[:8]
            return f"{prefix}[{marker}] {r.text}"
        if stamp:
            return f"{prefix}[{stamp}] {r.text}"
        return f"{prefix}{r.text}"

    @staticmethod
    def _recall_key(text) -> str:
        """Normalised text used to spot the same fact stored more than once."""
        cleaned = str(text or "").split("| When:")[0].lower()
        cleaned = "".join(ch if (ch.isalnum() or ch == " ") else " " for ch in cleaned)
        return " ".join(cleaned.split())[:160]

    @staticmethod
    def _has_provenance(r) -> bool:
        """True when the record can still be traced back to the session it came from."""
        meta = getattr(r, "metadata", None) or {}
        if isinstance(meta, dict) and meta.get("session_id"):
            return True
        for tag in getattr(r, "tags", None) or []:
            if isinstance(tag, str) and tag.startswith("session:"):
                return True
        return str(getattr(r, "type", "")) == "experience"

    def _dedupe_recall(self, results):
        """Collapse repeated facts, keeping the record that carries provenance.

        Consolidation writes an observation whose text matches the experience it came
        from, so recall regularly returns one sentence twice - measured at 17 percent
        of all returned lines across twenty queries. That is context budget spent on
        nothing, and it makes a single fact look like two. Matching is on normalised
        text, so it is deterministic and costs no model call; when both copies are
        present the one with a session reference wins even if it ranked lower.
        """
        kept = {}
        order = []
        for r in results:
            key = self._recall_key(getattr(r, "text", ""))
            if not key:
                continue
            prev = kept.get(key)
            if prev is None:
                kept[key] = r
                order.append(key)
            elif self._has_provenance(r) and not self._has_provenance(prev):
                kept[key] = r
        return [kept[k] for k in order]

'''

ANCHOR = "    def _do_recall(self, query: str) -> tuple[str, int]:"

JOIN = '"' + BS + 'n".join('

OLD_PREFETCH = '            return "' + BS + 'n".join(f"- {r.text}" for r in results if r.text), len(results)'
OLD_TOOL = ('        return "' + BS + 'n".join(f"{i}. {r.text}" for i, r in enumerate(results, 1))'
            ' or "No relevant memories found."')


def new_prefetch(nl: str) -> str:
    return ("            deduped = self._dedupe_recall([r for r in results if r.text])" + nl
            + "            return " + JOIN
            + "self._fmt_recall_line(r) for r in deduped), len(deduped)")


def new_tool(nl: str) -> str:
    return ("        deduped = self._dedupe_recall(results)" + nl
            + "        return " + JOIN
            + 'self._fmt_recall_line(r, f"{i}. ") for i, r in enumerate(deduped, 1))'
            + ' or "No relevant memories found."')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", type=pathlib.Path, default=DEFAULT_PLUGIN_DIR)
    ap.add_argument("--check", action="store_true", help="report state, never modify")
    args = ap.parse_args()

    target = args.path / "__init__.py"
    if not target.is_file():
        print(f"FAIL: {target} not found")
        return 2

    text = target.read_text(encoding="utf-8")
    has_dates = MARKER in text
    has_dedupe = DEDUPE_MARKER in text and WIRED in text

    if has_dates and has_dedupe:
        print(f"OK: patch already present in {target}")
        return 0

    if args.check:
        print(f"PENDING: dates={has_dates} dedupe={has_dedupe} in {target}")
        return 1

    if has_dates and not has_dedupe:
        print("PARTIAL: the date patch is present but dedupe is missing.")
        print("Restore the upstream file first; this script applies both at once.")
        return 1

    if ANCHOR not in text:
        print(f"FAIL: anchor not found ({ANCHOR!r}); upstream changed shape, review manually")
        return 1
    if OLD_PREFETCH not in text or OLD_TOOL not in text:
        print("FAIL: render sites not found; upstream changed shape, review manually")
        return 1

    nl = "\r\n" if "\r\n" in text else "\n"

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = target.with_suffix(f".py.bak-{stamp}")
    shutil.copy2(target, backup)

    # HELPER already ends with a blank line; adding another would leave two.
    helper_block = HELPER.replace("\n", nl)
    patched = text.replace(ANCHOR, helper_block + ANCHOR, 1)
    patched = patched.replace(OLD_PREFETCH, new_prefetch(nl), 1)
    patched = patched.replace(OLD_TOOL, new_tool(nl), 1)
    target.write_text(patched, encoding="utf-8")

    check = target.read_text(encoding="utf-8")
    ok = (MARKER in check and DEDUPE_MARKER in check and WIRED in check
          and OLD_PREFETCH not in check and OLD_TOOL not in check)
    if not ok:
        shutil.copy2(backup, target)
        print(f"FAIL: verification failed, restored from {backup.name}")
        return 1

    print(f"APPLIED and verified: {target} (backup: {backup.name})")
    print("Restart the Hermes BACKEND, not just the window: tools run in the python")
    print("backend, which survives a UI restart and keeps the old module in memory.")
    print("A UI-only restart looks exactly like a patch that did not apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
