"""Journal storage for recording pre-change state so operations can be reverted.

The journal is append-only for the fields that describe what actually happened
(``args_json``, ``pre_state_json``, ``executed_at``): once an action row is
inserted, no code path in this module issues an UPDATE that touches those
columns. Only status-transition fields (status, error, post_state_json,
post_fingerprint, reverted_at, revert_note) are ever updated.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import aiosqlite
from pydantic import BaseModel

from backstep.config import ReversibilityClass

ActionStatus = Literal["pending", "committed", "failed", "reverted", "unrevertable"]

SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    client_name TEXT,
    label TEXT
);

CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    seq INTEGER NOT NULL,
    server_name TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    adapter TEXT,
    reversibility TEXT NOT NULL,
    args_json TEXT NOT NULL,
    pre_state_json TEXT,
    post_state_json TEXT,
    post_fingerprint TEXT,
    status TEXT NOT NULL,
    error TEXT,
    executed_at TEXT NOT NULL,
    reverted_at TEXT,
    revert_note TEXT
);

CREATE INDEX IF NOT EXISTS idx_actions_session_seq ON actions(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_actions_status ON actions(status);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ActionRecord(BaseModel):
    id: str
    session_id: str
    seq: int
    server_name: str
    tool_name: str
    adapter: str | None
    reversibility: ReversibilityClass
    args: Any
    pre_state: Any | None
    post_state: Any | None
    post_fingerprint: str | None
    status: ActionStatus
    error: str | None
    executed_at: str
    reverted_at: str | None
    revert_note: str | None


class SessionSummary(BaseModel):
    id: str
    started_at: str
    ended_at: str | None
    client_name: str | None
    label: str | None
    action_count: int
    committed_count: int


class Journal:
    """Append-only SQLite journal of tool-call actions, grouped into sessions."""

    def __init__(self) -> None:
        self._conn: aiosqlite.Connection | None = None
        self._seq_lock = asyncio.Lock()

    @property
    def _connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Journal is not open; call open() first.")
        return self._conn

    async def open(self, path: str | Path) -> None:
        db_path = Path(path).expanduser()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(db_path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA_SQL)
        cursor = await self._conn.execute("SELECT COUNT(*) FROM schema_version")
        row = await cursor.fetchone()
        assert row is not None
        if row[0] == 0:
            await self._conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def start_session(
        self, client_name: str | None = None, label: str | None = None
    ) -> str:
        session_id = str(uuid4())
        await self._connection.execute(
            "INSERT INTO sessions (id, started_at, ended_at, client_name, label) "
            "VALUES (?, ?, NULL, ?, ?)",
            (session_id, _now_iso(), client_name, label),
        )
        await self._connection.commit()
        return session_id

    async def end_session(self, session_id: str) -> None:
        await self._connection.execute(
            "UPDATE sessions SET ended_at = ? WHERE id = ?", (_now_iso(), session_id)
        )
        await self._connection.commit()

    async def begin_action(
        self,
        session_id: str,
        server_name: str,
        tool_name: str,
        reversibility: ReversibilityClass,
        args: Any,
        adapter: str | None = None,
        pre_state: Any | None = None,
        note: str | None = None,
    ) -> str:
        """Insert a new `pending` action.

        `note` seeds the `error` column at insert time for non-fatal context
        (e.g. "capture_failed: ..."); it does not imply the action failed.

        `args`/`pre_state` are serialized with `default=str` since adapters
        journal raw DB values (`Decimal`, `datetime`, ...) that plain `json`
        can't encode; round-tripping such a field back out of the journal
        yields its string form, not the original type.
        """
        action_id = str(uuid4())
        args_json = json.dumps(args, default=str)
        pre_state_json = None if pre_state is None else json.dumps(pre_state, default=str)
        executed_at = _now_iso()

        async with self._seq_lock:
            cursor = await self._connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM actions WHERE session_id = ?",
                (session_id,),
            )
            seq_row = await cursor.fetchone()
            assert seq_row is not None
            seq = seq_row[0]
            await self._connection.execute(
                """
                INSERT INTO actions (
                    id, session_id, seq, server_name, tool_name, adapter,
                    reversibility, args_json, pre_state_json, post_state_json,
                    post_fingerprint, status, error, executed_at, reverted_at,
                    revert_note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'pending', ?, ?, NULL, NULL)
                """,
                (
                    action_id,
                    session_id,
                    seq,
                    server_name,
                    tool_name,
                    adapter,
                    reversibility,
                    args_json,
                    pre_state_json,
                    note,
                    executed_at,
                ),
            )
            await self._connection.commit()

        return action_id

    async def commit_action(
        self, action_id: str, post_state: Any | None, post_fingerprint: str | None
    ) -> None:
        await self._require_action(action_id)
        post_state_json = None if post_state is None else json.dumps(post_state, default=str)
        await self._connection.execute(
            "UPDATE actions SET post_state_json = ?, post_fingerprint = ?, status = 'committed' "
            "WHERE id = ?",
            (post_state_json, post_fingerprint, action_id),
        )
        await self._connection.commit()

    async def fail_action(self, action_id: str, error: str) -> None:
        await self._require_action(action_id)
        await self._connection.execute(
            "UPDATE actions SET status = 'failed', error = ? WHERE id = ?",
            (error, action_id),
        )
        await self._connection.commit()

    async def mark_reverted(self, action_id: str, note: str | None = None) -> None:
        row = await self._require_action(action_id)
        if row["status"] == "reverted":
            raise ValueError(f"Action {action_id} has already been reverted.")
        await self._connection.execute(
            "UPDATE actions SET status = 'reverted', reverted_at = ?, revert_note = ? "
            "WHERE id = ?",
            (_now_iso(), note, action_id),
        )
        await self._connection.commit()

    async def mark_unrevertable(self, action_id: str, reason: str) -> None:
        await self._require_action(action_id)
        await self._connection.execute(
            "UPDATE actions SET status = 'unrevertable', error = ? WHERE id = ?",
            (reason, action_id),
        )
        await self._connection.commit()

    async def list_actions(
        self,
        session_id: str | None = None,
        limit: int = 50,
        status: ActionStatus | None = None,
    ) -> list[ActionRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)

        cursor = await self._connection.execute(
            f"SELECT * FROM actions {where} ORDER BY session_id, seq DESC LIMIT ?",  # noqa: S608
            params,
        )
        rows = await cursor.fetchall()
        return [_row_to_action(row) for row in rows]

    async def get_action(self, action_id: str) -> ActionRecord:
        row = await self._require_action(action_id)
        return _row_to_action(row)

    async def latest_session(self) -> str | None:
        cursor = await self._connection.execute(
            "SELECT id FROM sessions ORDER BY started_at DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        return None if row is None else str(row["id"])

    async def list_sessions(self, limit: int = 50) -> list[SessionSummary]:
        cursor = await self._connection.execute(
            """
            SELECT s.id, s.started_at, s.ended_at, s.client_name, s.label,
                   COUNT(a.id) AS action_count,
                   SUM(CASE WHEN a.status = 'committed' THEN 1 ELSE 0 END) AS committed_count
            FROM sessions s
            LEFT JOIN actions a ON a.session_id = s.id
            GROUP BY s.id
            ORDER BY s.started_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            SessionSummary(
                id=row["id"],
                started_at=row["started_at"],
                ended_at=row["ended_at"],
                client_name=row["client_name"],
                label=row["label"],
                action_count=row["action_count"],
                committed_count=row["committed_count"] or 0,
            )
            for row in rows
        ]

    async def schema_version(self) -> int:
        cursor = await self._connection.execute("SELECT version FROM schema_version")
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("journal has no schema_version row")
        return int(row[0])

    async def _require_action(self, action_id: str) -> aiosqlite.Row:
        cursor = await self._connection.execute(
            "SELECT * FROM actions WHERE id = ?", (action_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise KeyError(f"No action with id {action_id!r}")
        return row


def _row_to_action(row: aiosqlite.Row) -> ActionRecord:
    return ActionRecord(
        id=row["id"],
        session_id=row["session_id"],
        seq=row["seq"],
        server_name=row["server_name"],
        tool_name=row["tool_name"],
        adapter=row["adapter"],
        reversibility=row["reversibility"],
        args=json.loads(row["args_json"]),
        pre_state=None if row["pre_state_json"] is None else json.loads(row["pre_state_json"]),
        post_state=None if row["post_state_json"] is None else json.loads(row["post_state_json"]),
        post_fingerprint=row["post_fingerprint"],
        status=row["status"],
        error=row["error"],
        executed_at=row["executed_at"],
        reverted_at=row["reverted_at"],
        revert_note=row["revert_note"],
    )
