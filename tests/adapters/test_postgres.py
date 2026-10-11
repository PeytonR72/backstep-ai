"""Tests for backstep.adapters.postgres, backed by a real Postgres.

Uses BACKSTEP_TEST_PG_DSN if set, else a testcontainers-managed Postgres —
see tests/pg_testing.py. The whole module is skipped (not failed) when
neither is available, so `uv run pytest` still passes without Docker.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import AsyncIterator

os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

import asyncpg
import pytest

from backstep import codec
from backstep.adapters.postgres import PostgresAdapter, current_fingerprint
from backstep.journal import ActionRecord
from tests.pg_testing import requires_postgres

pytestmark = requires_postgres


def _make_entry(
    *,
    pre_state: dict | None,
    post_state: dict | None = None,
    post_fingerprint: str | None = None,
) -> ActionRecord:
    return ActionRecord(
        id="test-action",
        session_id="test-session",
        seq=1,
        server_name="pgserver",
        tool_name="execute_sql",
        adapter="postgres",
        reversibility="R2",
        args={},
        pre_state=pre_state,
        post_state=post_state,
        post_fingerprint=post_fingerprint,
        status="pending",
        error=None,
        executed_at="2026-01-01T00:00:00+00:00",
        reverted_at=None,
        revert_note=None,
    )


@pytest.fixture
async def adapter(postgres_dsn: str) -> AsyncIterator[PostgresAdapter]:
    a = await PostgresAdapter.connect(postgres_dsn, max_captured_rows=5)
    try:
        yield a
    finally:
        await a.close()


@pytest.fixture
async def widgets(pool: asyncpg.Pool) -> AsyncIterator[None]:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS widgets")
        await conn.execute(
            """
            CREATE TABLE widgets (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                qty INTEGER NOT NULL DEFAULT 0,
                active BOOLEAN NOT NULL DEFAULT true
            )
            """
        )
    yield
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS widgets")


async def test_update_capture_and_revert(
    adapter: PostgresAdapter, pool: asyncpg.Pool, widgets: None
) -> None:
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO widgets (name, qty) VALUES ('a', 10), ('b', 20)")

    pre_state = await adapter.capture(
        "execute_sql", {"sql": "UPDATE widgets SET qty = qty + 100 WHERE name = 'a'"}
    )
    assert pre_state is not None
    assert pre_state.kind == "pg_rows"
    assert pre_state.data["op"] == "update"
    assert pre_state.data["pk_columns"] == ["id"]
    assert [row["qty"] for row in pre_state.data["rows"]] == [10]

    async with pool.acquire() as conn:
        await conn.execute("UPDATE widgets SET qty = qty + 100 WHERE name = 'a'")
        assert await conn.fetchval("SELECT qty FROM widgets WHERE name = 'a'") == 110

    entry = _make_entry(pre_state=pre_state.model_dump())
    result = await adapter.revert(entry)

    assert result.ok is True
    assert result.restored_count == 1
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT qty FROM widgets WHERE name = 'a'") == 10


async def test_delete_capture_and_revert(
    adapter: PostgresAdapter, pool: asyncpg.Pool, widgets: None
) -> None:
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO widgets (name, qty) VALUES ('a', 10), ('b', 20)")

    pre_state = await adapter.capture(
        "execute_sql", {"sql": "DELETE FROM widgets WHERE name = 'b'"}
    )
    assert pre_state is not None
    assert pre_state.kind == "pg_rows"
    assert pre_state.data["op"] == "delete"
    assert "note" in pre_state.data
    captured_row = pre_state.data["rows"][0]

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM widgets WHERE name = 'b'")
        assert await conn.fetchval("SELECT count(*) FROM widgets WHERE name = 'b'") == 0

    entry = _make_entry(pre_state=pre_state.model_dump())
    result = await adapter.revert(entry)

    assert result.ok is True
    assert result.restored_count == 1
    async with pool.acquire() as conn:
        restored = await conn.fetchrow("SELECT * FROM widgets WHERE name = 'b'")
    assert restored is not None
    assert restored["id"] == captured_row["id"]
    assert restored["qty"] == captured_row["qty"]


async def test_delete_revert_with_identity_and_generated_columns(
    adapter: PostgresAdapter, pool: asyncpg.Pool
) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS identity_widgets")
        await conn.execute(
            """
            CREATE TABLE identity_widgets (
                id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                name TEXT NOT NULL,
                name_upper TEXT GENERATED ALWAYS AS (upper(name)) STORED
            )
            """
        )
        await conn.execute("INSERT INTO identity_widgets (name) VALUES ('x'), ('y')")
        original = await conn.fetchrow("SELECT * FROM identity_widgets WHERE name = 'x'")

    try:
        pre_state = await adapter.capture(
            "execute_sql", {"sql": "DELETE FROM identity_widgets WHERE name = 'x'"}
        )
        assert pre_state is not None

        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM identity_widgets WHERE name = 'x'")

        entry = _make_entry(pre_state=pre_state.model_dump())
        result = await adapter.revert(entry)

        assert result.ok is True
        assert result.restored_count == 1
        assert "name_upper" in result.detail  # names what could not be explicitly restored

        async with pool.acquire() as conn:
            restored = await conn.fetchrow("SELECT * FROM identity_widgets WHERE name = 'x'")
        assert restored is not None
        assert restored["id"] == original["id"]  # OVERRIDING SYSTEM VALUE preserved the identity
        assert restored["name_upper"] == "X"  # recomputed, matches the original anyway
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DROP TABLE IF EXISTS identity_widgets")


async def test_insert_capture_and_revert(
    adapter: PostgresAdapter, pool: asyncpg.Pool, widgets: None
) -> None:
    pre_state = await adapter.capture(
        "execute_sql",
        {"sql": "INSERT INTO widgets (name, qty) VALUES ('c', 1) RETURNING id"},
    )
    assert pre_state is not None
    assert pre_state.kind == "pg_insert"
    # An unqualified table name resolves against whatever schema this
    # connection actually defaults to (its search_path) — not hardcoded to
    # "public", since tests run each in their own throwaway schema.
    async with pool.acquire() as conn:
        current_schema = await conn.fetchval("SELECT current_schema()")
    assert pre_state.data == {
        "table": f"{current_schema}.widgets",
        "columns": ["name", "qty"],
    }

    async with pool.acquire() as conn:
        new_id = await conn.fetchval(
            "INSERT INTO widgets (name, qty) VALUES ('c', 1) RETURNING id"
        )

    post_state = {
        "content": [{"type": "text", "text": json.dumps([{"id": new_id}])}],
        "is_error": False,
    }
    entry = _make_entry(pre_state=pre_state.model_dump(), post_state=post_state)
    result = await adapter.revert(entry)

    assert result.ok is True
    assert result.restored_count == 1
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM widgets WHERE id = $1", new_id) == 0


async def test_insert_revert_fails_without_returning_data(
    adapter: PostgresAdapter, pool: asyncpg.Pool, widgets: None
) -> None:
    pre_state = await adapter.capture(
        "execute_sql", {"sql": "INSERT INTO widgets (name, qty) VALUES ('c', 1)"}
    )
    assert pre_state is not None

    empty_post_state = {"content": [], "is_error": False}
    entry = _make_entry(pre_state=pre_state.model_dump(), post_state=empty_post_state)
    result = await adapter.revert(entry)

    assert result.ok is False
    assert "RETURNING" in result.detail or "returning" in result.detail.lower()


@pytest.mark.parametrize(
    "sql",
    [
        "TRUNCATE TABLE widgets",
        "CREATE TABLE nope (id int)",
        "DROP TABLE widgets",
        "this is not valid sql at all (((",
        "SELECT 1; SELECT 2;",
    ],
)
async def test_unparseable_or_unsupported_statements_return_none(
    adapter: PostgresAdapter, widgets: None, sql: str
) -> None:
    assert await adapter.capture("execute_sql", {"sql": sql}) is None


async def test_row_cap_returns_none_when_predicate_matches_too_many_rows(
    adapter: PostgresAdapter, pool: asyncpg.Pool, widgets: None
) -> None:
    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO widgets (name, qty) VALUES ($1, $2)",
            [(f"row-{i}", i) for i in range(10)],
        )

    # adapter fixture is configured with max_captured_rows=5
    pre_state = await adapter.capture(
        "execute_sql", {"sql": "UPDATE widgets SET qty = 0 WHERE active = true"}
    )
    assert pre_state is None


def test_fingerprint_is_stable_and_order_independent(adapter: PostgresAdapter) -> None:
    rows = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
    state = {"rows": rows, "pk_columns": ["id"]}
    reordered_state = {"rows": list(reversed(rows)), "pk_columns": ["id"]}

    fp1 = adapter.fingerprint(state)
    fp2 = adapter.fingerprint(reordered_state)
    assert fp1 == fp2

    expected_canonical = codec.dumps(rows, sort_keys=True)
    expected = hashlib.sha256(expected_canonical.encode("utf-8")).hexdigest()
    assert fp1 == expected


async def test_current_fingerprint_detects_drift_and_missing_rows(
    adapter: PostgresAdapter, pool: asyncpg.Pool, widgets: None
) -> None:
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO widgets (name, qty) VALUES ('a', 10)")

    pre_state = await adapter.capture(
        "execute_sql", {"sql": "UPDATE widgets SET qty = 1 WHERE name = 'a'"}
    )
    assert pre_state is not None
    original_fingerprint = adapter.fingerprint(pre_state.data)

    async with pool.acquire() as conn:
        await conn.execute("UPDATE widgets SET qty = 1 WHERE name = 'a'")

    entry = _make_entry(pre_state=pre_state.model_dump())
    drifted_fingerprint = await current_fingerprint(adapter, entry)
    assert drifted_fingerprint is not None
    assert drifted_fingerprint != original_fingerprint

    await adapter.revert(entry)
    restored_fingerprint = await current_fingerprint(adapter, entry)
    assert restored_fingerprint == original_fingerprint

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM widgets WHERE name = 'a'")
    assert await current_fingerprint(adapter, entry) is None
