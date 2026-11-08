"""Agent half of the local-alerts package.

This package carries no tools and no hooks. It exists for two other faces:

    dashboard/plugin_api.py   backend routes under /api/plugins/local-alerts/
    desktop/plugin.js        the chip and pane in the app

A package still needs this file. Without it the loader registers nothing
("No __init__.py"), the plugin never appears as an agent row, and because the
desktop half is paired to that row it never shows up in Capabilities → Plugins
either — the failure looks like "the app ignored my plugin", not like a missing
entry point.
"""

from __future__ import annotations


def register(ctx) -> None:  # noqa: ARG001 — nothing to register on the agent side
    """Entry point required by the plugin loader. No agent-side surface."""
    return None
