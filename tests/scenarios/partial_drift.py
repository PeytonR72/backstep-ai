"""Scenario: an update, then an out-of-band edit to the same row, then undo.

The out-of-band edit happens directly against the database — not through the
proxy — simulating a second process (another agent, a human, a cron job)
touching the same row after backstep's copy of "what happened" was recorded.
The correct outcome is a refusal: reverting on top of that would silently
throw away the out-of-band change.
"""

from __future__ import annotations

from typing import Any

import asyncpg
from mcp import Client

from tests.scenarios.base import Scenario


class PartialDriftScenario(Scenario):
    name = "partial_drift"
    description = "An update, then an out-of-band edit to the same row, then an undo attempt"
    reversibility_class = "R1"
    expect_refusal = True

    _db: asyncpg.Pool

    async def setup(self, db: asyncpg.Pool) -> None:
        self._db = db
        async with db.acquire() as conn:
            await conn.execute("DROP TABLE IF EXISTS documents")
            await conn.execute(
                "CREATE TABLE documents ("
                "id SERIAL PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL"
                ")"
            )
            await conn.execute(
                "INSERT INTO documents (id, title, status) VALUES (1, 'Q3 plan', 'draft')"
            )

    async def damage(self, proxy: Client) -> None:
        await proxy.call_tool(
            "execute_sql", {"sql": "UPDATE documents SET status = 'archived' WHERE id = 1"}
        )
        # Out-of-band: some other process edits the same row, bypassing backstep entirely.
        async with self._db.acquire() as conn:
            await conn.execute("UPDATE documents SET status = 'deleted' WHERE id = 1")

    async def expected(self, db: asyncpg.Pool) -> Any:
        # Correct behavior is to refuse and leave the out-of-band edit alone —
        # NOT to restore the pre-damage ('draft') state over top of it.
        return [{"id": 1, "title": "Q3 plan", "status": "deleted"}]

    async def actual(self, db: asyncpg.Pool) -> Any:
        async with db.acquire() as conn:
            rows = await conn.fetch("SELECT id, title, status FROM documents ORDER BY id")
        return [dict(r) for r in rows]
