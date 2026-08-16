"""Base adapter interface for capturing and reverting tool-specific state.

An adapter knows how to snapshot the state a specific tool is about to
change (`capture`) and how to undo that change later (`revert`). Adapters are
optional: a tool policy with no `adapter` configured is still journaled, just
without a pre-state snapshot.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

from pydantic import BaseModel

from backstep.journal import ActionRecord


class PreState(BaseModel):
    kind: str
    data: dict[str, Any]


class RevertResult(BaseModel):
    ok: bool
    detail: str
    restored_count: int | None = None


class Adapter(Protocol):
    name: str

    async def capture(self, tool_name: str, args: dict[str, Any]) -> PreState | None: ...

    async def revert(self, entry: ActionRecord) -> RevertResult: ...

    def fingerprint(self, state: dict[str, Any]) -> str: ...


def canonical_fingerprint(state: dict[str, Any]) -> str:
    """Stable sha256 hex digest of `state`'s canonical JSON encoding.

    Available for adapters to implement `fingerprint()` with, and used by the
    proxy itself as the fallback fingerprint when a tool has no adapter.
    """
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AdapterRegistry:
    """Lookup of configured adapters by name."""

    def __init__(self, adapters: dict[str, Adapter] | None = None) -> None:
        self._adapters: dict[str, Adapter] = dict(adapters) if adapters else {}

    def register(self, adapter: Adapter) -> None:
        self._adapters[adapter.name] = adapter

    def get_adapter(self, name: str) -> Adapter | None:
        return self._adapters.get(name)
