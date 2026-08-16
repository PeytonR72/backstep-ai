"""Tests for backstep.reverter."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

import asyncpg
import pytest

from backstep.adapters.base import AdapterRegistry
from backstep.adapters.base import RevertResult as AdapterRevertResult
from backstep.adapters.postgres import PostgresAdapter
from backstep.journal import ActionRecord, Journal
from backstep.reverter import REFUSAL_OUTCOMES, Reverter
from tests.pg_testing import requires_postgres


class FakeAdapter:
    """A minimal `Adapter` that never drift-checks (only `PostgresAdapter` does)."""

    name = "fake"

    def __init__(self, fail_ids: frozenset[str] = frozenset()) -> None:
        self._fail_ids = fail_ids

    async def capture(self, tool_name: str, args: dict[str, Any]) -> None:
        return None

    async def revert(self, entry: ActionRecord) -> AdapterRevertResult:
        if entry.id in self._fail_ids:
            return AdapterRevertResult(ok=False, detail="fake adapter: forced failure")
        return AdapterRevertResult(
            ok=True, detail=f"fake adapter: reverted {entry.id}", restored_count=1
        )

    def fingerprint(self, state: dict[str, Any]) -> str:
        return "fake-fingerprint"


async def _make_journal(tmp_path: Path) -> Journal:
    journal = Journal()
    await journal.open(tmp_path / "journal.db")
    return journal


_DEFAULT_PRE_STATE: dict[str, Any] = {"kind": "fake", "data": {}}
_UNSET: dict[str, Any] = {}


async def _begin_and_commit(
    journal: Journal,
    session_id: str,
    *,
    reversibility: str = "R1",
    adapter: str | None = "fake",
    pre_state: dict[str, Any] | None | object = _UNSET,
    post_state: dict[str, Any] | None = None,
    post_fingerprint: str | None = "fp",
) -> str:
    action_id = await journal.begin_action(
        session_id=session_id,
        server_name="srv",
        tool_name="do_thing",
        reversibility=reversibility,  # type: ignore[arg-type]
        args={},
        adapter=adapter,
        pre_state=_DEFAULT_PRE_STATE if pre_state is _UNSET else pre_state,
    )
    await journal.commit_action(
        action_id, post_state=post_state or {}, post_fingerprint=post_fingerprint
    )
    return action_id


@pytest.fixture
def registry() -> AdapterRegistry:
    reg = AdapterRegistry()
    reg.register(FakeAdapter())
    return reg


async def test_revert_action_refuses_non_committed_status(
    tmp_path: Path, registry: AdapterRegistry
) -> None:
    journal = await _make_journal(tmp_path)
    try:
        session_id = await journal.start_session()
        action_id = await journal.begin_action(
            session_id=session_id,
            server_name="srv",
            tool_name="do_thing",
            reversibility="R1",
            args={},
            adapter="fake",
            pre_state={"kind": "fake", "data": {}},
        )
        # Left as "pending" — never committed.

        result = await Reverter(journal, registry).revert_action(action_id)

        assert result.ok is False
        assert result.outcome == "refused_not_committed"
        assert result.outcome in REFUSAL_OUTCOMES
        entry = await journal.get_action(action_id)
        assert entry.status == "pending"  # untouched
    finally:
        await journal.close()


async def test_revert_action_refuses_already_reverted(
    tmp_path: Path, registry: AdapterRegistry
) -> None:
    journal = await _make_journal(tmp_path)
    try:
        session_id = await journal.start_session()
        action_id = await _begin_and_commit(journal, session_id)
        reverter = Reverter(journal, registry)

        first = await reverter.revert_action(action_id)
        assert first.outcome == "reverted"

        second = await reverter.revert_action(action_id)
        assert second.ok is False
        assert second.outcome == "refused_already_reverted"
    finally:
        await journal.close()


async def test_revert_action_refuses_r4(tmp_path: Path, registry: AdapterRegistry) -> None:
    journal = await _make_journal(tmp_path)
    try:
        session_id = await journal.start_session()
        action_id = await _begin_and_commit(journal, session_id, reversibility="R4")

        result = await Reverter(journal, registry).revert_action(action_id)

        assert result.ok is False
        assert result.outcome == "refused_irreversible"
        entry = await journal.get_action(action_id)
        assert entry.status == "unrevertable"
    finally:
        await journal.close()


async def test_revert_action_refuses_r3_without_adapter_inverse(
    tmp_path: Path, registry: AdapterRegistry
) -> None:
    journal = await _make_journal(tmp_path)
    try:
        session_id = await journal.start_session()
        # R3 tools are journaled with pre_state=None (see backstep.proxy) since
        # no compensating inverse is ever captured for them today.
        action_id = await _begin_and_commit(
            journal, session_id, reversibility="R3", adapter=None, pre_state=None
        )

        result = await Reverter(journal, registry).revert_action(action_id)

        assert result.ok is False
        assert result.outcome == "refused_no_inverse"
    finally:
        await journal.close()


async def test_revert_action_refuses_when_pre_state_missing(
    tmp_path: Path, registry: AdapterRegistry
) -> None:
    journal = await _make_journal(tmp_path)
    try:
        session_id = await journal.start_session()
        action_id = await _begin_and_commit(
            journal, session_id, reversibility="R2", adapter="fake", pre_state=None
        )

        result = await Reverter(journal, registry).revert_action(action_id)

        assert result.ok is False
        assert result.outcome == "refused_no_pre_state"
    finally:
        await journal.close()


async def test_plan_returns_same_order_revert_session_executes(
    tmp_path: Path, registry: AdapterRegistry
) -> None:
    journal = await _make_journal(tmp_path)
    try:
        session_id = await journal.start_session()
        ids = [await _begin_and_commit(journal, session_id) for _ in range(3)]

        reverter = Reverter(journal, registry)
        planned = await reverter.plan(session_id)
        assert [a.id for a in planned] == list(reversed(ids))

        report = await reverter.revert_session(session_id)
        assert [r.action_id for r in report.reverted] == list(reversed(ids))
        assert report.skipped_with_reason == []
        assert report.still_changed == []
    finally:
        await journal.close()


async def test_revert_session_aborts_on_first_failure_and_reports_still_changed(
    tmp_path: Path,
) -> None:
    journal = await _make_journal(tmp_path)
    try:
        session_id = await journal.start_session()
        first_id = await _begin_and_commit(journal, session_id)
        second_id = await _begin_and_commit(journal, session_id)
        third_id = await _begin_and_commit(journal, session_id)

        # second_id is reverted before third_id (reverse-seq order), so make
        # the FakeAdapter fail specifically on second_id to force an abort
        # partway through.
        registry = AdapterRegistry()
        registry.register(FakeAdapter(fail_ids=frozenset({second_id})))
        reverter = Reverter(journal, registry)

        report = await reverter.revert_session(session_id)

        assert [r.action_id for r in report.reverted] == [third_id]
        assert [r.action_id for r in report.skipped_with_reason] == [second_id]
        assert report.skipped_with_reason[0].outcome == "failed"
        assert [a.id for a in report.still_changed] == [first_id]

        # The action after the abort point was never touched.
        untouched = await journal.get_action(first_id)
        assert untouched.status == "committed"
    finally:
        await journal.close()


@requires_postgres
class TestPostgresDrift:
    @pytest.fixture
    async def pool(self, postgres_dsn: str) -> AsyncIterator[Any]:
        p = await asyncpg.create_pool(dsn=postgres_dsn)
        assert p is not None
        try:
            yield p
        finally:
            await p.close()

    @pytest.fixture
    async def adapter(self, postgres_dsn: str) -> AsyncIterator[Any]:
        a = await PostgresAdapter.connect(postgres_dsn)
        try:
            yield a
        finally:
            await a.close()

    @pytest.fixture
    async def documents(self, pool: Any) -> AsyncIterator[None]:
        async with pool.acquire() as conn:
            await conn.execute("DROP TABLE IF EXISTS documents")
            await conn.execute(
                "CREATE TABLE documents (id SERIAL PRIMARY KEY, status TEXT NOT NULL)"
            )
            await conn.execute("INSERT INTO documents (id, status) VALUES (1, 'draft')")
        yield
        async with pool.acquire() as conn:
            await conn.execute("DROP TABLE IF EXISTS documents")

    @pytest.fixture
    async def items(self, pool: Any) -> AsyncIterator[None]:
        async with pool.acquire() as conn:
            await conn.execute("DROP TABLE IF EXISTS items")
            await conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            await conn.execute(
                "INSERT INTO items (id, name) VALUES (1, 'first'), (2, 'second')"
            )
        yield
        async with pool.acquire() as conn:
            await conn.execute("DROP TABLE IF EXISTS items")

    @pytest.fixture
    async def logs_no_pk(self, pool: Any) -> AsyncIterator[None]:
        async with pool.acquire() as conn:
            await conn.execute("DROP TABLE IF EXISTS logs_no_pk")
            await conn.execute("CREATE TABLE logs_no_pk (message TEXT NOT NULL)")
            await conn.execute("INSERT INTO logs_no_pk (message) VALUES ('hello')")
        yield
        async with pool.acquire() as conn:
            await conn.execute("DROP TABLE IF EXISTS logs_no_pk")

    async def _delete_and_journal(
        self, journal: Journal, adapter: Any, pool: Any, sql: str
    ) -> str:
        """Capture+execute a DELETE exactly like the proxy would, and commit it to the journal."""
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
        # A DELETE's post_state falls back to the raw (non-row-shaped) tool
        # result in production (see backstep.proxy._capture_post_state); the
        # delete-drift check never reads post_state/post_fingerprint at all,
        # so this stand-in is enough for these tests.
        await journal.commit_action(action_id, post_state={}, post_fingerprint=None)
        return action_id

    async def test_force_over_drift_succeeds_and_records_drift_in_revert_note(
        self, tmp_path: Path, pool: Any, adapter: Any, documents: None
    ) -> None:
        journal = await _make_journal(tmp_path)
        try:
            registry = AdapterRegistry()
            registry.register(adapter)
            reverter = Reverter(journal, registry)

            pre_state = await adapter.capture(
                "execute_sql", {"sql": "UPDATE documents SET status = 'archived' WHERE id = 1"}
            )
            assert pre_state is not None

            async with pool.acquire() as conn:
                await conn.execute("UPDATE documents SET status = 'archived' WHERE id = 1")
                post_row = await conn.fetchrow(
                    "SELECT id, status FROM documents WHERE id = 1"
                )
            post_state = {"rows": [dict(post_row)], "pk_columns": ["id"]}
            post_fingerprint = adapter.fingerprint(post_state)

            session_id = await journal.start_session()
            action_id = await journal.begin_action(
                session_id=session_id,
                server_name="sql",
                tool_name="execute_sql",
                reversibility="R1",
                args={"sql": "UPDATE documents SET status = 'archived' WHERE id = 1"},
                adapter="postgres",
                pre_state=pre_state.model_dump(),
            )
            await journal.commit_action(
                action_id, post_state=post_state, post_fingerprint=post_fingerprint
            )

            # Out-of-band edit after backstep's write — a real conflicting change.
            async with pool.acquire() as conn:
                await conn.execute("UPDATE documents SET status = 'deleted' WHERE id = 1")

            refused = await reverter.revert_action(action_id)
            assert refused.ok is False
            assert refused.outcome == "refused_drift"
            assert refused.drift is not None
            assert len(refused.drift.changed_rows) == 1
            row_drift = refused.drift.changed_rows[0]
            assert row_drift.pk == {"id": 1}
            assert any(
                c.column == "status" and c.old == "archived" and c.new == "deleted"
                for c in row_drift.changes
            )

            # A plain (non-forced) drift refusal must not foreclose a later
            # force=True retry on this same entry: status has to stay
            # "committed", not flip to "unrevertable".
            after_refusal = await journal.get_action(action_id)
            assert after_refusal.status == "committed"

            forced = await reverter.revert_action(action_id, force=True)
            assert forced.ok is True
            assert forced.outcome == "reverted"

            entry = await journal.get_action(action_id)
            assert entry.status == "reverted"
            assert entry.revert_note is not None
            assert "FORCED OVER DETECTED DRIFT" in entry.revert_note
            assert "drift detected" in entry.revert_note

            async with pool.acquire() as conn:
                restored = await conn.fetchrow("SELECT status FROM documents WHERE id = 1")
            # adapter.revert() restores from pre_state, ignoring the drifted value.
            assert restored["status"] == "draft"
        finally:
            await journal.close()

    async def test_delete_reoccupied_pk_refuses_with_occupying_row_in_drift(
        self, tmp_path: Path, pool: Any, adapter: Any, items: None
    ) -> None:
        journal = await _make_journal(tmp_path)
        try:
            registry = AdapterRegistry()
            registry.register(adapter)
            reverter = Reverter(journal, registry)

            action_id = await self._delete_and_journal(
                journal, adapter, pool, "DELETE FROM items WHERE id = 1"
            )

            # Something else reoccupies the freed primary key with different data.
            async with pool.acquire() as conn:
                await conn.execute("INSERT INTO items (id, name) VALUES (1, 'intruder')")

            result = await reverter.revert_action(action_id)

            assert result.ok is False
            assert result.outcome == "refused_drift"
            assert result.drift is not None
            assert result.drift.occupied_rows == [{"id": 1, "name": "intruder"}]
            assert result.drift.changed_rows == []
            assert result.drift.disappeared_rows == []

            entry = await journal.get_action(action_id)
            # Drift is a transient, retriable condition (unlike R4/no-pk/etc),
            # so status stays "committed" rather than flipping to
            # "unrevertable" — a later force=True call must still be able to
            # find and revert this entry.
            assert entry.status == "committed"

            # Refusing means the intruder row was left alone.
            async with pool.acquire() as conn:
                row = await conn.fetchrow("SELECT name FROM items WHERE id = 1")
            assert row["name"] == "intruder"
        finally:
            await journal.close()

    async def test_delete_partial_reoccupation_refuses_wholesale(
        self, tmp_path: Path, pool: Any, adapter: Any, items: None
    ) -> None:
        journal = await _make_journal(tmp_path)
        try:
            registry = AdapterRegistry()
            registry.register(adapter)
            reverter = Reverter(journal, registry)

            action_id = await self._delete_and_journal(
                journal, adapter, pool, "DELETE FROM items WHERE id IN (1, 2)"
            )

            # Only one of the two freed primary keys gets reoccupied.
            async with pool.acquire() as conn:
                await conn.execute("INSERT INTO items (id, name) VALUES (1, 'intruder')")

            result = await reverter.revert_action(action_id)

            assert result.ok is False
            assert result.outcome == "refused_drift"
            assert result.drift is not None
            assert result.drift.occupied_rows == [{"id": 1, "name": "intruder"}]

            # Not a partial restore: id=2 (the untouched key) stays absent.
            async with pool.acquire() as conn:
                rows = await conn.fetch("SELECT id FROM items ORDER BY id")
            assert [r["id"] for r in rows] == [1]
        finally:
            await journal.close()

    async def test_delete_untouched_pk_reverts_successfully(
        self, tmp_path: Path, pool: Any, adapter: Any, items: None
    ) -> None:
        journal = await _make_journal(tmp_path)
        try:
            registry = AdapterRegistry()
            registry.register(adapter)
            reverter = Reverter(journal, registry)

            action_id = await self._delete_and_journal(
                journal, adapter, pool, "DELETE FROM items WHERE id = 1"
            )

            result = await reverter.revert_action(action_id)

            assert result.ok is True
            assert result.outcome == "reverted"
            assert result.drift is None

            async with pool.acquire() as conn:
                row = await conn.fetchrow("SELECT id, name FROM items WHERE id = 1")
            assert row is not None
            assert row["name"] == "first"
        finally:
            await journal.close()

    async def test_delete_on_table_without_primary_key_is_unrevertable(
        self, tmp_path: Path, pool: Any, adapter: Any, logs_no_pk: None
    ) -> None:
        journal = await _make_journal(tmp_path)
        try:
            registry = AdapterRegistry()
            registry.register(adapter)
            reverter = Reverter(journal, registry)

            action_id = await self._delete_and_journal(
                journal, adapter, pool, "DELETE FROM logs_no_pk WHERE message = 'hello'"
            )

            result = await reverter.revert_action(action_id)

            assert result.ok is False
            assert result.outcome == "refused_no_primary_key"
            assert "no primary key" in result.detail.lower()
            assert result.drift is None  # refused before any drift check ran

            entry = await journal.get_action(action_id)
            assert entry.status == "unrevertable"
            assert entry.error is not None
            assert "no primary key" in entry.error.lower()
        finally:
            await journal.close()
