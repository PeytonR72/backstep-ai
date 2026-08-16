"""MCP server exposing one `execute_sql` tool that runs SQL against Postgres.

Stands in for a real Supabase/Postgres MCP server for backstep's Postgres
adapter tests and recovery scenarios: reads its own DSN from the
`SQL_SERVER_DSN` environment variable so it can be pointed at whatever
throwaway Postgres the test/scenario harness spun up, executes whatever SQL
it's given, and returns the resulting rows (if any) as a JSON text block —
including RETURNING rows for INSERT/UPDATE/DELETE, which is what
`backstep.adapters.postgres` looks for when reverting an insert.
"""

from __future__ import annotations

import json
import os

import asyncpg
from mcp.server.mcpserver import MCPServer

server: MCPServer = MCPServer("sql")
_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn=os.environ["SQL_SERVER_DSN"])
    assert _pool is not None
    return _pool


@server.tool()
async def execute_sql(sql: str) -> str:
    """Execute one SQL statement and return any resulting rows as a JSON array."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        try:
            records = await conn.fetch(sql)
        except asyncpg.PostgresError as exc:
            raise RuntimeError(str(exc)) from exc
    return json.dumps([dict(record) for record in records], default=str)


if __name__ == "__main__":
    server.run()
