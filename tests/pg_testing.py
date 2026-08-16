"""Postgres test-mode helpers: prefer BACKSTEP_TEST_PG_DSN, fall back to testcontainers.

The `postgres_dsn` pytest fixture itself lives in `tests/conftest.py` —
fixtures need to be there (or imported directly) to be auto-discoverable —
so it's automatically available to every test under `tests/` with no import
needed. This module holds the plain functions/markers built on top of it:
mode detection and the shared skip condition.
"""

from __future__ import annotations

import os

import pytest

from backstep.eval import TEST_PG_DSN_ENV_VAR, with_search_path

__all__ = [
    "TEST_PG_DSN_ENV_VAR",
    "postgres_available",
    "postgres_test_mode",
    "requires_postgres",
    "with_search_path",
]


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


def postgres_test_mode() -> str:
    """"external" if `BACKSTEP_TEST_PG_DSN` is set, else "testcontainers"."""
    return "external" if os.environ.get(TEST_PG_DSN_ENV_VAR) else "testcontainers"


def postgres_available() -> bool:
    return bool(os.environ.get(TEST_PG_DSN_ENV_VAR)) or _docker_available()


requires_postgres = pytest.mark.skipif(
    not postgres_available(),
    reason=f"requires {TEST_PG_DSN_ENV_VAR} or a working Docker daemon",
)
