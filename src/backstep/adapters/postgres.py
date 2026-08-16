"""Postgres adapter: capture/revert pre-change state for SQL-executing tools.

Connects to Postgres directly (via `BACKSTEP_PG_DSN` or `[adapters.postgres]
dsn` in backstep.toml) — a connection of its own, independent of whatever
connection the upstream MCP server uses. It parses the raw SQL a tool call is
about to run with sqlglot and, for INSERT/UPDATE/DELETE against a single
table, captures enough to revert it later.

Anything it cannot safely reason about — DDL, TRUNCATE, multi-statement SQL,
unparseable SQL, a predicate matching too many rows — is left uncaptured:
`capture()` returns `None` rather than guess, and the interceptor journals
that as a non-fatal `capture_failed` note while still letting the call
through (see `backstep.proxy._intercept_call_tool`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Literal

import asyncpg
import sqlglot
from sqlglot import exp

from backstep.adapters.base import PreState, RevertResult, canonical_fingerprint
from backstep.config import BackstepConfig, PostgresAdapterConfig
from backstep.journal import ActionRecord

logger = logging.getLogger("backstep.adapters.postgres")

DEFAULT_MAX_CAPTURED_ROWS = 1000
_SQL_ARG_KEYS = ("sql", "query", "statement", "command")

RowOp = Literal["update", "delete"]
_ColumnKind = Literal["identity_always", "identity_by_default", "generated_always", "plain"]


def resolve_dsn(config: PostgresAdapterConfig | None = None) -> str:
    """Resolve the adapter's own Postgres DSN: `BACKSTEP_PG_DSN` env, then config."""
    dsn = os.environ.get("BACKSTEP_PG_DSN")
    if dsn:
        return dsn
    if config is not None and config.dsn:
        return config.dsn
    raise RuntimeError(
        "No Postgres DSN configured for the postgres adapter: set BACKSTEP_PG_DSN "
        "or [adapters.postgres] dsn in backstep.toml"
    )


def _extract_sql(args: dict[str, Any]) -> str | None:
    for key in _SQL_ARG_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _qualified_ident(schema: str, table: str) -> str:
    return f"{_quote_ident(schema)}.{_quote_ident(table)}"


