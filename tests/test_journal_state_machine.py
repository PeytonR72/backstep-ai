"""Journal status as a state machine: multi-step revert sequences against real Postgres.

`test_reverter.py` mostly checks one revert attempt at a time. The bugs that
matter here live *between* attempts — a refusal that flips status to the
wrong value forecloses (or wrongly permits) the next one. So every test in
this file drives an action through several transitions and asserts status,
error and revert_note after each step, not only at the end.

The invariant test at the bottom partitions every refusal in
`REFUSAL_OUTCOMES`, plus `"failed"`, into three classes:

  - TRANSIENT (drift): the condition can go away or be forced past, so status
    must stay "committed" to keep a retry possible.
  - STATUS_GUARD (not committed / already reverted): the refusal is *about*
    the current status, so it must leave that status exactly as it found it —
    an already-reverted action must not be rewritten as "unrevertable".
  - PERMANENT (everything else): no retry can ever succeed, so the action is
    marked "unrevertable". This includes `"failed"` (the adapter returned
    `ok=False`), which isn't in `REFUSAL_OUTCOMES` because `backstep eval`
    must not grade it as a correct refusal, but has the same status contract.

PERMANENT is computed by subtraction, so a refusal reason newly added to
`reverter.py` defaults to it and this file goes red unless that's right —
forcing a conscious classification instead of a silent pass.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from backstep.adapters.base import AdapterRegistry
from backstep.adapters.base import RevertResult as AdapterRevertResult
from backstep.adapters.postgres import PostgresAdapter
from backstep.config import ReversibilityClass
from backstep.journal import ActionRecord, ActionStatus, Journal
from backstep.reverter import REFUSAL_OUTCOMES, Reverter, RevertOutcome, RevertResult
from tests.pg_testing import requires_postgres

_UPDATE_SQL = "UPDATE documents SET status = 'archived' WHERE id = 1"
_DELETE_NO_PK_SQL = "DELETE FROM logs_no_pk WHERE message = 'hello'"

TRANSIENT_REFUSALS = frozenset({"refused_drift"})
STATUS_GUARD_REFUSALS = frozenset({"refused_not_committed", "refused_already_reverted"})
INVARIANT_OUTCOMES: frozenset[RevertOutcome] = REFUSAL_OUTCOMES | {"failed"}
PERMANENT_REFUSALS = INVARIANT_OUTCOMES - TRANSIENT_REFUSALS - STATUS_GUARD_REFUSALS


@pytest.fixture
async def pool(postgres_dsn: str) -> AsyncIterator[Any]:
    """A pool on a schema seeded with `documents` (PK) and `logs_no_pk` (no PK)."""
    p = await asyncpg.create_pool(dsn=postgres_dsn)
    assert p is not None
    try:
        async with p.acquire() as conn:
            await conn.execute(
                "CREATE TABLE documents (id INTEGER PRIMARY KEY, status TEXT NOT NULL)"
            )
            await conn.execute("INSERT INTO documents (id, status) VALUES (1, 'draft')")
            await conn.execute("CREATE TABLE logs_no_pk (message TEXT NOT NULL)")
            await conn.execute("INSERT INTO logs_no_pk (message) VALUES ('hello')")
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
async def journal(tmp_path: Path) -> AsyncIterator[Journal]:
    j = Journal()
    await j.open(tmp_path / "journal.db")
    try:
        yield j
    finally:
        await j.close()


@pytest.fixture
def reverter(journal: Journal, adapter: PostgresAdapter) -> Reverter:
    registry = AdapterRegistry()
    registry.register(adapter)
    return Reverter(journal, registry)


async def _document_status(pool: Any) -> str:
    async with pool.acquire() as conn:
        status: str = await conn.fetchval("SELECT status FROM documents WHERE id = 1")
    return status


async def _set_document_status(pool: Any, status: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute("UPDATE documents SET status = $1 WHERE id = 1", status)


async def _commit_update(journal: Journal, adapter: PostgresAdapter, pool: Any) -> str:
    """Capture, execute and journal `_UPDATE_SQL` exactly as the proxy would."""
    pre_state = await adapter.capture("execute_sql", {"sql": _UPDATE_SQL})
    assert pre_state is not None
    async with pool.acquire() as conn:
        await conn.execute(_UPDATE_SQL)
        post_row = await conn.fetchrow("SELECT id, status FROM documents WHERE id = 1")
    post_state = {"rows": [dict(post_row)], "pk_columns": ["id"]}

    session_id = await journal.start_session()
    action_id = await journal.begin_action(
        session_id=session_id,
        server_name="sql",
        tool_name="execute_sql",
        reversibility="R1",
        args={"sql": _UPDATE_SQL},
        adapter="postgres",
        pre_state=pre_state.model_dump(),
    )
    await journal.commit_action(
        action_id, post_state=post_state, post_fingerprint=adapter.fingerprint(post_state)
    )
    return action_id


async def _commit_delete_without_pk(journal: Journal, adapter: PostgresAdapter, pool: Any) -> str:
    pre_state = await adapter.capture("execute_sql", {"sql": _DELETE_NO_PK_SQL})
    assert pre_state is not None
    async with pool.acquire() as conn:
        await conn.execute(_DELETE_NO_PK_SQL)

    session_id = await journal.start_session()
    action_id = await journal.begin_action(
        session_id=session_id,
        server_name="sql",
        tool_name="execute_sql",
        reversibility="R2",
        args={"sql": _DELETE_NO_PK_SQL},
        adapter="postgres",
        pre_state=pre_state.model_dump(),
    )
    # The delete-path never reads post_state/post_fingerprint (see test_reverter.py).
    await journal.commit_action(action_id, post_state={}, post_fingerprint=None)
    return action_id


async def _journal_synthetic(
    journal: Journal,
    *,
    reversibility: ReversibilityClass,
    adapter: str | None,
    pre_state: dict[str, Any] | None,
    commit: bool = True,
) -> str:
    """Journal an action directly, aimed at one specific branch of `_revert_entry`."""
    session_id = await journal.start_session()
    action_id = await journal.begin_action(
        session_id=session_id,
        server_name="sql",
        tool_name="execute_sql",
        reversibility=reversibility,
        args={"sql": _UPDATE_SQL},
        adapter=adapter,
        pre_state=pre_state,
    )
    if commit:
        await journal.commit_action(action_id, post_state={}, post_fingerprint=None)
    return action_id


def _assert_entry(
    entry: ActionRecord,
    *,
    status: ActionStatus,
    error_contains: str | None = None,
    note_set: bool = False,
    note_contains: str | None = None,
) -> None:
    """Pin all three status-transition fields; unset/`None` means the field must be empty."""
    assert entry.status == status
    if error_contains is None:
        assert entry.error is None
    else:
        assert entry.error is not None
        assert error_contains in entry.error.lower()
    if not (note_set or note_contains):
        assert entry.revert_note is None
    else:
        assert entry.revert_note is not None
        assert (note_contains or "") in entry.revert_note


# --- Multi-step transition sequences ---------------------------------------


@requires_postgres
async def test_committed_drift_refusal_force_revert_reverted(
    journal: Journal, adapter: PostgresAdapter, reverter: Reverter, pool: Any
) -> None:
    action_id = await _commit_update(journal, adapter, pool)
    _assert_entry(await journal.get_action(action_id), status="committed")

    await _set_document_status(pool, "deleted")  # out-of-band edit -> drift
    _assert_entry(await journal.get_action(action_id), status="committed")

    refused = await reverter.revert_action(action_id)
    assert refused.outcome == "refused_drift"
    assert "drift detected" in refused.detail
    _assert_entry(await journal.get_action(action_id), status="committed")
    assert await _document_status(pool) == "deleted"

    forced = await reverter.revert_action(action_id, force=True)
    assert forced.outcome == "reverted"
    entry = await journal.get_action(action_id)
    _assert_entry(entry, status="reverted", note_contains="FORCED OVER DETECTED DRIFT")
    assert entry.reverted_at is not None
    assert await _document_status(pool) == "draft"


@requires_postgres
async def test_committed_drift_refusal_manual_restore_then_plain_revert_succeeds(
    journal: Journal, adapter: PostgresAdapter, reverter: Reverter, pool: Any
) -> None:
    action_id = await _commit_update(journal, adapter, pool)
    _assert_entry(await journal.get_action(action_id), status="committed")

    await _set_document_status(pool, "deleted")
    refused = await reverter.revert_action(action_id)
    assert refused.outcome == "refused_drift"
    _assert_entry(await journal.get_action(action_id), status="committed")

    # Undo the out-of-band edit by hand: the row is back to its post-commit state.
    await _set_document_status(pool, "archived")
    assert await reverter.check_drift(action_id) is None

    result = await reverter.revert_action(action_id)
    assert result.outcome == "reverted"
    assert result.drift is None
    entry = await journal.get_action(action_id)
    _assert_entry(entry, status="reverted", note_set=True)
    assert entry.revert_note is not None
    assert "FORCED" not in entry.revert_note
    assert await _document_status(pool) == "draft"


@requires_postgres
async def test_committed_reverted_second_revert_refuses(
    journal: Journal, adapter: PostgresAdapter, reverter: Reverter, pool: Any
) -> None:
    action_id = await _commit_update(journal, adapter, pool)
    _assert_entry(await journal.get_action(action_id), status="committed")

    first = await reverter.revert_action(action_id)
    assert first.outcome == "reverted"
    after_first = await journal.get_action(action_id)
    _assert_entry(after_first, status="reverted", note_set=True)
    assert await _document_status(pool) == "draft"

    second = await reverter.revert_action(action_id)
    assert second.outcome == "refused_already_reverted"
    after_second = await journal.get_action(action_id)
    # Byte-for-byte unchanged: the refusal must not touch the reverted record.
    assert after_second == after_first
    assert await _document_status(pool) == "draft"


@requires_postgres
async def test_committed_refused_no_primary_key_retry_still_refuses(
    journal: Journal, adapter: PostgresAdapter, reverter: Reverter, pool: Any
) -> None:
    action_id = await _commit_delete_without_pk(journal, adapter, pool)
    _assert_entry(await journal.get_action(action_id), status="committed")

    first = await reverter.revert_action(action_id)
    assert first.outcome == "refused_no_primary_key"
    after_first = await journal.get_action(action_id)
    _assert_entry(after_first, status="unrevertable", error_contains="no primary key")

    # The refusal is permanent: the retry is turned away by the status guard
    # before the primary-key check is ever reached again.
    retry = await reverter.revert_action(action_id)
    assert retry.outcome == "refused_not_committed"
    assert "'unrevertable'" in retry.detail
    assert await journal.get_action(action_id) == after_first

    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM logs_no_pk") == 0


@requires_postgres
async def test_committed_adapter_revert_raises_status_stays_committed_retry_possible(
    journal: Journal,
    adapter: PostgresAdapter,
    reverter: Reverter,
    pool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action_id = await _commit_update(journal, adapter, pool)
    _assert_entry(await journal.get_action(action_id), status="committed")

    async def _boom(entry: ActionRecord) -> Any:
        raise ConnectionError("connection reset mid-revert")

    monkeypatch.setattr(adapter, "revert", _boom)
    # `_revert_entry` only handles an `ok=False` result; a raised exception
    # propagates before any mark_* call, so the journal is never touched.
    with pytest.raises(ConnectionError, match="connection reset"):
        await reverter.revert_action(action_id)
    _assert_entry(await journal.get_action(action_id), status="committed")
    assert await _document_status(pool) == "archived"

    monkeypatch.undo()
    retry = await reverter.revert_action(action_id)
    assert retry.outcome == "reverted"
    _assert_entry(await journal.get_action(action_id), status="reverted", note_set=True)
    assert await _document_status(pool) == "draft"


@requires_postgres
async def test_committed_adapter_revert_not_ok_marks_unrevertable_retry_refused(
    journal: Journal,
    adapter: PostgresAdapter,
    reverter: Reverter,
    pool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action_id = await _commit_update(journal, adapter, pool)
    _assert_entry(await journal.get_action(action_id), status="committed")

    async def _not_ok(entry: ActionRecord) -> AdapterRevertResult:
        return AdapterRevertResult(ok=False, detail="stub adapter declined: pre_state unusable")

    monkeypatch.setattr(adapter, "revert", _not_ok)
    # The counterpart of the raise case above: an `ok=False` *result* is taken
    # as permanent, so the entry is marked unrevertable and retry is foreclosed.
    first = await reverter.revert_action(action_id)
    assert first.outcome == "failed"
    assert first.ok is False
    assert "stub adapter declined" in first.detail
    after_first = await journal.get_action(action_id)
    _assert_entry(after_first, status="unrevertable", error_contains="stub adapter declined")
    # The stub mutated nothing, and the reverter must not have either.
    assert await _document_status(pool) == "archived"

    monkeypatch.undo()
    retry = await reverter.revert_action(action_id)
    assert retry.outcome == "refused_not_committed"
    assert "'unrevertable'" in retry.detail
    assert await journal.get_action(action_id) == after_first
    assert await _document_status(pool) == "archived"


# --- Refusal-class status invariant -----------------------------------------


@dataclass(frozen=True)
class _Refusal:
    result: RevertResult
    status_before: ActionStatus
    entry_after: ActionRecord


async def _drive_to_refusal(
    reason: RevertOutcome,
    journal: Journal,
    adapter: PostgresAdapter,
    reverter: Reverter,
    pool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> _Refusal:
    pre_state = {"kind": "pg_rows", "data": {"op": "update"}}
    if reason == "refused_drift":
        action_id = await _commit_update(journal, adapter, pool)
        await _set_document_status(pool, "deleted")
    elif reason == "refused_already_reverted":
        action_id = await _commit_update(journal, adapter, pool)
        assert (await reverter.revert_action(action_id)).outcome == "reverted"
    elif reason == "refused_not_committed":
        action_id = await _journal_synthetic(
            journal, reversibility="R1", adapter="postgres", pre_state=pre_state, commit=False
        )
    elif reason == "refused_irreversible":
        action_id = await _journal_synthetic(
            journal, reversibility="R4", adapter="postgres", pre_state=pre_state
        )
    elif reason == "refused_no_inverse":
        action_id = await _journal_synthetic(
            journal, reversibility="R3", adapter=None, pre_state=pre_state
        )
    elif reason == "refused_no_pre_state":
        action_id = await _journal_synthetic(
            journal, reversibility="R1", adapter="postgres", pre_state=None
        )
    elif reason == "refused_no_adapter":
        action_id = await _journal_synthetic(
            journal, reversibility="R1", adapter="not_registered", pre_state=pre_state
        )
    elif reason == "refused_no_primary_key":
        action_id = await _commit_delete_without_pk(journal, adapter, pool)
    elif reason == "failed":
        action_id = await _commit_update(journal, adapter, pool)

        async def _not_ok(entry: ActionRecord) -> AdapterRevertResult:
            return AdapterRevertResult(ok=False, detail="stub adapter declined")

        monkeypatch.setattr(adapter, "revert", _not_ok)
    else:
        pytest.fail(f"no setup branch for refusal {reason!r}; add one to _drive_to_refusal")

    status_before = (await journal.get_action(action_id)).status
    result = await reverter.revert_action(action_id)
    return _Refusal(result, status_before, await journal.get_action(action_id))


def test_refusal_classes_partition_refusal_outcomes() -> None:
    assert TRANSIENT_REFUSALS <= REFUSAL_OUTCOMES
    assert STATUS_GUARD_REFUSALS <= REFUSAL_OUTCOMES
    assert not TRANSIENT_REFUSALS & STATUS_GUARD_REFUSALS
    assert TRANSIENT_REFUSALS | STATUS_GUARD_REFUSALS | PERMANENT_REFUSALS == INVARIANT_OUTCOMES
    assert "failed" in PERMANENT_REFUSALS


@requires_postgres
@pytest.mark.parametrize("refusal_reason", sorted(INVARIANT_OUTCOMES))
async def test_refusal_status_invariant(
    refusal_reason: RevertOutcome,
    journal: Journal,
    adapter: PostgresAdapter,
    reverter: Reverter,
    pool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TRANSIENT -> 'committed'; STATUS_GUARD -> unchanged; PERMANENT -> 'unrevertable'."""
    refusal = await _drive_to_refusal(refusal_reason, journal, adapter, reverter, pool, monkeypatch)

    # First: did the setup actually hit the branch under test?
    assert refusal.result.outcome == refusal_reason, refusal.result.detail
    assert refusal.result.ok is False

    status = refusal.entry_after.status
    if refusal_reason in TRANSIENT_REFUSALS:
        assert status == "committed", f"{refusal_reason} is TRANSIENT but left status={status!r}"
    elif refusal_reason in STATUS_GUARD_REFUSALS:
        assert status == refusal.status_before, (
            f"{refusal_reason} is a STATUS_GUARD but changed status "
            f"{refusal.status_before!r} -> {status!r}"
        )
    else:
        assert status == "unrevertable", f"{refusal_reason} is PERMANENT but left status={status!r}"
        assert refusal.entry_after.error == refusal.result.detail
