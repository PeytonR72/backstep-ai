"""Mutation guards: prove each refusal mechanism is what actually produces the refusal.

`partial_drift` and `unparseable_sql` grade "refused (correct)" in `backstep
eval`, and `test_reverter.py` shows a reoccupied DELETE refuses — but a
passing refusal only means something if it would have *failed* without the
mechanism it claims to exercise. Each test here monkeypatches exactly one
safety mechanism off and asserts the outcome flips from a refusal to a
failure (a clobbered edit, a safety-violation grade, a PK conflict).

These tests fail loudly if the scenario still PASSES with its guard
disabled: every test asserts the outcome is *not* a refusal, so a scenario
that keeps refusing with its mechanism removed — i.e. one that isn't
actually exercising that mechanism — turns this file red.

Two non-obvious choices:

  - The `unparseable_sql` guard doesn't just strip `capture()`'s
    try/except so the runtime error propagates. That alone does not flip
    the grade: `backstep.proxy._intercept_call_tool` catches adapter
    exceptions too, journals the action with no pre_state, and the revert
    still refuses with `refused_no_pre_state` (defense in depth). The
    mechanism that actually matters is "decline rather than guess", so the
    mutation makes `capture()` guess — dropping the WHERE clause that
    references the out-of-scope CTE — and asserts the eval now flags a
    safety violation.
  - There is no "DELETE reoccupation" scenario among `tests.scenarios.
    SCENARIOS`, so that guard drives `Reverter.revert_action` directly
    against a real `PostgresAdapter` rather than going through the eval
    grader. Its "grade" is the revert's outcome: a PK-conflict error instead
    of `refused_drift`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

import asyncpg
import pytest
import sqlglot
from sqlglot import exp

from backstep import reverter as reverter_module
from backstep.adapters import postgres as postgres_module
from backstep.adapters.base import Adapter, AdapterRegistry, PreState
from backstep.adapters.postgres import PostgresAdapter
from backstep.eval import ScenarioResult, _run_one_scenario
from backstep.journal import ActionRecord, Journal
from backstep.reverter import DriftDetail, Reverter
from tests.pg_testing import requires_postgres
from tests.scenarios.base import Scenario
from tests.scenarios.partial_drift import PartialDriftScenario
from tests.scenarios.unparseable_sql import UnparseableSqlScenario

pytestmark = [pytest.mark.mutation, requires_postgres]


async def _run_single_scenario(
    scenario_cls: type[Scenario], postgres_dsn: str, tmp_path: Path
) -> ScenarioResult:
    """Run one scenario through the same proxy/journal/grader path `run_eval` uses."""
    journal = Journal()
    await journal.open(tmp_path / "journal.db")
    adapter = await PostgresAdapter.connect(postgres_dsn)
    try:
        return await _run_one_scenario(scenario_cls, postgres_dsn, adapter, journal)
    finally:
        await adapter.close()
        await journal.close()


async def test_partial_drift_guard_disabling_drift_check_clobbers_edit(
    postgres_dsn: str, pool: asyncpg.Pool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With `Reverter._check_drift` reporting no drift, partial_drift's
    out-of-band edit is clobbered by the revert instead of triggering a
    refusal — proving the scenario's "refused" grade depends on drift
    detection actually running."""

    async def _always_no_drift(
        self: Reverter, adapter: Adapter | None, entry: ActionRecord
    ) -> DriftDetail | None:
        return None

    monkeypatch.setattr(reverter_module.Reverter, "_check_drift", _always_no_drift)

    result = await _run_single_scenario(PartialDriftScenario, postgres_dsn, tmp_path)

    assert result.recovered != "refused", (
        "partial_drift graded as refused even with drift detection disabled — this "
        "scenario is not actually testing drift refusal"
    )
    assert result.recovered == "none"
    assert result.notes.startswith("SAFETY VIOLATION")

    # The revert ran over the out-of-band 'deleted' edit, restoring 'draft'.
    async with pool.acquire() as conn:
        status = await conn.fetchval("SELECT status FROM documents WHERE id = 1")
    assert status == "draft"


