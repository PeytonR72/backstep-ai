"""Scenario: 200 INSERTs in a loop, simulating a stuck agent."""

from __future__ import annotations

from typing import Any

import asyncpg
from mcp import Client

from tests.scenarios.base import Scenario

INSERT_COUNT = 200


class RunawayInsertLoopScenario(Scenario):
    name = "runaway_insert_loop"
    description = f"{INSERT_COUNT} INSERTs in a loop, simulating a stuck agent"
    reversibility_class = "R1"

    async def setup(self, db: asyncpg.Pool) -> None:
        async with db.acquire() as conn:
            await conn.execute("DROP TABLE IF EXISTS logs")
            await conn.execute(
                "CREATE TABLE logs (id SERIAL PRIMARY KEY, message TEXT NOT NULL)"
            )

    async def damage(self, proxy: Client) -> None:
        for i in range(INSERT_COUNT):
            await proxy.call_tool(
                "execute_sql",
                {"sql": f"INSERT INTO logs (message) VALUES ('spam {i}') RETURNING id"},
            )

    async def expected(self, db: asyncpg.Pool) -> Any:
        return []

    async def actual(self, db: asyncpg.Pool) -> Any:
        async with db.acquire() as conn:
            rows = await conn.fetch("SELECT id, message FROM logs ORDER BY id")
        return [dict(r) for r in rows]
