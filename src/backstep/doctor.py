"""`backstep doctor`: sanity-check a backstep.toml and its dependencies.

Runs a handful of independent checks — config parses, each upstream server
spawns and answers `tools/list`, the postgres adapter's DSN connects (if
configured), and the journal file is readable with a matching schema
version — plus two non-failing, informational reports: every upstream tool
that has no entry in `backstep.toml` (a nudge towards `backstep init`, not a
failure by itself: an unclassified tool falls back to
`unknown_tool_behavior`, which is a deliberate, if conservative, policy) and
which Postgres backend `backstep eval`/the test suite would use.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path

from mcp import Client, StdioServerParameters, stdio_client

from backstep.adapters.postgres import PostgresAdapter, resolve_dsn
from backstep.config import BackstepConfig, UpstreamServer, load_config
from backstep.eval import TEST_PG_DSN_ENV_VAR
from backstep.journal import SCHEMA_VERSION, Journal

CONNECT_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class UnclassifiedTool:
    server_name: str
    tool_name: str


@dataclass(frozen=True)
class DoctorReport:
    checks: list[DoctorCheck] = field(default_factory=list)
    unclassified_tools: list[UnclassifiedTool] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


async def _list_server_tools(server: UpstreamServer) -> list[str]:
    params = StdioServerParameters(command=server.command, args=server.args, env=server.env or None)
    async with Client(stdio_client(params)) as client:
        if client.server_capabilities.tools is None:
            return []
        result = await client.list_tools()
        return [tool.name for tool in result.tools]


async def _check_server(server: UpstreamServer) -> tuple[DoctorCheck, list[str]]:
    try:
        tool_names = await asyncio.wait_for(
            _list_server_tools(server), timeout=CONNECT_TIMEOUT_SECONDS
        )
    except Exception as exc:
        check = DoctorCheck(f"server:{server.name}", False, f"failed to spawn/list tools: {exc}")
        return check, []
    check = DoctorCheck(f"server:{server.name}", True, f"responded with {len(tool_names)} tool(s)")
    return check, tool_names


async def _check_postgres_adapter(config: BackstepConfig) -> DoctorCheck | None:
    pg_config = config.adapters.postgres
    uses_postgres = pg_config is not None or any(
        tool.adapter == "postgres" for server in config.servers for tool in server.tools
    )
    if not uses_postgres:
        return None
    try:
        dsn = resolve_dsn(pg_config)
        adapter = await PostgresAdapter.connect(dsn)
        await adapter.close()
    except Exception as exc:
        return DoctorCheck("adapter:postgres", False, f"could not connect: {exc}")
    return DoctorCheck("adapter:postgres", True, "connected")


async def _check_journal(config: BackstepConfig) -> DoctorCheck:
    journal_path = Path(config.journal_path).expanduser()
    if not journal_path.exists():
        return DoctorCheck(
            "journal", True, f"{journal_path} does not exist yet (will be created on first use)"
        )
    journal = Journal()
    try:
        await journal.open(journal_path)
        version = await journal.schema_version()
    except Exception as exc:
        return DoctorCheck("journal", False, f"could not read {journal_path}: {exc}")
    finally:
        await journal.close()
    if version != SCHEMA_VERSION:
        return DoctorCheck(
            "journal",
            False,
            f"schema version {version} does not match expected {SCHEMA_VERSION}",
        )
    return DoctorCheck("journal", True, f"{journal_path} readable, schema version {version}")


def _eval_postgres_mode_check() -> DoctorCheck:
    """Which Postgres backend `backstep eval`/the Postgres-backed test suite would use.

    Always `ok=True` — this is informational, not a pass/fail health check.
    """
    external_dsn = os.environ.get(TEST_PG_DSN_ENV_VAR)
    if external_dsn:
        return DoctorCheck(
            "eval:postgres-mode",
            True,
            f"external ({TEST_PG_DSN_ENV_VAR} is set) — a disposable schema per "
            "run/test on that server",
        )
    return DoctorCheck(
        "eval:postgres-mode",
        True,
        "testcontainers — spins a throwaway Postgres container per eval run/test "
        f"session; set {TEST_PG_DSN_ENV_VAR} to use an already-running Postgres instead",
    )


async def run_doctor(config_path: str | Path) -> DoctorReport:
    """Run every doctor check against `config_path` and return the results."""
    try:
        config = load_config(config_path)
    except Exception as exc:
        return DoctorReport(checks=[DoctorCheck("config", False, f"failed to parse: {exc}")])

    checks = [DoctorCheck("config", True, f"parsed {config_path}")]
    unclassified: list[UnclassifiedTool] = []

    for server in config.servers:
        check, tool_names = await _check_server(server)
        checks.append(check)
        configured_names = {tool.name for tool in server.tools}
        for tool_name in tool_names:
            if tool_name not in configured_names:
                unclassified.append(UnclassifiedTool(server.name, tool_name))

    postgres_check = await _check_postgres_adapter(config)
    if postgres_check is not None:
        checks.append(postgres_check)

    checks.append(await _check_journal(config))
    checks.append(_eval_postgres_mode_check())

    return DoctorReport(checks=checks, unclassified_tools=unclassified)
