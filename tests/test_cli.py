"""Tests for backstep.cli."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from backstep import cli
from backstep.cli import app
from backstep.journal import Journal

os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

from tests.pg_testing import requires_postgres  # noqa: E402

runner = CliRunner()

# CliRunner.invoke calls CLI commands that call asyncio.run() internally, so
# these tests must stay plain `def` (not `async def`) — pytest-asyncio's auto
# mode would otherwise already have an event loop running, and a nested
# asyncio.run() call fails. Async setup/assertions go through asyncio.run()
# explicitly from sync test bodies instead.


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "backstep" in result.output.lower()


def _write_config(tmp_path: Path) -> Path:
    """A minimal config with no upstream servers — the journal-only CLI commands
    (sessions/log/show/undo/undo-session/doctor) don't need a real MCP server."""
    journal_path = (tmp_path / "journal.db").as_posix()
    config_path = tmp_path / "backstep.toml"
    config_path.write_text(f'journal_path = "{journal_path}"\n', encoding="utf-8")
    return config_path


async def _seed_committed_action(config_path: Path) -> tuple[str, str]:
    journal = Journal()
    journal_path = config_path.parent / "journal.db"
    await journal.open(journal_path)
    try:
        session_id = await journal.start_session(client_name="test-client", label="demo")
        action_id = await journal.begin_action(
            session_id=session_id,
            server_name="srv",
            tool_name="write_row",
            reversibility="R2",
            args={"id": 1},
            adapter=None,
            pre_state=None,
        )
        await journal.commit_action(action_id, post_state={"ok": True}, post_fingerprint="fp")
        return session_id, action_id
    finally:
        await journal.close()


