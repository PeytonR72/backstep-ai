"""Scenario: 3 updates, 1 delete, 1 insert, undone together as one session."""

from __future__ import annotations

from typing import Any

import asyncpg
from mcp import Client

from tests.scenarios.base import Scenario


class MixedSessionScenario(Scenario):
    name = "mixed_session"
    description = "3 updates, 1 delete, 1 insert in one session, undone together"
    reversibility_class = "R2"

    async def setup(self, db: asyncpg.Pool) -> None:
        async with db.acquire() as conn:
            await conn.execute("DROP TABLE IF EXISTS items")
            await conn.execute(
                "CREATE TABLE items ("
                "id SERIAL PRIMARY KEY, name TEXT NOT NULL, qty INTEGER NOT NULL"
                ")"
            )
            await conn.executemany(
                "INSERT INTO items (name, qty) VALUES ($1, $2)",
                [("a", 1), ("b", 2), ("c", 3), ("d", 4)],
            )

    async def damage(self, proxy: Client) -> None:
        for name in ("a", "b", "c"):
            await proxy.call_tool(
                "execute_sql", {"sql": f"UPDATE items SET qty = qty + 10 WHERE name = '{name}'"}
            )
        await proxy.call_tool("execute_sql", {"sql": "DELETE FROM items WHERE name = 'd'"})
        await proxy.call_tool(
            "execute_sql", {"sql": "INSERT INTO items (name, qty) VALUES ('e', 99) RETURNING id"}
        )

    async def expected(self, db: asyncpg.Pool) -> Any:
        return [
            {"name": "a", "qty": 1},
            {"name": "b", "qty": 2},
            {"name": "c", "qty": 3},
            {"name": "d", "qty": 4},
        ]

    async def actual(self, db: asyncpg.Pool) -> Any:
        async with db.acquire() as conn:
            rows = await conn.fetch("SELECT name, qty FROM items ORDER BY name")
        return [{"name": r["name"], "qty": r["qty"]} for r in rows]
