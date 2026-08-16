"""Base class for backstep's scripted "agent goes wrong" recovery scenarios.

Each scenario seeds a known-good database state, damages it through the
backstep proxy exactly as a misbehaving agent would, and states what the
world should look like once backstep undoes that damage. `backstep eval`
(see `backstep.eval`) runs every scenario in `tests.scenarios.SCENARIOS`
against a throwaway Postgres and grades the actual recovery.

Beyond the three hooks a scenario author writes (`setup`, `damage`,
`expected`), the base class adds two things the *runner* needs and that
don't belong in scenario authors' heads:

  - `actual(db)`: re-derives the same shape `expected(db)` returns, but by
    querying live state. Grading is then just `actual == expected` (full),
    `actual == <state right after damage>` (none), or neither (partial) —
    generic, no scenario-specific comparison code in the runner.
  - `expect_refusal`: True for scenarios whose correct outcome is backstep
    refusing to revert (drift, capture_failed) rather than restoring
    anything — `partial_drift` and `unparseable_sql` set this.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import asyncpg
from mcp import Client

from backstep.config import ReversibilityClass


class Scenario(ABC):
    """One scripted, gradable "agent goes wrong then gets undone" story."""

    name: str
    description: str
    reversibility_class: ReversibilityClass
    expect_refusal: bool = False

    @abstractmethod
    async def setup(self, db: asyncpg.Pool) -> None:
        """Seed a known-good state directly against `db`, bypassing the proxy."""

    @abstractmethod
    async def damage(self, proxy: Client) -> None:
        """Issue tool calls through `proxy`, exactly as a misbehaving agent would."""

    @abstractmethod
    async def expected(self, db: asyncpg.Pool) -> Any:
        """The state that should exist after a correct undo."""

    @abstractmethod
    async def actual(self, db: asyncpg.Pool) -> Any:
        """The live state, in the same shape as `expected`, queried on demand."""
