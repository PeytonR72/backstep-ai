"""Scenario: a CTE-heavy statement backstep's capture cannot safely handle.

The statement is valid SQL and runs fine upstream — Postgres doesn't need
backstep's help to execute a CTE-driven UPDATE. What it can't do is rewrite
that predicate into a safe standalone SELECT to snapshot pre-state (the WHERE
clause references a CTE, `ranked`, that a bare `SELECT * FROM widgets WHERE
...` wouldn't have in scope), so `capture()` correctly returns `None` rather
than guessing. The call still goes through — backstep degrades to
transparent passthrough, not to a broken tool call — but the resulting
action is honestly unrevertable.
"""

from __future__ import annotations

from typing import Any

import asyncpg
from mcp import Client

from tests.scenarios.base import Scenario

_DAMAGE_SQL = """
WITH ranked AS (
    SELECT id, ROW_NUMBER() OVER (PARTITION BY category ORDER BY id) AS rn
    FROM widgets
)
UPDATE widgets
SET featured = true
FROM ranked
WHERE widgets.id = ranked.id AND ranked.rn = 1
"""


class UnparseableSqlScenario(Scenario):
    name = "unparseable_sql"
    description = "A CTE-heavy UPDATE that capture() cannot rewrite into a safe SELECT"
    reversibility_class = "R2"
    expect_refusal = True

    async def setup(self, db: asyncpg.Pool) -> None:
        async with db.acquire() as conn:
            await conn.execute("DROP TABLE IF EXISTS widgets")
            await conn.execute(
                """
                CREATE TABLE widgets (
                    id SERIAL PRIMARY KEY,
                    category TEXT NOT NULL,
                    featured BOOLEAN NOT NULL DEFAULT false
                )
                """
            )
            await conn.executemany(
                "INSERT INTO widgets (id, category) VALUES ($1, $2)",
                [(1, "a"), (2, "a"), (3, "b"), (4, "b")],
            )

    async def damage(self, proxy: Client) -> None:
        await proxy.call_tool("execute_sql", {"sql": _DAMAGE_SQL})

    async def expected(self, db: asyncpg.Pool) -> Any:
        # Unrevertable by design: the featured flags this statement set are
        # expected to remain exactly as the (uncaptured) statement left them.
        return [
            {"id": 1, "category": "a", "featured": True},
            {"id": 2, "category": "a", "featured": False},
            {"id": 3, "category": "b", "featured": True},
            {"id": 4, "category": "b", "featured": False},
        ]

    async def actual(self, db: asyncpg.Pool) -> Any:
        async with db.acquire() as conn:
            rows = await conn.fetch("SELECT id, category, featured FROM widgets ORDER BY id")
        return [dict(r) for r in rows]