async def test_unparseable_sql_guard_forcing_capture_miscaptures(
    postgres_dsn: str, pool: asyncpg.Pool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With `capture()` guessing instead of declining — snapshotting the
    UPDATE's target table with the CTE-referencing predicate dropped — the
    action gets a (wrong) pre_state, the revert runs, and the eval grades a
    safety violation instead of "refused". Proves the scenario's refusal
    depends on capture declining an unrewritable predicate."""
    original_capture = postgres_module.PostgresAdapter.capture

    async def _guessing_capture(
        self: PostgresAdapter, tool_name: str, args: dict[str, Any]
    ) -> PreState | None:
        sql = postgres_module._extract_sql(args)
        statement = sqlglot.parse_one(sql, dialect="postgres") if sql else None
        if isinstance(statement, exp.Update) and statement.args.get("with_") is not None:
            guessed = statement.copy()
            guessed.set("where", None)
            return await self._capture_rows(guessed, op="update")
        return await original_capture(self, tool_name, args)

    monkeypatch.setattr(postgres_module.PostgresAdapter, "capture", _guessing_capture)

    result = await _run_single_scenario(UnparseableSqlScenario, postgres_dsn, tmp_path)

    assert result.recovered != "refused", (
        "unparseable_sql graded as refused even with capture guessing past the "
        "unrewritable predicate — this scenario is not actually exercising the guard"
    )
    assert result.recovered == "none"
    assert result.notes.startswith("SAFETY VIOLATION")

    # The guessed snapshot rolled back the flags the (uncaptured) statement set.
    async with pool.acquire() as conn:
        featured = await conn.fetch("SELECT featured FROM widgets ORDER BY id")
    assert [r["featured"] for r in featured] == [False, False, False, False]


async def test_delete_reoccupation_guard_disabling_presence_check_hits_pk_conflict(
    postgres_dsn: str, pool: asyncpg.Pool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With `fetch_rows_at_pks` (the DELETE-reoccupation presence check)
    always reporting the freed primary keys as empty, reverting a DELETE
    whose key an intruder row now occupies attempts the re-insert and hits a
    PK conflict instead of refusing with `refused_drift`. Driven through
    `Reverter` directly — no eval scenario covers DELETE reoccupation."""

    async def _always_empty(
        adapter: PostgresAdapter,
        table: str,
        pk_columns: list[str],
        pk_values: list[tuple[Any, ...]],
    ) -> list[dict[str, Any]]:
        return []

    # reverter.py binds the name via `from ... import fetch_rows_at_pks`, so
    # patching the source module alone wouldn't reach it.
    monkeypatch.setattr(reverter_module, "fetch_rows_at_pks", _always_empty)

    async with pool.acquire() as conn:
        await conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        await conn.execute("INSERT INTO items (id, name) VALUES (1, 'original')")

    adapter = await PostgresAdapter.connect(postgres_dsn)
    journal = Journal()
    await journal.open(tmp_path / "journal.db")
    try:
        sql = "DELETE FROM items WHERE id = 1"
        pre_state = await adapter.capture("execute_sql", {"sql": sql})
        assert pre_state is not None
        async with pool.acquire() as conn:
            await conn.execute(sql)

        session_id = await journal.start_session()
        action_id = await journal.begin_action(
            session_id=session_id,
            server_name="sql",
            tool_name="execute_sql",
            reversibility="R2",
            args={"sql": sql},
            adapter="postgres",
            pre_state=pre_state.model_dump(),
        )
        await journal.commit_action(action_id, post_state={}, post_fingerprint=None)

        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO items (id, name) VALUES (1, 'intruder')")

        registry = AdapterRegistry()
        registry.register(adapter)
        reverter = Reverter(journal, registry)

        # A refusal here would come back as a RevertResult, not an exception,
        # so reaching the re-insert at all proves the presence check was the
        # only thing standing between this revert and the intruder row.
        with pytest.raises(asyncpg.UniqueViolationError):
            result = await reverter.revert_action(action_id)
            pytest.fail(
                f"DELETE revert returned {result.outcome!r} with the presence check "
                "disabled — the reoccupation refusal is not coming from fetch_rows_at_pks"
            )

        # The re-insert's transaction rolled back, so the intruder survives —
        # but only by luck of the PK constraint, not because backstep refused.
        async with pool.acquire() as conn:
            name = await conn.fetchval("SELECT name FROM items WHERE id = 1")
        assert name == "intruder"
    finally:
        await journal.close()
        await adapter.close()
