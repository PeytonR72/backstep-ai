"""Scenario: UPDATE with a predicate matching far more rows than intended."""

from __future__ import annotations

from typing import Any

import asyncpg
from mcp import Client

from tests.scenarios.base import Scenario


class WrongWhereUpdateScenario(Scenario):
    name = "wrong_where_update"
    description = "UPDATE with a predicate matching far more rows than intended"
    reversibility_class = "R1"

    async def setup(self, db: asyncpg.Pool) -> None:
        async with db.acquire() as conn:
            await conn.execute("DROP TABLE IF EXISTS accounts")
            await conn.execute(
                """
                CREATE TABLE accounts (
                    id SERIAL PRIMARY KEY,
                    owner TEXT NOT NULL,
                    balance NUMERIC NOT NULL
                )
                """
            )
            await conn.executemany(
                "INSERT INTO accounts (owner, balance) VALUES ($1, $2)",
                [("alice", 100), ("bob", 250), ("carol", 75), ("dave", 500)],
            )

    async def damage(self, proxy: Client) -> None:
        # Intent: zero out alice's balance only. A dropped id filter (meant to
        # be `id = 1`) instead matches — and zeroes — every account.
        await proxy.call_tool(
            "execute_sql", {"sql": "UPDATE accounts SET balance = 0 WHERE id > 0"}
        )

    async def expected(self, db: asyncpg.Pool) -> Any:
        return [
            {"id": 1, "owner": "alice", "balance": 100},
            {"id": 2, "owner": "bob", "balance": 250},
            {"id": 3, "owner": "carol", "balance": 75},
            {"id": 4, "owner": "dave", "balance": 500},
        ]

    async def actual(self, db: asyncpg.Pool) -> Any:
        async with db.acquire() as conn:
            rows = await conn.fetch("SELECT id, owner, balance FROM accounts ORDER BY id")
        return [
            {"id": r["id"], "owner": r["owner"], "balance": float(r["balance"])} for r in rows
        ]
