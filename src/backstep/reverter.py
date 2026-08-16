"""Revert orchestration: undo journaled actions, drift-checking first.

`Reverter` is the only thing in the codebase that drives `Adapter.revert()`
end to end — the proxy only journals. Given a journaled `ActionRecord`, it
decides whether reverting it is safe, and if so, does it and marks the
journal accordingly. It's deliberately conservative: any ambiguity (no
pre_state, no adapter, drift since the action ran, an already-reverted or
non-committed action) ends in a refusal, never a guess.

Drift detection compares the row(s) an action touched, as they exist right
now, against what backstep captured immediately after the action committed
(`entry.post_state`/`entry.post_fingerprint`) — not against the pre-action
state, except for DELETE, where drift means the opposite thing entirely:
a DELETE's whole point was freeing up its primary key(s), so a *missing*
row there is the expected post-condition, not drift. What *is* drift for a
DELETE is a primary key that used to be free now being occupied — by
anything, not necessarily a row that looks like what was deleted — since a
revert would silently overwrite whatever now lives there. Partial
occupation (some keys free, some taken) is still drift, refused wholesale
rather than partially restored. A DELETE whose table has no primary key
can't be drift-checked or safely restored by identity at all, so it's
refused as unrevertable before ever attempting `adapter.revert()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from backstep.adapters.base import Adapter, AdapterRegistry
from backstep.adapters.postgres import (
    PostgresAdapter,
    RowSnapshot,
    fetch_current_rows,
    fetch_rows_at_pks,
)
from backstep.journal import ActionRecord, Journal

RevertOutcome = Literal[
    "reverted",
    "refused_not_committed",
    "refused_already_reverted",
    "refused_irreversible",
    "refused_no_inverse",
    "refused_no_pre_state",
    "refused_no_adapter",
    "refused_no_primary_key",
    "refused_drift",
    "failed",
]

#: Outcomes where nothing was mutated back — either by design (a refusal) or
#: because the attempt itself failed.
REFUSAL_OUTCOMES: frozenset[RevertOutcome] = frozenset(
    {
        "refused_not_committed",
        "refused_already_reverted",
        "refused_irreversible",
        "refused_no_inverse",
        "refused_no_pre_state",
        "refused_no_adapter",
        "refused_no_primary_key",
        "refused_drift",
    }
)


@dataclass(frozen=True)
class ColumnChange:
    column: str
    old: Any
    new: Any


@dataclass(frozen=True)
class RowDrift:
    """One row that still exists but no longer matches what backstep last saw."""

    pk: dict[str, Any]
    changes: list[ColumnChange]


@dataclass(frozen=True)
class DriftDetail:
    """Structured diff between what backstep last saw and what's there now.

    Data, not a printed string — CLI code renders this as a table; anything
    that only needs a one-line description can use `summary`.

    `occupied_rows` is specific to DELETE drift: the current contents of
    whatever now occupies primary key(s) the delete freed up — what a
    forced revert would silently overwrite. `changed_rows`/`disappeared_rows`
    are specific to UPDATE/INSERT drift and stay empty for a DELETE.
    """

    table: str | None
    changed_rows: list[RowDrift]
    disappeared_rows: list[dict[str, Any]]
    summary: str
    occupied_rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class RevertResult:
    action_id: str
    tool_name: str
    reversibility: str
    outcome: RevertOutcome
    ok: bool
    detail: str
    drift: DriftDetail | None = None


@dataclass(frozen=True)
class SessionRevertReport:
    """What happened when reverting a whole session, and what didn't.

    `revert_session` stops at the first non-`"reverted"` outcome, so the
    caller can always tell exactly what state the system was left in:
    `reverted` succeeded, `skipped_with_reason` is the one action that
    stopped the run (refused or failed), and `still_changed` is everything
    after it that was never attempted — its effects are still live.
    """

    session_id: str
    reverted: list[RevertResult] = field(default_factory=list)
    skipped_with_reason: list[RevertResult] = field(default_factory=list)
    still_changed: list[ActionRecord] = field(default_factory=list)


def _row_op(entry: ActionRecord) -> str | None:
    """The captured op ("update" | "delete") for a `pg_rows` pre_state, else `None`."""
    if not isinstance(entry.pre_state, dict) or entry.pre_state.get("kind") != "pg_rows":
        return None
    data = entry.pre_state.get("data")
    return data.get("op") if isinstance(data, dict) else None


def _pk_tuple(row: dict[str, Any], pk_columns: list[str]) -> tuple[Any, ...]:
    return tuple(row.get(c) for c in pk_columns)


def _build_drift_detail(
    table: str | None,
    pk_columns: list[str],
    old_rows: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
) -> DriftDetail:
    """Diff `old_rows` (what backstep last saw) against `new_rows` (live now), by primary key."""
    new_by_pk = {_pk_tuple(row, pk_columns): row for row in new_rows}

    changed_rows: list[RowDrift] = []
    disappeared_rows: list[dict[str, Any]] = []
    for old_row in old_rows:
        pk = _pk_tuple(old_row, pk_columns)
        new_row = new_by_pk.get(pk)
        if new_row is None:
            disappeared_rows.append(old_row)
            continue
        changes = [
            ColumnChange(column=column, old=old_row.get(column), new=new_row.get(column))
            for column in old_row
            if column not in pk_columns and old_row.get(column) != new_row.get(column)
        ]
        if changes:
            pk_dict = dict(zip(pk_columns, pk, strict=True))
            changed_rows.append(RowDrift(pk=pk_dict, changes=changes))

    parts = []
    if changed_rows:
        parts.append(f"{len(changed_rows)} row(s) changed")
    if disappeared_rows:
        parts.append(f"{len(disappeared_rows)} row(s) disappeared")
    where = f" on {table}" if table else ""
    summary = (
        f"drift detected{where}: {', '.join(parts)}"
        if parts
        else f"drift detected{where}: current state no longer matches what backstep last saw"
    )
    return DriftDetail(
        table=table, changed_rows=changed_rows, disappeared_rows=disappeared_rows, summary=summary
    )


def _delete_pre_state_data(entry: ActionRecord) -> dict[str, Any] | None:
    """The captured `pg_rows` DELETE data for `entry`, or `None` if it isn't one."""
    if not isinstance(entry.pre_state, dict) or entry.pre_state.get("kind") != "pg_rows":
        return None
    data = entry.pre_state.get("data")
    if not isinstance(data, dict) or data.get("op") != "delete":
        return None
    return data


