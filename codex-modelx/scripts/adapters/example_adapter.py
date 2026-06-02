"""Example Codex ModelX tool adapter.

Advanced users can copy this file and implement adapt_tool(tool) to clean or
reshape a Codex tool schema before proxy.py converts it to Chat Completions.
The MVP proxy does not auto-load arbitrary adapters yet; this is the stable
extension point reserved for the next version.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def adapt_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Return a modified copy of one Codex tool schema."""
    cloned = deepcopy(tool)
    description = cloned.get("description")
    if isinstance(description, str) and len(description) > 800:
        cloned["description"] = description[:797] + "..."
    return cloned
