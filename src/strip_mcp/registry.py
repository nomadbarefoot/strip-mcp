"""Global tool name → server mapping with collision detection."""

from __future__ import annotations

import logging

from .errors import ToolCollisionError, ToolNotFoundError

logger = logging.getLogger(__name__)

_LEVENSHTEIN_MAX = 5  # only suggest if edit distance ≤ this


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = curr
    return prev[-1]


class ToolRegistry:
    """Maps namespaced tool names to their server_id."""

    def __init__(self) -> None:
        self._map: dict[str, str] = {}  # tool_name → server_id

    def register(self, tool_name: str, server_id: str) -> None:
        """Register a tool. Raises ToolCollisionError if name already taken."""
        if tool_name in self._map:
            raise ToolCollisionError(
                f"Tool name {tool_name!r} already registered by server "
                f"{self._map[tool_name]!r}. Use namespace=True or rename."
            )
        self._map[tool_name] = server_id

    def deregister_server(self, server_id: str) -> None:
        """Remove all tools belonging to server_id."""
        self._map = {k: v for k, v in self._map.items() if v != server_id}

    def resolve(self, tool_name: str) -> str:
        """Return server_id for tool_name. Raises ToolNotFoundError if missing."""
        if tool_name in self._map:
            return self._map[tool_name]
        suggestion = self._closest(tool_name)
        raise ToolNotFoundError(tool_name, suggestion)

    def all_names(self) -> list[str]:
        return list(self._map.keys())

    def _closest(self, name: str) -> str | None:
        best: tuple[int, str] | None = None
        for candidate in self._map:
            dist = _levenshtein(name, candidate)
            if dist <= _LEVENSHTEIN_MAX:
                if best is None or dist < best[0]:
                    best = (dist, candidate)
        return best[1] if best else None
