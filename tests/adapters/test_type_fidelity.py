"""Round-trip fidelity and fingerprint stability/sensitivity across every
DB-native type the journal/fingerprint codec (Task 2a, `backstep.codec`)
must handle exactly: NUMERIC, timestamptz, timestamp, date, uuid, jsonb,
text[], int[], bytea, boolean (and their nullable variants).

Backed by a real Postgres — see tests/pg_testing.py and tests/conftest.py
for the `postgres_dsn` fixture / `requires_postgres` marker. The whole
module is skipped (not failed) when neither BACKSTEP_TEST_PG_DSN nor a
working Docker daemon is available.

Per the plan's Global Constraints: if the fingerprint-stability-under-
no-change assertion fails for ANY column type, that is a live
false-positive drift bug in backstep.codec/_fingerprint_state, not a test
bug to work around — stop and fix the codec rather than weakening this
test.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from backstep.adapters.postgres import PostgresAdapter, current_fingerprint
from backstep.journal import ActionRecord
from tests.pg_testing import requires_postgres

pytestmark = requires_postgres

CREATE_TABLE_SQL = """
CREATE TABLE type_fidelity (
    id SERIAL PRIMARY KEY,
    amount NUMERIC(12,4) NOT NULL,
    amount_null NUMERIC(12,4),
    seen_at TIMESTAMPTZ NOT NULL,
    seen_at_null TIMESTAMPTZ,
    plain_ts TIMESTAMP NOT NULL,
    plain_ts_null TIMESTAMP,
    seen_date DATE NOT NULL,
    seen_date_null DATE,
    ext_id UUID NOT NULL,
    ext_id_null UUID,
    payload JSONB NOT NULL,
    payload_null JSONB,
    tags TEXT[] NOT NULL,
    tags_null TEXT[],
    scores INT[] NOT NULL,
    scores_null INT[],
    blob BYTEA NOT NULL,
    blob_null BYTEA,
    flag BOOLEAN NOT NULL,
    flag_null BOOLEAN
);
"""

_ROW1_EXT_ID = uuid4()
_ROW2_EXT_ID = uuid4()

_INSERT_SQL = """
INSERT INTO type_fidelity (
    amount, amount_null, seen_at, seen_at_null, plain_ts, plain_ts_null,
    seen_date, seen_date_null, ext_id, ext_id_null, payload, payload_null,
    tags, tags_null, scores, scores_null, blob, blob_null, flag, flag_null
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20
) RETURNING id
"""


def _row1_values() -> list[object]:
    return [
        Decimal("1234.5678"),
        Decimal("1.0000"),
        datetime(2024, 3, 1, 12, 30, 45, 123456, tzinfo=UTC),
        datetime(2024, 3, 1, 12, 30, 45, 654321, tzinfo=UTC),
        datetime(2024, 3, 1, 8, 0, 0, 999),
        datetime(2024, 3, 1, 8, 0, 0),
        date(2024, 3, 1),
        date(2024, 1, 1),
        _ROW1_EXT_ID,
        uuid4(),
        {"b": 2, "a": 1},
        {"z": True},
        ["x", "y"],
        ["p"],
        [3, 1, 2],
        [9],
        b"\x00\x01\xff",
        b"\x02",
        True,
        False,
    ]


def _row2_values() -> list[object]:
    """All nullable columns NULL; non-nullable columns given minimal valid values."""
    return [
        Decimal("0.0000"),
        None,
        datetime(2024, 1, 1, tzinfo=UTC),
        None,
        datetime(2024, 1, 1, 0, 0, 0),
        None,
        date(2024, 1, 1),
        None,
        _ROW2_EXT_ID,
        None,
        {},
        None,
        [],
        None,
        [],
        None,
        b"",
        None,
        False,
        None,
    ]


@pytest.fixture
async def pool(postgres_dsn: str) -> AsyncIterator[asyncpg.Pool]:
    p = await asyncpg.create_pool(dsn=postgres_dsn)
    assert p is not None
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture
async def adapter(postgres_dsn: str) -> AsyncIterator[PostgresAdapter]:
    a = await PostgresAdapter.connect(postgres_dsn)
    try:
        yield a
    finally:
        await a.close()


@pytest.fixture
async def type_fidelity_table(pool: asyncpg.Pool) -> AsyncIterator[None]:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS type_fidelity")
        await conn.execute(CREATE_TABLE_SQL)
        await conn.execute(_INSERT_SQL, *_row1_values())
        await conn.execute(_INSERT_SQL, *_row2_values())
    yield
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS type_fidelity")


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


# -- requirement (i): round-trip capture -> update -> revert -----------------


async def test_round_trip_capture_update_revert(
    adapter: PostgresAdapter, pool: asyncpg.Pool, type_fidelity_table: None
) -> None:
    async with pool.acquire() as conn:
        original_rows = [
            dict(r)
            for r in await conn.fetch("SELECT * FROM type_fidelity ORDER BY id")
        ]

    # capture() only reads pre-state; the caller (normally proxy.py's
    # interceptor) is responsible for actually executing the statement.
    update_sql = """
    UPDATE type_fidelity SET
        amount = 9999.9999,
        amount_null = NULL,
        seen_at = '2030-01-01T00:00:00+00'::timestamptz,
        seen_at_null = NULL,
        plain_ts = '2030-01-01T00:00:00'::timestamp,
        plain_ts_null = NULL,
        seen_date = '2030-01-01'::date,
        seen_date_null = NULL,
        ext_id = '11111111-2222-3333-4444-555555555555'::uuid,
        ext_id_null = NULL,
        payload = '{"changed": true}'::jsonb,
        payload_null = NULL,
        tags = ARRAY['changed'],
        tags_null = NULL,
        scores = ARRAY[9,9,9],
        scores_null = NULL,
        blob = '\\xdeadbeef'::bytea,
        blob_null = NULL,
        flag = NOT flag,
        flag_null = NULL
    WHERE id = 1
    """

    pre_state = await adapter.capture("execute_sql", {"sql": update_sql})
    assert pre_state is not None
    assert pre_state.kind == "pg_rows"

    async with pool.acquire() as conn:
        await conn.execute(update_sql)

    entry = _make_entry(pre_state=pre_state.model_dump())
    result = await adapter.revert(entry)
    assert result.ok is True
    assert result.restored_count == 1

    async with pool.acquire() as conn:
        reverted_rows = [
            dict(r)
            for r in await conn.fetch("SELECT * FROM type_fidelity ORDER BY id")
        ]

    for original_row, current_row in zip(original_rows, reverted_rows, strict=True):
        for column in original_row:
            assert current_row[column] == original_row[column], (
                f"column {column}: {current_row[column]!r} != {original_row[column]!r}"
            )
            # Also assert the native Python type survived, not just equality
            # (e.g. a stringified Decimal can compare equal to a float by
            # coincidence but is still the wrong type).
            assert type(current_row[column]) is type(original_row[column]), (
                f"column {column}: type {type(current_row[column])!r} != "
                f"{type(original_row[column])!r}"
            )


# -- requirement (ii): fingerprint stability under no change -----------------


@pytest.mark.parametrize(
    "column",
    [
        "amount",
        "amount_null",
        "seen_at",
        "seen_at_null",
        "plain_ts",
        "plain_ts_null",
        "seen_date",
        "seen_date_null",
        "ext_id",
        "ext_id_null",
        "payload",
        "payload_null",
        "tags",
        "tags_null",
        "scores",
        "scores_null",
        "blob",
        "blob_null",
        "flag",
        "flag_null",
    ],
)
async def test_fingerprint_stable_under_no_change(
    adapter: PostgresAdapter, pool: asyncpg.Pool, type_fidelity_table: None, column: str
) -> None:
    # A no-op UPDATE touching the row (but not the column under test)
    # exercises the full capture -> commit -> current_fingerprint path
    # without actually changing any data.
    pre_state = await adapter.capture(
        "execute_sql", {"sql": "UPDATE type_fidelity SET id = id WHERE id = 1"}
    )
    assert pre_state is not None

    async with pool.acquire() as conn:
        await conn.execute("UPDATE type_fidelity SET id = id WHERE id = 1")

    post_state = await adapter.capture_post(pre_state, {"content": [], "is_error": False})
    assert post_state is not None
    post_fingerprint = adapter.fingerprint(post_state)

    entry = _make_entry(pre_state=pre_state.model_dump(), post_fingerprint=post_fingerprint)
    current_fp = await current_fingerprint(adapter, entry)

    assert post_fingerprint == current_fp, (
        f"fingerprint drifted with zero changes for column {column!r} — "
        "false-positive drift bug"
    )


# -- requirement (iii): fingerprint sensitivity -------------------------------


async def _capture_commit_fingerprint(
    adapter: PostgresAdapter, pool: asyncpg.Pool, sql: str
) -> str:
    pre_state = await adapter.capture("execute_sql", {"sql": sql})
    assert pre_state is not None
    async with pool.acquire() as conn:
        await conn.execute(sql)
    post_state = await adapter.capture_post(pre_state, {"content": [], "is_error": False})
    assert post_state is not None
    return adapter.fingerprint(post_state)


async def test_fingerprint_sensitive_to_decimal_scale_change(
    adapter: PostgresAdapter, pool: asyncpg.Pool, type_fidelity_table: None
) -> None:
    baseline = await _capture_commit_fingerprint(
        adapter, pool, "UPDATE type_fidelity SET amount = 1234.5678 WHERE id = 1"
    )
    changed = await _capture_commit_fingerprint(
        adapter, pool, "UPDATE type_fidelity SET amount = 1234.5679 WHERE id = 1"
    )
    assert baseline != changed


async def test_fingerprint_sensitive_to_microsecond_change(
    adapter: PostgresAdapter, pool: asyncpg.Pool, type_fidelity_table: None
) -> None:
    baseline = await _capture_commit_fingerprint(
        adapter,
        pool,
        "UPDATE type_fidelity SET seen_at = '2024-03-01T12:30:45.123456+00'::timestamptz "
        "WHERE id = 1",
    )
    changed = await _capture_commit_fingerprint(
        adapter,
        pool,
        "UPDATE type_fidelity SET seen_at = '2024-03-01T12:30:45.123457+00'::timestamptz "
        "WHERE id = 1",
    )
    assert baseline != changed


async def test_fingerprint_sensitive_to_single_byte_change(
    adapter: PostgresAdapter, pool: asyncpg.Pool, type_fidelity_table: None
) -> None:
    baseline = await _capture_commit_fingerprint(
        adapter, pool, r"UPDATE type_fidelity SET blob = '\x0001ff'::bytea WHERE id = 1"
    )
    changed = await _capture_commit_fingerprint(
        adapter, pool, r"UPDATE type_fidelity SET blob = '\x0001fe'::bytea WHERE id = 1"
    )
    assert baseline != changed


async def test_fingerprint_insensitive_to_jsonb_key_reorder(
    adapter: PostgresAdapter, pool: asyncpg.Pool, type_fidelity_table: None
) -> None:
    fp_before = await _capture_commit_fingerprint(
        adapter,
        pool,
        """UPDATE type_fidelity SET payload = '{"a":1,"b":2}'::jsonb WHERE id = 1""",
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT payload FROM type_fidelity WHERE id = 1")
    payload_before = row["payload"]

    fp_after = await _capture_commit_fingerprint(
        adapter,
        pool,
        """UPDATE type_fidelity SET payload = '{"b":2,"a":1}'::jsonb WHERE id = 1""",
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT payload FROM type_fidelity WHERE id = 1")
    payload_after = row["payload"]

    # Postgres's jsonb type normalizes key order on storage (parsed binary
    # form, not text) — confirm asyncpg returns the same Python dict for
    # both before asserting anything about the fingerprint, so a failure
    # here points at codec.py's dict encoding rather than jsonb itself.
    assert payload_before == payload_after

    assert fp_before == fp_after


# -- basic UUID/dict sanity (defensive, not strictly required by the plan) --


def test_fingerprint_hex_digest_shape(adapter: PostgresAdapter) -> None:
    fp = adapter.fingerprint({"rows": [{"id": 1}], "pk_columns": ["id"]})
    assert len(fp) == hashlib.sha256(b"").digest_size * 2
    # sanity: a UUID/Decimal-bearing row still produces a valid hex digest
    fp2 = adapter.fingerprint(
        {
            "rows": [{"id": UUID(int=0), "amount": Decimal("1.00")}],
            "pk_columns": ["id"],
        }
    )
    assert len(fp2) == 64
