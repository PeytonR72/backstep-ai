"""Scenario: DELETE from a parent table with FK children (ON DELETE CASCADE).

This one is expected to recover only *partially*: Postgres's own foreign-key
enforcement deletes the child rows as a side effect of deleting the parent,
inside the same statement — backstep's SQL-level capture never sees a
separate DELETE for the children, so it can restore the parent but not them.
That's a real, honest limit of statement-level capture, not a bug; the
benchmark should show it rather than hide it.
"""

from __future__ import annotations

from typing import Any

import asyncpg
from mcp import Client

from tests.scenarios.base import Scenario


class CascadingDeleteScenario(Scenario):
    name = "cascading_delete"
    description = "DELETE from a parent table whose children are removed via ON DELETE CASCADE"
    reversibility_class = "R2"

    async def setup(self, db: asyncpg.Pool) -> None:
        async with db.acquire() as conn:
            await conn.execute("DROP TABLE IF EXISTS cd_children")
            await conn.execute("DROP TABLE IF EXISTS cd_parents")
            await conn.execute(
                "CREATE TABLE cd_parents (id SERIAL PRIMARY KEY, name TEXT NOT NULL)"
            )
            await conn.execute(
                """
                CREATE TABLE cd_children (
                    id SERIAL PRIMARY KEY,
                    parent_id INTEGER NOT NULL REFERENCES cd_parents(id) ON DELETE CASCADE,
                    label TEXT NOT NULL
                )
                """
            )
            await conn.execute("INSERT INTO cd_parents (id, name) VALUES (1, 'project-x')")
            await conn.executemany(
                "INSERT INTO cd_children (parent_id, label) VALUES ($1, $2)",
                [(1, "task-a"), (1, "task-b"), (1, "task-c")],
            )

    async def damage(self, proxy: Client) -> None:
        await proxy.call_tool("execute_sql", {"sql": "DELETE FROM cd_parents WHERE id = 1"})

    async def expected(self, db: asyncpg.Pool) -> Any:
        return {
            "parents": [{"id": 1, "name": "project-x"}],
            "children": [
                {"id": 1, "parent_id": 1, "label": "task-a"},
                {"id": 2, "parent_id": 1, "label": "task-b"},
                {"id": 3, "parent_id": 1, "label": "task-c"},
            ],
        }

    async def actual(self, db: asyncpg.Pool) -> Any:
        async with db.acquire() as conn:
            parents = await conn.fetch("SELECT id, name FROM cd_parents ORDER BY id")
            children = await conn.fetch(
                "SELECT id, parent_id, label FROM cd_children ORDER BY id"
            )
        return {
            "parents": [dict(r) for r in parents],
            "children": [dict(r) for r in children],
        }
