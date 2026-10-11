"""`backstep doctor`: sanity-check a backstep.toml and its dependencies.

Runs a handful of independent checks — config parses, each upstream server
spawns and answers `tools/list`, the postgres adapter's DSN connects (if
configured), and the journal file is readable with a matching schema
version — plus two non-failing, informational reports: every upstream tool
that has no entry in `backstep.toml` (a nudge towards `backstep init`, not a
failure by itself: an unclassified tool falls back to
`unknown_tool_behavior`, which is a deliberate, if conservative, policy) and
which Postgres backend `backstep eval`/the test suite would use.

Checks are split into two groups:

- Config-independent checks (journal readability, the `BACKSTEP_PG_DSN`
  adapter connection, and the `eval:postgres-mode` report) always run, even
  when `backstep.toml` is missing or fails to parse.
- Config-dependent checks (upstream server reachability, the config-derived
  postgres adapter connection, unclassified-tool listing) are reported as
  SKIPPED rather than omitted or FAILED when there's no config to read.

This means `backstep doctor` never crashes on a missing/malformed config —
it degrades to reporting what it can and pointing at `backstep init` or
`backstep.example.toml` for the rest.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from mcp import Client, StdioServerParameters, stdio_client

from backstep.adapters.postgres import PostgresAdapter, resolve_dsn
from backstep.config import BackstepConfig, UpstreamServer, load_config
from backstep.eval import TEST_PG_DSN_ENV_VAR
from backstep.journal import SCHEMA_VERSION, Journal

CONNECT_TIMEOUT_SECONDS = 15.0

CheckStatus = Literal["passed", "failed", "skipped"]

_DEFAULT_JOURNAL_PATH: str = BackstepConfig.model_fields["journal_path"].default

#: `<your-project-ref>`-style tokens left over from backstep.example.toml.
_PLACEHOLDER_RE = re.compile(r"<[A-Za-z][A-Za-z0-9_-]*>")


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: CheckStatus
    detail: str

    @property
    def ok(self) -> bool:
        """True unless this check FAILED. SKIPPED checks do not fail the run."""
        return self.status != "failed"


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
        return all(check.status != "failed" for check in self.checks)


async def _list_server_tools(server: UpstreamServer) -> list[str]:
    params = StdioServerParameters(command=server.command, args=server.args, env=server.env or None)
    async with Client(stdio_client(params)) as client:
        if client.server_capabilities.tools is None:
            return []
        result = await client.list_tools()
        return [tool.name for tool in result.tools]


def _unfilled_placeholders(server: UpstreamServer) -> list[str]:
    """Placeholders still present in `server`'s command/args, plus the names
    of env vars whose values contain one (env values are named, not echoed,
    since a filled-in one is usually a secret)."""
    found = [m for value in (server.command, *server.args) for m in _PLACEHOLDER_RE.findall(value)]
    found += [
        f"env {key}" for key, value in (server.env or {}).items() if _PLACEHOLDER_RE.search(value)
    ]
    return found


async def _check_server(
    server: UpstreamServer, config_path: str | Path
) -> tuple[DoctorCheck, list[str]]:
    # Checked before spawning: on Windows cmd.exe parses `<...>` as a
    # redirect, so the spawn fails with an opaque TaskGroup error instead.
    placeholders = _unfilled_placeholders(server)
    if placeholders:
        check = DoctorCheck(
            f"server:{server.name}",
            "failed",
            f"unfilled placeholder(s) in {config_path}: {', '.join(placeholders)}; "
            "replace them with your own values",
        )
        return check, []
    try:
        tool_names = await asyncio.wait_for(
            _list_server_tools(server), timeout=CONNECT_TIMEOUT_SECONDS
        )
    except Exception as exc:
        check = DoctorCheck(
            f"server:{server.name}", "failed", f"failed to spawn/list tools: {exc}"
        )
        return check, []
    check = DoctorCheck(
        f"server:{server.name}", "passed", f"responded with {len(tool_names)} tool(s)"
    )
    return check, tool_names


async def _check_postgres_adapter(config: BackstepConfig) -> DoctorCheck:
    """Config-dependent: connects using the DSN resolved from `config` (env still wins)."""
    pg_config = config.adapters.postgres
    uses_postgres = pg_config is not None or any(
        tool.adapter == "postgres" for server in config.servers for tool in server.tools
    )
    if not uses_postgres:
        return DoctorCheck("adapter:postgres", "skipped", "not configured")
    try:
        dsn = resolve_dsn(pg_config)
        adapter = await PostgresAdapter.connect(dsn)
        await adapter.close()
    except Exception as exc:
        return DoctorCheck("adapter:postgres", "failed", f"could not connect: {exc}")
    return DoctorCheck("adapter:postgres", "passed", "connected")


async def _check_postgres_env_adapter() -> DoctorCheck:
    """Config-independent: only runs if `BACKSTEP_PG_DSN` is set in the environment."""
    dsn = os.environ.get("BACKSTEP_PG_DSN")
    if not dsn:
        return DoctorCheck("adapter:postgres (env)", "skipped", "BACKSTEP_PG_DSN not set")
    try:
        adapter = await PostgresAdapter.connect(dsn)
        await adapter.close()
    except Exception as exc:
        return DoctorCheck("adapter:postgres (env)", "failed", f"could not connect: {exc}")
    return DoctorCheck("adapter:postgres (env)", "passed", "connected")


async def _check_journal(config: BackstepConfig | None) -> DoctorCheck:
    journal_path_str = config.journal_path if config is not None else _DEFAULT_JOURNAL_PATH
    journal_path = Path(journal_path_str).expanduser()
    if not journal_path.exists():
        return DoctorCheck(
            "journal", "passed", f"{journal_path} does not exist yet (will be created on first use)"
        )
    journal = Journal()
    try:
        await journal.open(journal_path)
        version = await journal.schema_version()
    except Exception as exc:
        return DoctorCheck("journal", "failed", f"could not read {journal_path}: {exc}")
    finally:
        await journal.close()
    if version != SCHEMA_VERSION:
        return DoctorCheck(
            "journal",
            "failed",
            f"schema version {version} does not match expected {SCHEMA_VERSION}",
        )
    return DoctorCheck("journal", "passed", f"{journal_path} readable, schema version {version}")


def _eval_postgres_mode_check() -> DoctorCheck:
    """Which Postgres backend `backstep eval`/the Postgres-backed test suite would use.

    Always "passed" — this is informational, not a pass/fail health check.
    """
    external_dsn = os.environ.get(TEST_PG_DSN_ENV_VAR)
    if external_dsn:
        return DoctorCheck(
            "eval:postgres-mode",
            "passed",
            f"external ({TEST_PG_DSN_ENV_VAR} is set) — a disposable schema per "
            "run/test on that server",
        )
    return DoctorCheck(
        "eval:postgres-mode",
        "passed",
        "testcontainers — spins a throwaway Postgres container per eval run/test "
        f"session; set {TEST_PG_DSN_ENV_VAR} to use an already-running Postgres instead",
    )


async def run_doctor(config_path: str | Path) -> DoctorReport:
    """Run every doctor check against `config_path` and return the results.

    Never raises on a missing/malformed config: the "config" check FAILS
    with a pointer to `backstep init` / `backstep.example.toml`, but every
    config-independent check (journal, env-DSN adapter, eval:postgres-mode)
    still runs, and config-dependent checks (servers, unclassified tools,
    config-derived adapter) are reported SKIPPED rather than omitted.
    """
    checks: list[DoctorCheck] = []
    unclassified: list[UnclassifiedTool] = []

    config: BackstepConfig | None = None
    try:
        config = load_config(config_path)
        checks.append(DoctorCheck("config", "passed", f"loaded {config_path}"))
    except FileNotFoundError:
        checks.append(
            DoctorCheck(
                "config",
                "failed",
                f"no config found at {config_path}; run `backstep init` or copy "
                "backstep.example.toml to backstep.toml",
            )
        )
    except Exception as exc:
        checks.append(
            DoctorCheck(
                "config",
                "failed",
                f"failed to parse {config_path}: {exc}; run `backstep init` or copy "
                "backstep.example.toml to backstep.toml",
            )
        )

    # -- config-independent checks: always run -------------------------
    checks.append(await _check_journal(config))
    checks.append(await _check_postgres_env_adapter())
    checks.append(_eval_postgres_mode_check())

    # -- config-dependent checks: SKIPPED (not FAILED) when config is None --
    if config is None:
        checks.append(DoctorCheck("upstream servers", "skipped", "no config loaded"))
        checks.append(DoctorCheck("unclassified tools", "skipped", "no config loaded"))
    else:
        for server in config.servers:
            check, tool_names = await _check_server(server, config_path)
            checks.append(check)
            configured_names = {tool.name for tool in server.tools}
            for tool_name in tool_names:
                if tool_name not in configured_names:
                    unclassified.append(UnclassifiedTool(server.name, tool_name))
        checks.append(await _check_postgres_adapter(config))

    return DoctorReport(checks=checks, unclassified_tools=unclassified)