def _build_delete_drift_detail(
    table: str, pk_columns: list[str], occupied_rows: list[dict[str, Any]]
) -> DriftDetail:
    """DriftDetail for a DELETE whose freed-up primary key(s) are occupied again."""
    keys = [{c: row.get(c) for c in pk_columns} for row in occupied_rows]
    summary = (
        f"drift on {table}: {len(occupied_rows)} of the deleted row's primary key(s) are "
        f"now occupied by different data ({keys}); reverting would silently overwrite it"
    )
    return DriftDetail(
        table=table,
        changed_rows=[],
        disappeared_rows=[],
        summary=summary,
        occupied_rows=occupied_rows,
    )


class Reverter:
    """Reverts journaled actions, one at a time or a whole session at once."""

    def __init__(self, journal: Journal, adapter_registry: AdapterRegistry) -> None:
        self._journal = journal
        self._adapter_registry = adapter_registry

    async def revert_action(self, action_id: str, *, force: bool = False) -> RevertResult:
        """Revert one journaled action by id. See module docstring for the refusal rules."""
        entry = await self._journal.get_action(action_id)
        return await self._revert_entry(entry, force=force)

    async def check_drift(self, action_id: str) -> DriftDetail | None:
        """Live drift check for `action_id`, without attempting a revert. `None` means no drift."""
        entry = await self._journal.get_action(action_id)
        adapter = self._adapter_registry.get_adapter(entry.adapter) if entry.adapter else None
        return await self._check_drift(adapter, entry)

    async def plan(self, session_id: str) -> list[ActionRecord]:
        """The committed actions in `session_id`, reverse-seq ordered — the order `revert_session`
        would attempt them in, without executing anything."""
        return await self._journal.list_actions(
            session_id=session_id, status="committed", limit=1_000_000
        )

    async def revert_session(self, session_id: str, *, force: bool = False) -> SessionRevertReport:
        """Revert every committed action in `session_id`, most-recent-first.

        Stops at the first refusal or failure; everything after that point is
        left untouched and reported in `still_changed`.
        """
        pending = await self.plan(session_id)
        reverted: list[RevertResult] = []
        for index, entry in enumerate(pending):
            result = await self._revert_entry(entry, force=force)
            if result.outcome != "reverted":
                return SessionRevertReport(
                    session_id=session_id,
                    reverted=reverted,
                    skipped_with_reason=[result],
                    still_changed=pending[index + 1 :],
                )
            reverted.append(result)
        return SessionRevertReport(session_id=session_id, reverted=reverted)

    async def _revert_entry(self, entry: ActionRecord, *, force: bool) -> RevertResult:
        if entry.status == "reverted":
            return RevertResult(
                entry.id,
                entry.tool_name,
                entry.reversibility,
                "refused_already_reverted",
                False,
                "this action has already been reverted; refusing a double-undo",
            )

        if entry.status != "committed":
            return RevertResult(
                entry.id,
                entry.tool_name,
                entry.reversibility,
                "refused_not_committed",
                False,
                f"status is {entry.status!r}, not 'committed'; nothing to revert",
            )

        if entry.reversibility == "R4":
            detail = "R4: this action is irreversible by definition"
            await self._journal.mark_unrevertable(entry.id, detail)
            return RevertResult(
                entry.id,
                entry.tool_name,
                entry.reversibility,
                "refused_irreversible",
                False,
                detail,
            )

        adapter = self._adapter_registry.get_adapter(entry.adapter) if entry.adapter else None
        has_pre_state = isinstance(entry.pre_state, dict)

        if entry.reversibility == "R3" and (adapter is None or not has_pre_state):
            detail = "R3 (compensable only) has no adapter inverse defined for this action"
            await self._journal.mark_unrevertable(entry.id, detail)
            return RevertResult(
                entry.id, entry.tool_name, entry.reversibility, "refused_no_inverse", False, detail
            )

        if not has_pre_state or adapter is None:
            detail = (
                "no pre_state was captured for this action"
                if not has_pre_state
                else f"adapter {entry.adapter!r} is not registered"
            )
            outcome: RevertOutcome = (
                "refused_no_pre_state" if not has_pre_state else "refused_no_adapter"
            )
            await self._journal.mark_unrevertable(entry.id, detail)
            return RevertResult(
                entry.id, entry.tool_name, entry.reversibility, outcome, False, detail
            )

        if isinstance(adapter, PostgresAdapter):
            delete_data = _delete_pre_state_data(entry)
            if delete_data is not None and not (delete_data.get("pk_columns") or []):
                table = delete_data.get("table")
                detail = (
                    f"table {table!r} has no primary key; a DELETE revert cannot be safely "
                    "drift-checked or restored by identity"
                )
                await self._journal.mark_unrevertable(entry.id, detail)
                return RevertResult(
                    entry.id,
                    entry.tool_name,
                    entry.reversibility,
                    "refused_no_primary_key",
                    False,
                    detail,
                )

        drift = await self._check_drift(adapter, entry)
        if drift is not None and not force:
            # Deliberately does NOT mark_unrevertable: unlike the refusals
            # above, drift is a transient condition a caller can retry past
            # with force=True — on this same entry, in a later call. Flipping
            # status away from "committed" here would make that retry
            # impossible (a real bug this once was: the entry would come back
            # "refused_not_committed" on the very next attempt).
            return RevertResult(
                entry.id,
                entry.tool_name,
                entry.reversibility,
                "refused_drift",
                False,
                drift.summary,
                drift,
            )

        result = await adapter.revert(entry)
        if not result.ok:
            await self._journal.mark_unrevertable(entry.id, result.detail)
            return RevertResult(
                entry.id,
                entry.tool_name,
                entry.reversibility,
                "failed",
                False,
                result.detail,
                drift,
            )

        note = result.detail
        if drift is not None:
            note = f"FORCED OVER DETECTED DRIFT ({drift.summary}); {note}"
        await self._journal.mark_reverted(entry.id, note)
        return RevertResult(
            entry.id, entry.tool_name, entry.reversibility, "reverted", True, note, drift
        )

    async def _check_drift(
        self, adapter: Adapter | None, entry: ActionRecord
    ) -> DriftDetail | None:
        """`None` means no drift (or nothing checkable); otherwise the structured diff."""
        if not isinstance(adapter, PostgresAdapter):
            return None
        if _row_op(entry) == "delete":
            return await self._check_delete_drift(adapter, entry)
        return await self._check_update_or_insert_drift(adapter, entry)

    async def _check_update_or_insert_drift(
        self, adapter: PostgresAdapter, entry: ActionRecord
    ) -> DriftDetail | None:
        """Drift for UPDATE/INSERT: the touched row(s) went missing, or no longer match."""
        if entry.post_fingerprint is None:
            return None

        snapshot: RowSnapshot | None = await fetch_current_rows(adapter, entry)
        if snapshot is None:
            return None

        live_fingerprint = (
            adapter.fingerprint({"rows": snapshot.rows, "pk_columns": snapshot.pk_columns})
            if snapshot.rows
            else None
        )
        if live_fingerprint == entry.post_fingerprint:
            return None

        old_rows = entry.post_state.get("rows") if isinstance(entry.post_state, dict) else None
        return _build_drift_detail(
            snapshot.table, snapshot.pk_columns, old_rows or [], snapshot.rows
        )

    async def _check_delete_drift(
        self, adapter: PostgresAdapter, entry: ActionRecord
    ) -> DriftDetail | None:
        """Drift for DELETE: any primary key it freed up is occupied again, by anything."""
        data = _delete_pre_state_data(entry)
        if data is None:
            return None
        table = data.get("table")
        pk_columns: list[str] = data.get("pk_columns") or []
        rows = data.get("rows") or []
        if not table or not pk_columns:
            # Missing pk_columns is caught earlier as "refused_no_primary_key"
            # before this is ever reached; nothing left to check here.
            return None

        pk_values = [
            _pk_tuple(row, pk_columns)
            for row in rows
            if isinstance(row, dict) and all(column in row for column in pk_columns)
        ]
        if not pk_values:
            return None

        occupied = await fetch_rows_at_pks(adapter, table, pk_columns, pk_values)
        if not occupied:
            return None
        return _build_delete_drift_detail(table, pk_columns, occupied)
