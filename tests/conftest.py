"""Shared pytest fixtures for the whole test tree.

Postgres-backed tests get an isolated DSN via the `postgres_dsn` fixture:
prefers `BACKSTEP_TEST_PG_DSN` (an already-running Postgres) with a fresh,
disposable schema per test; falls back to a session-scoped testcontainers
Postgres when that env var isn't set. Fixtures must live in conftest.py (or
be imported directly) to be discoverable by pytest, which is why this isn't
in tests/pg_testing.py alongside the plain helper functions built on it.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import asyncpg
import pytest

from backstep.adapters.postgres import PostgresAdapter
from backstep.eval import TEST_PG_DSN_ENV_VAR, with_search_path
from tests.pg_testing import _docker_available

os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")


@pytest.fixture(scope="session")
def _postgres_base_dsn() -> Iterator[str]:
    """The server-level DSN to build per-test schemas on top of.

    Session-scoped purely as a performance optimization — spinning up a
    testcontainers Postgres (or reusing an external one) once per test run
    rather than once per test. Nothing about isolation depends on this
    scope: `postgres_dsn` below is what tests actually consume, and it's
    function-scoped.
    """
    external_dsn = os.environ.get(TEST_PG_DSN_ENV_VAR)
    if external_dsn:
        yield external_dsn
        return

    if not _docker_available():
        pytest.skip(f"requires {TEST_PG_DSN_ENV_VAR} or a working Docker daemon")

    from testcontainers.community.postgres import PostgresContainer

    container = PostgresContainer("postgres:16-alpine", driver=None)
    try:
        container.start()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"could not start the Postgres testcontainer: {exc}")
        return
    try:
        yield container.get_connection_url()
    finally:
        container.stop()


@pytest.fixture
async def postgres_dsn(_postgres_base_dsn: str) -> AsyncIterator[str]:
    """A DSN whose default schema is fresh, empty, and unique to this test.

    Never reused across tests: each test gets its own `CREATE SCHEMA`,
    dropped afterwards — regardless of whether the underlying server is an
    external one (`BACKSTEP_TEST_PG_DSN`) or a testcontainers-managed
    throwaway container.
    """
    schema = f"backstep_test_{uuid4().hex[:12]}"
    admin_pool = await asyncpg.create_pool(dsn=_postgres_base_dsn)
    assert admin_pool is not None
    try:
        async with admin_pool.acquire() as conn:
            await conn.execute(f'CREATE SCHEMA "{schema}"')
        yield with_search_path(_postgres_base_dsn, schema)
    finally:
        async with admin_pool.acquire() as conn:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin_pool.close()


@pytest.fixture
async def pool(postgres_dsn: str) -> AsyncIterator[asyncpg.Pool]:
    """A plain asyncpg pool on this test's schema, for seeding and inspecting rows.

    Modules that need seeded tables override this by requesting `pool`
    themselves (pytest resolves that to this fixture) and seeding through it.
    """
    p = await asyncpg.create_pool(dsn=postgres_dsn)
    assert p is not None
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture
async def adapter(postgres_dsn: str) -> AsyncIterator[PostgresAdapter]:
    """A `PostgresAdapter` on this test's schema, with default settings."""
    a = await PostgresAdapter.connect(postgres_dsn)
    try:
        yield a
    finally:
        await a.close()