def _rowcount_from_status(status: str) -> int:
    try:
        return int(status.rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


def _rows_from_value(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
        return value
    if isinstance(value, dict):
        for candidate in value.values():
            if (
                isinstance(candidate, list)
                and candidate
                and all(isinstance(item, dict) for item in candidate)
            ):
                return candidate
    return None


def _extract_result_rows(post_state: Any) -> list[dict[str, Any]] | None:
    """Best-effort extraction of RETURNING-style rows from a tool's post_state.

    `post_state` is usually this adapter's own `{"rows": [...], "pk_columns":
    [...]}` shape (from `capture_post`) — checked first — but falls back to
    whatever `backstep.proxy` captured straight from the upstream
    CallToolResult when there's no adapter-native snapshot (e.g. a DELETE, or
    an adapter that declined). That raw shape varies by upstream MCP server,
    so this tries the common cases: structured content that's a list of rows
    (or a dict wrapping one), then scanning text content blocks for JSON that
    looks like rows.
    """
    if not isinstance(post_state, dict):
        return None

    native_rows = _rows_from_value(post_state.get("rows"))
    if native_rows is not None:
        return native_rows

    structured = post_state.get("structured_content") or post_state.get("structuredContent")
    rows = _rows_from_value(structured)
    if rows is not None:
        return rows

    for block in post_state.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if not isinstance(text, str):
            continue
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            continue
        rows = _rows_from_value(parsed)
        if rows is not None:
            return rows
    return None


def _pk_value_tuples(
    rows: list[dict[str, Any]], pk_columns: list[str]
) -> list[tuple[Any, ...]]:
    if not pk_columns:
        return []
    return [
        tuple(row[c] for c in pk_columns)
        for row in rows
        if isinstance(row, dict) and all(c in row for c in pk_columns)
    ]


def _fingerprint_state(state: dict[str, Any]) -> str:
    """sha256 over `state["rows"]` sorted by primary key, keys sorted, values canonicalized.

    Falls back to hashing `state` as-is when it isn't row-shaped, so this
    stays safe to call as the generic `Adapter.fingerprint` even on data this
    adapter didn't produce itself.
    """
    rows = state.get("rows")
    if not isinstance(rows, list):
        return canonical_fingerprint(state)
    pk_columns = state.get("pk_columns") or []

    def sort_key(row: Any) -> str:
        key_values: Any = row
        if isinstance(row, dict) and pk_columns and all(c in row for c in pk_columns):
            key_values = [row[c] for c in pk_columns]
        return json.dumps(key_values, sort_keys=True, separators=(",", ":"), default=str)

    ordered = sorted(rows, key=sort_key)
    canonical = json.dumps(ordered, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PostgresAdapter:
    """Adapter for tools that execute raw SQL against Postgres."""

    name = "postgres"

    def __init__(
        self, pool: asyncpg.Pool, *, max_captured_rows: int = DEFAULT_MAX_CAPTURED_ROWS
    ) -> None:
        self._pool = pool
        self._max_captured_rows = max_captured_rows
        self._pk_cache: dict[tuple[str, str], list[str]] = {}
        self._column_kind_cache: dict[tuple[str, str], dict[str, _ColumnKind]] = {}
        self._default_schema_cache: str | None = None

    @classmethod
    async def connect(
        cls, dsn: str, *, max_captured_rows: int = DEFAULT_MAX_CAPTURED_ROWS
    ) -> PostgresAdapter:
        pool = await asyncpg.create_pool(dsn=dsn)
        assert pool is not None
        return cls(pool, max_captured_rows=max_captured_rows)

    @classmethod
    async def from_config(cls, config: BackstepConfig) -> PostgresAdapter:
        pg_config = config.adapters.postgres
        dsn = resolve_dsn(pg_config)
        max_rows = DEFAULT_MAX_CAPTURED_ROWS if pg_config is None else pg_config.max_captured_rows
        return await cls.connect(dsn, max_captured_rows=max_rows)

    async def close(self) -> None:
        await self._pool.close()

    # -- Adapter protocol ---------------------------------------------------

    async def capture(self, tool_name: str, args: dict[str, Any]) -> PreState | None:
        sql = _extract_sql(args)
        if sql is None:
            return None

        try:
            statements = [s for s in sqlglot.parse(sql, dialect="postgres") if s is not None]
        except Exception:
            logger.debug("postgres adapter: could not parse SQL for %r", tool_name, exc_info=True)
            return None

        if len(statements) != 1:
            # Multi-statement SQL could touch more than one table/predicate;
            # too risky to guess at, so it's left uncaptured.
            return None
        statement = statements[0]

        try:
            if isinstance(statement, exp.Insert):
                return await self._capture_insert(statement)
            if isinstance(statement, exp.Update):
                return await self._capture_rows(statement, op="update")
            if isinstance(statement, exp.Delete):
                return await self._capture_rows(statement, op="delete")
        except Exception:
            logger.debug(
                "postgres adapter: capture declined for %r (unparseable statement or "
                "runtime error building the pre-state SELECT)",
                tool_name,
                exc_info=True,
            )
            return None

        return None  # DDL, TRUNCATE, SELECT, etc. — nothing to capture

    async def revert(self, entry: ActionRecord) -> RevertResult:
        pre_state = entry.pre_state
        if not isinstance(pre_state, dict):
            return RevertResult(
                ok=False, detail="no pre_state was captured; nothing to revert from"
            )

        kind = pre_state.get("kind")
        data = pre_state.get("data")
        if not isinstance(data, dict):
            return RevertResult(ok=False, detail=f"malformed pre_state for kind {kind!r}")

        if kind == "pg_rows":
            return await self._revert_rows(data)
        if kind == "pg_insert":
            return await self._revert_insert(entry, data)
        return RevertResult(ok=False, detail=f"unknown pre_state kind {kind!r}; cannot revert")

    def fingerprint(self, state: dict[str, Any]) -> str:
        return _fingerprint_state(state)

    async def capture_post(
        self, pre_state: PreState, result: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Re-read the affected rows by primary key right after the call that produced them.

        Not part of the `Adapter` protocol proper — `backstep.proxy` duck-checks
        for this optional hook so `post_state`/`post_fingerprint` are built the
        *same way* `current_fingerprint` reads live rows later ({"rows": ...,
        "pk_columns": ...}), making the two directly comparable for drift
        detection. Using the raw tool result for one side and a row snapshot
        for the other would make every comparison a false mismatch.

        For `pg_rows`, an UPDATE's rows still exist and are re-read at their
        new values; a DELETE's rows are gone, so this returns `None` (the
        caller falls back to hashing the raw tool result — fine, since a
        deleted row has no meaningful "current state" to compare later
        anyway). For `pg_insert`, the new row(s) are identified from the
        RETURNING data on `result`, the same way `revert()` does it.
        """
        table = pre_state.data.get("table")
        pk_columns: list[str] = []
        pk_values: list[tuple[Any, ...]] = []

        if pre_state.kind == "pg_rows":
            pk_columns = pre_state.data.get("pk_columns") or []
            pk_values = _pk_value_tuples(pre_state.data.get("rows") or [], pk_columns)
        elif pre_state.kind == "pg_insert" and table:
            schema, table_name = table.split(".", 1)
            pk_columns = await self._pk_columns(schema, table_name)
            pk_values = _pk_value_tuples(_extract_result_rows(result) or [], pk_columns)
        else:
            return None

        if not table or not pk_columns or not pk_values:
            return None
        current_rows = await self._fetch_rows_by_pk(table, pk_columns, pk_values)
        if not current_rows:
            return None
        return {"rows": current_rows, "pk_columns": pk_columns}

    # -- schema resolution ---------------------------------------------------

    async def _default_schema(self) -> str:
        """The schema an unqualified table name resolves to on this connection.

        Cached for the adapter's lifetime: `search_path` is set once, at
        connection time (by the DSN or the server's default), and doesn't
        change afterwards. Resolving it from the live connection — rather
        than hardcoding "public" — is what makes captures/reverts land in
        whatever schema the adapter is actually connected to (a test's
        throwaway schema, a `search_path`-scoped production connection,
        anything), not always the default database-wide schema.
        """
        if self._default_schema_cache is None:
            async with self._pool.acquire() as conn:
                self._default_schema_cache = await conn.fetchval("SELECT current_schema()")
        return self._default_schema_cache

    async def _resolve_table_name(self, table_expr: exp.Table) -> tuple[str, str]:
        """(schema, table) for a parsed sqlglot Table, resolving an unqualified schema."""
        schema = table_expr.db or await self._default_schema()
        return (schema, table_expr.name)

    # -- capture: INSERT ------------------------------------------------

    async def _capture_insert(self, statement: exp.Insert) -> PreState | None:
        target = statement.this
        table_expr = target.this if isinstance(target, exp.Schema) else target
        if not isinstance(table_expr, exp.Table):
            return None
        schema, table = await self._resolve_table_name(table_expr)
        columns = [c.name for c in target.expressions] if isinstance(target, exp.Schema) else []
        return PreState(kind="pg_insert", data={"table": f"{schema}.{table}", "columns": columns})

    # -- capture: UPDATE / DELETE ----------------------------------------

    async def _capture_rows(
        self, statement: exp.Update | exp.Delete, *, op: RowOp
    ) -> PreState | None:
        table_expr = statement.this
        if not isinstance(table_expr, exp.Table):
            return None
        schema, table = await self._resolve_table_name(table_expr)

        where = statement.args.get("where")
        select = exp.select("*").from_(table_expr.copy())
        if where is not None:
            select = select.where(where.this.copy())
        select = select.limit(self._max_captured_rows + 1)

        pk_columns = await self._pk_columns(schema, table)

        async with self._pool.acquire() as conn:
            records = await conn.fetch(select.sql(dialect="postgres"))

        if len(records) > self._max_captured_rows:
            logger.warning(
                "postgres adapter: predicate on %s.%s matched more than %d row(s); refusing "
                "to capture",
                schema,
                table,
                self._max_captured_rows,
            )
            return None

        data: dict[str, Any] = {
            "table": f"{schema}.{table}",
            "pk_columns": pk_columns,
            "rows": [dict(record) for record in records],
            "op": op,
        }
        if op == "delete":
            data["note"] = "identity/metadata columns may not survive restore (R2)"
        return PreState(kind="pg_rows", data=data)

    # -- revert: pg_rows --------------------------------------------------

    async def _revert_rows(self, data: dict[str, Any]) -> RevertResult:
        table = data.get("table")
        pk_columns: list[str] = data.get("pk_columns") or []
        rows: list[dict[str, Any]] = data.get("rows") or []
        op = data.get("op")

        if not table:
            return RevertResult(
                ok=False, detail="pre_state is missing the target table; cannot revert"
            )
        if not rows:
            return RevertResult(
                ok=True, detail="no rows were captured; nothing to revert", restored_count=0
            )
        if not pk_columns:
            return RevertResult(
                ok=False,
                detail=f"table {table} has no primary key; cannot safely revert rows by identity",
            )

        schema, table_name = table.split(".", 1)
        if op == "update":
            return await self._revert_update(schema, table_name, pk_columns, rows)
        if op == "delete":
            return await self._revert_delete(schema, table_name, pk_columns, rows)
        return RevertResult(ok=False, detail=f"unknown row-capture operation {op!r}; cannot revert")

    async def _revert_update(
        self, schema: str, table: str, pk_columns: list[str], rows: list[dict[str, Any]]
    ) -> RevertResult:
        qualified = _qualified_ident(schema, table)
        restored = 0
        async with self._pool.acquire() as conn, conn.transaction():
            for row in rows:
                set_columns = [c for c in row if c not in pk_columns]
                if not set_columns:
                    continue
                set_clause = ", ".join(
                    f"{_quote_ident(c)} = ${i + 1}" for i, c in enumerate(set_columns)
                )
                where_clause = " AND ".join(
                    f"{_quote_ident(c)} = ${i + 1}"
                    for i, c in enumerate(pk_columns, start=len(set_columns))
                )
                values = [row[c] for c in set_columns] + [row[c] for c in pk_columns]
                await conn.execute(
                    f"UPDATE {qualified} SET {set_clause} WHERE {where_clause}", *values
                )
                restored += 1
        return RevertResult(
            ok=True,
            detail=f"restored {restored} row(s) in {schema}.{table}",
            restored_count=restored,
        )

    async def _revert_delete(
        self, schema: str, table: str, pk_columns: list[str], rows: list[dict[str, Any]]
    ) -> RevertResult:
        qualified = _qualified_ident(schema, table)
        column_kinds = await self._column_kinds(schema, table)

        all_columns = list(rows[0].keys())
        insert_columns = [
            c for c in all_columns if column_kinds.get(c, "plain") != "generated_always"
        ]
        skipped_columns = [c for c in all_columns if c not in insert_columns]
        needs_override = any(column_kinds.get(c) == "identity_always" for c in insert_columns)

        col_list = ", ".join(_quote_ident(c) for c in insert_columns)
        placeholders = ", ".join(f"${i + 1}" for i in range(len(insert_columns)))
        overriding = " OVERRIDING SYSTEM VALUE" if needs_override else ""
        sql = f"INSERT INTO {qualified} ({col_list}){overriding} VALUES ({placeholders})"

        restored = 0
        async with self._pool.acquire() as conn, conn.transaction():
            for row in rows:
                await conn.execute(sql, *[row[c] for c in insert_columns])
                restored += 1

        detail = f"restored {restored} row(s) in {schema}.{table} by re-inserting them"
        if skipped_columns:
            detail += f"; could not restore generated column(s): {', '.join(skipped_columns)}"
        return RevertResult(ok=True, detail=detail, restored_count=restored)

    # -- revert: pg_insert --------------------------------------------------

    async def _revert_insert(self, entry: ActionRecord, data: dict[str, Any]) -> RevertResult:
        table = data.get("table")
        if not table:
            return RevertResult(
                ok=False, detail="pre_state is missing the target table; cannot revert"
            )
        schema, table_name = table.split(".", 1)

        pk_columns = await self._pk_columns(schema, table_name)
        if not pk_columns:
            return RevertResult(
                ok=False,
                detail=(
                    f"table {table} has no primary key; cannot safely identify "
                    "inserted rows to delete"
                ),
            )

        result_rows = _extract_result_rows(entry.post_state)
        if not result_rows:
            return RevertResult(
                ok=False,
                detail=(
                    "the tool's response did not contain identifiable RETURNING data for the "
                    f"row(s) inserted into {table}; refusing to delete by guesswork"
                ),
            )
        if not all(all(c in row for c in pk_columns) for row in result_rows):
            return RevertResult(
                ok=False,
                detail=(
                    f"the tool's response did not include primary key column(s) {pk_columns} "
                    f"for {table}; refusing to delete by guesswork"
                ),
            )

        qualified = _qualified_ident(schema, table_name)
        deleted = 0
        async with self._pool.acquire() as conn, conn.transaction():
            for row in result_rows:
                where_clause = " AND ".join(
                    f"{_quote_ident(c)} = ${i + 1}" for i, c in enumerate(pk_columns)
                )
                status = await conn.execute(
                    f"DELETE FROM {qualified} WHERE {where_clause}", *[row[c] for c in pk_columns]
                )
                deleted += _rowcount_from_status(status)

        return RevertResult(
            ok=True,
            detail=f"deleted {deleted} row(s) inserted into {table}",
            restored_count=deleted,
        )

    # -- schema introspection (cached per table per process) ----------------

    async def _pk_columns(self, schema: str, table: str) -> list[str]:
        key = (schema, table)
        cached = self._pk_cache.get(key)
        if cached is not None:
            return cached
        async with self._pool.acquire() as conn:
            records = await conn.fetch(
                """
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                WHERE tc.constraint_type = 'PRIMARY KEY'
                  AND tc.table_schema = $1
                  AND tc.table_name = $2
                ORDER BY kcu.ordinal_position
                """,
                schema,
                table,
            )
        columns = [r["column_name"] for r in records]
        self._pk_cache[key] = columns
        return columns

    async def _column_kinds(self, schema: str, table: str) -> dict[str, _ColumnKind]:
        key = (schema, table)
        cached = self._column_kind_cache.get(key)
        if cached is not None:
            return cached
        async with self._pool.acquire() as conn:
            records = await conn.fetch(
                """
                SELECT column_name, is_identity, identity_generation, is_generated
                FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = $2
                """,
                schema,
                table,
            )
        kinds: dict[str, _ColumnKind] = {}
        for r in records:
            if r["is_generated"] == "ALWAYS":
                kinds[r["column_name"]] = "generated_always"
            elif r["is_identity"] == "YES" and r["identity_generation"] == "ALWAYS":
                kinds[r["column_name"]] = "identity_always"
            elif r["is_identity"] == "YES":
                kinds[r["column_name"]] = "identity_by_default"
            else:
                kinds[r["column_name"]] = "plain"
        self._column_kind_cache[key] = kinds
        return kinds

    async def _fetch_rows_by_pk(
        self, table: str, pk_columns: list[str], pk_values: list[tuple[Any, ...]]
    ) -> list[dict[str, Any]]:
        if not pk_columns or not pk_values:
            return []
        schema, table_name = table.split(".", 1)
        qualified = _qualified_ident(schema, table_name)
        where_clause = " AND ".join(
            f"{_quote_ident(c)} = ${i + 1}" for i, c in enumerate(pk_columns)
        )
        select_sql = f"SELECT * FROM {qualified} WHERE {where_clause}"

        rows: list[dict[str, Any]] = []
        async with self._pool.acquire() as conn:
            for values in pk_values:
                record = await conn.fetchrow(select_sql, *values)
                if record is not None:
                    rows.append(dict(record))
        return rows


@dataclass(frozen=True)
class RowSnapshot:
    """The row(s) an action is tracking, as they exist right now."""

    table: str
    pk_columns: list[str]
    rows: list[dict[str, Any]]
    """May be empty — that means none of the tracked primary keys currently exist."""


async def fetch_current_rows(adapter: PostgresAdapter, entry: ActionRecord) -> RowSnapshot | None:
    """Re-fetch, by primary key, the row(s) `entry` touched, as they exist right now.

    Primary keys come from `pre_state` for row-captured ops (`pg_rows`:
    UPDATE or DELETE) and from `post_state`'s RETURNING-derived rows for
    `pg_insert` (an insert's `pre_state` has no concrete row data of its
    own). Returns `None` when there's nothing trackable at all — no table,
    no primary key, no primary key values to look up. A non-`None` result
    may still carry an empty `rows` list, meaning none of those primary keys
    exist anymore.
    """
    pre_state = entry.pre_state
    if not isinstance(pre_state, dict):
        return None
    kind = pre_state.get("kind")
    data = pre_state.get("data")
    if not isinstance(data, dict):
        return None

    table = data.get("table")
    if not table:
        return None

    pk_columns: list[str]
    if kind == "pg_rows":
        pk_columns = data.get("pk_columns") or []
        pk_values = _pk_value_tuples(data.get("rows") or [], pk_columns)
    elif kind == "pg_insert":
        schema, table_name = table.split(".", 1)
        pk_columns = await adapter._pk_columns(schema, table_name)
        pk_values = _pk_value_tuples(_extract_result_rows(entry.post_state) or [], pk_columns)
    else:
        return None

    if not pk_columns or not pk_values:
        return None

    current_rows = await adapter._fetch_rows_by_pk(table, pk_columns, pk_values)
    return RowSnapshot(table=table, pk_columns=pk_columns, rows=current_rows)


async def fetch_rows_at_pks(
    adapter: PostgresAdapter, table: str, pk_columns: list[str], pk_values: list[tuple[Any, ...]]
) -> list[dict[str, Any]]:
    """Presence check: which of `pk_values` are currently occupied in `table`, and by what.

    This is the mirror image of `fetch_current_rows`: that one asks "are the
    rows an UPDATE/INSERT touched still there and unchanged?" (missing is
    drift); this one asks "are any of the primary keys a DELETE freed up now
    occupied again?" (present is drift — by something, not necessarily what
    was there before). Returns an empty list when none of `pk_values` are
    currently occupied.
    """
    return await adapter._fetch_rows_by_pk(table, pk_columns, pk_values)


async def current_fingerprint(adapter: PostgresAdapter, entry: ActionRecord) -> str | None:
    """Re-read the rows `entry` touched and fingerprint their current state.

    Called by the reverter to detect drift before reverting: if this doesn't
    match `entry.post_fingerprint`, something has changed those rows since
    backstep last saw them. Returns `None` if none of the tracked rows still
    exist (or there's nothing trackable — no pre_state, no primary key, etc).
    """
    snapshot = await fetch_current_rows(adapter, entry)
    if snapshot is None or not snapshot.rows:
        return None
    return adapter.fingerprint({"rows": snapshot.rows, "pk_columns": snapshot.pk_columns})
