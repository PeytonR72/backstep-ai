"""Tests for backstep.cli."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from typer.testing import CliRunner

from backstep.cli import app
from backstep.journal import Journal

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