def test_sessions_empty(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    result = runner.invoke(app, ["sessions", "--config", str(config_path)])
    assert result.exit_code == 0
    assert "no sessions" in result.output.lower()


def test_sessions_lists_recorded_sessions(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    session_id, _action_id = asyncio.run(_seed_committed_action(config_path))

    result = runner.invoke(app, ["sessions", "--config", str(config_path)])

    assert result.exit_code == 0
    # The ID column is narrow enough that rich may truncate a full UUID.
    assert session_id[:8] in result.output


def test_log_defaults_to_latest_session(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    asyncio.run(_seed_committed_action(config_path))

    result = runner.invoke(app, ["log", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "write_row" in result.output


def test_log_rejects_invalid_status(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    result = runner.invoke(app, ["log", "--config", str(config_path), "--status", "bogus"])
    assert result.exit_code != 0
    assert "status" in result.output.lower()


def test_show_unknown_action_errors(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    result = runner.invoke(app, ["show", "does-not-exist", "--config", str(config_path)])
    assert result.exit_code != 0


def test_show_known_action_prints_detail(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    _session_id, action_id = asyncio.run(_seed_committed_action(config_path))

    result = runner.invoke(app, ["show", action_id, "--config", str(config_path)])

    assert result.exit_code == 0
    assert "write_row" in result.output
    assert "R2" in result.output


async def _get_status(config_path: Path, action_id: str) -> str:
    journal = Journal()
    await journal.open(config_path.parent / "journal.db")
    try:
        entry = await journal.get_action(action_id)
        return entry.status
    finally:
        await journal.close()


def test_undo_without_yes_does_not_execute(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    _session_id, action_id = asyncio.run(_seed_committed_action(config_path))

    result = runner.invoke(
        app, ["undo", action_id, "--config", str(config_path)], input="n\n"
    )

    assert result.exit_code == 0
    assert "aborted" in result.output.lower()
    assert asyncio.run(_get_status(config_path, action_id)) == "committed"  # untouched


def test_undo_session_prints_plan_before_confirming(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    session_id, action_id = asyncio.run(_seed_committed_action(config_path))

    result = runner.invoke(
        app, ["undo-session", session_id, "--config", str(config_path)], input="n\n"
    )

    assert result.exit_code == 0
    assert "plan for session" in result.output.lower()
    assert "aborted" in result.output.lower()
    assert asyncio.run(_get_status(config_path, action_id)) == "committed"  # untouched


def test_doctor_fails_on_unparseable_config(tmp_path: Path) -> None:
    bad_config = tmp_path / "backstep.toml"
    bad_config.write_text("this is not [ valid toml", encoding="utf-8")

    result = runner.invoke(app, ["doctor", "--config", str(bad_config)])

    assert result.exit_code != 0
    assert "config" in result.output.lower()


def test_doctor_passes_with_no_servers_and_no_journal_yet(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)

    result = runner.invoke(app, ["doctor", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "journal" in result.output.lower()


def test_doctor_no_config_present(tmp_path: Path) -> None:
    """No backstep.toml at all: config check FAILS with a path + backstep init
    pointer, but journal/adapter/eval:postgres-mode checks still run, and
    config-dependent checks (servers, unclassified tools) report SKIPPED."""
    missing_path = tmp_path / "backstep.toml"

    result = runner.invoke(app, ["doctor", "--config", str(missing_path)])

    assert result.exit_code != 0
    assert "backstep init" in result.output
    assert "backstep.example.toml" in result.output
    assert "SKIPPED" in result.output  # upstream servers / unclassified tools
    # journal check must have run (its name appears in the table) even though
    # config failed to load
    assert "journal" in result.output.lower()


def test_doctor_malformed_config(tmp_path: Path) -> None:
    bad = tmp_path / "backstep.toml"
    bad.write_text("this is not [ valid toml", encoding="utf-8")

    result = runner.invoke(app, ["doctor", "--config", str(bad)])

    assert result.exit_code != 0
    assert "failed to parse" in result.output.lower() or "config" in result.output.lower()
    assert "SKIPPED" in result.output
    assert "journal" in result.output.lower()


def test_doctor_valid_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A config that references the postgres adapter (like backstep.example.toml,
    with an inert/unreachable DSN and no servers so the test doesn't need a
    real MCP server): every config-dependent check should run and produce a
    PASSED/FAILED verdict, never SKIPPED. BACKSTEP_PG_DSN is also set (to the
    same inert DSN) so the config-independent env-adapter check runs instead
    of legitimately reporting SKIPPED for being unset."""
    monkeypatch.setenv("BACKSTEP_PG_DSN", "postgresql://postgres:postgres@127.0.0.1:1/postgres")
    journal_path = (tmp_path / "journal.db").as_posix()
    config_path = tmp_path / "backstep.toml"
    config_path.write_text(
        f"""
journal_path = "{journal_path}"
unknown_tool_behavior = "warn"

[adapters.postgres]
dsn = "postgresql://postgres:postgres@127.0.0.1:1/postgres"
max_captured_rows = 1000
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["doctor", "--config", str(config_path)])

    assert "SKIPPED" not in result.output  # all config-dependent checks ran
    assert "journal" in result.output.lower()


def test_doctor_flags_unfilled_placeholders_without_spawning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server copied from backstep.example.toml with `<...>` placeholders left
    in: fail with a pointer at each placeholder instead of spawning it (on
    Windows, cmd.exe parses `<your-project-ref>` as a redirect and the spawn
    dies with an opaque TaskGroup error)."""
    from backstep import doctor

    async def _must_not_spawn(server: object) -> list[str]:
        raise AssertionError("server with unfilled placeholders was spawned")

    monkeypatch.setattr(doctor, "_list_server_tools", _must_not_spawn)
    journal_path = (tmp_path / "journal.db").as_posix()
    config_path = tmp_path / "backstep.toml"
    config_path.write_text(
        f"""
journal_path = "{journal_path}"

[[servers]]
name = "supabase"
command = "npx"
args = ["-y", "@supabase/mcp-server-supabase@latest", "--project-ref=<your-project-ref>"]

[servers.env]
SUPABASE_ACCESS_TOKEN = "<your-access-token>"
""",
        encoding="utf-8",
    )

    report = asyncio.run(doctor.run_doctor(config_path))

    [check] = [c for c in report.checks if c.name == "server:supabase"]
    assert check.status == "failed"
    assert "<your-project-ref>" in check.detail
    assert "SUPABASE_ACCESS_TOKEN" in check.detail
    assert "<your-access-token>" not in check.detail  # env values may be secrets
    assert str(config_path) in check.detail


def test_doctor_reports_postgres_test_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backstep.eval import TEST_PG_DSN_ENV_VAR

    config_path = _write_config(tmp_path)

    monkeypatch.delenv(TEST_PG_DSN_ENV_VAR, raising=False)
    testcontainers_result = runner.invoke(app, ["doctor", "--config", str(config_path)])
    assert "testcontainers" in testcontainers_result.output.lower()

    monkeypatch.setenv(TEST_PG_DSN_ENV_VAR, "postgresql://example/db")
    external_result = runner.invoke(app, ["doctor", "--config", str(config_path)])
    assert "external" in external_result.output.lower()
    assert TEST_PG_DSN_ENV_VAR in external_result.output


# -- eval ---------------------------------------------------------------
#
# These need real Postgres (backstep eval runs the actual scenarios), so
# they're skipped rather than failed when neither BACKSTEP_TEST_PG_DSN nor a
# working Docker daemon is available (see tests/pg_testing.py).
#
# Each run writes its JSON report to tmp_path via --out, so the committed
# eval_results.json is never rewritten by the test suite. Tests that assert on
# the eval table's text use `wide_console` so Rich can't wrap the notes column
# mid-phrase.


@pytest.fixture
def wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "console", Console(width=250))


@requires_postgres
def test_eval_unparseable_sql_no_traceback_by_default(tmp_path: Path, wide_console: None) -> None:
    """The unparseable_sql scenario's capture failure must not leak a raw
    traceback into the eval table's console output, and the notes column
    should show the clean, translated reason instead of the generic
    "no pre_state was captured" detail."""
    result = runner.invoke(app, ["eval", "--out", str(tmp_path / "eval_results.json")])

    assert result.exit_code == 0
    assert "Traceback (most recent call last)" not in result.output
    assert "UndefinedTableError" not in result.output
    assert "capture declined: unparseable statement" in result.output


@requires_postgres
def test_eval_verbose_restores_traceback(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """--verbose raises the postgres adapter's log level to DEBUG, which
    restores the capture-failure traceback (attached via exc_info=True) to
    the log record, even though it still doesn't get printed to the console
    directly by rich."""
    with caplog.at_level(logging.DEBUG, logger="backstep.adapters.postgres"):
        result = runner.invoke(
            app, ["eval", "--verbose", "--out", str(tmp_path / "eval_results.json")]
        )

    assert result.exit_code == 0
    assert any(
        "UndefinedTableError" in (record.exc_text or "") or record.exc_info
        for record in caplog.records
        if record.name == "backstep.adapters.postgres"
    )
