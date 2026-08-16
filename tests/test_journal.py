"""Tests for backstep.journal."""

import asyncio
from pathlib import Path

import pytest

from backstep.journal import ActionRecord, Journal


async def _make_journal(tmp_path: Path) -> Journal:
    journal = Journal()
    await journal.open(tmp_path / "journal.db")
    return journal


async def test_full_lifecycle(tmp_path: Path) -> None:
    journal = await _make_journal(tmp_path)
    try:
        session_id = await journal.start_session(client_name="test-client", label="demo")

        action_id = await journal.begin_action(
            session_id=session_id,
            server_name="filesystem",
            tool_name="write_file",
            reversibility="R2",
            args={"path": "/tmp/a.txt", "content": "hello"},
            adapter="filesystem",
            pre_state={"content": "old"},
        )

        pending = await journal.get_action(action_id)
        assert isinstance(pending, ActionRecord)
        assert pending.status == "pending"
        assert pending.seq == 1
        assert pending.pre_state == {"content": "old"}
        assert pending.post_state is None

        await journal.commit_action(
            action_id, post_state={"content": "hello"}, post_fingerprint="abc123"
        )
        committed = await journal.get_action(action_id)
        assert committed.status == "committed"
        assert committed.post_state == {"content": "hello"}
        assert committed.post_fingerprint == "abc123"
        # Fields that must never change after insert.
        assert committed.args == pending.args
        assert committed.pre_state == pending.pre_state
        assert committed.executed_at == pending.executed_at

        await journal.mark_reverted(action_id, note="reverted in test")
        reverted = await journal.get_action(action_id)
        assert reverted.status == "reverted"
        assert reverted.revert_note == "reverted in test"
        assert reverted.reverted_at is not None

        await journal.end_session(session_id)

        actions = await journal.list_actions(session_id=session_id)
        assert len(actions) == 1
        assert actions[0].id == action_id

        assert await journal.latest_session() == session_id
    finally:
        await journal.close()


async def test_fail_action(tmp_path: Path) -> None:
    journal = await _make_journal(tmp_path)
    try:
        session_id = await journal.start_session()
        action_id = await journal.begin_action(
            session_id=session_id,
            server_name="db",
            tool_name="delete_row",
            reversibility="R3",
            args={"id": 1},
        )
        await journal.fail_action(action_id, error="upstream timeout")
        record = await journal.get_action(action_id)
        assert record.status == "failed"
        assert record.error == "upstream timeout"
        assert record.pre_state is None
    finally:
        await journal.close()


async def test_mark_unrevertable(tmp_path: Path) -> None:
    journal = await _make_journal(tmp_path)
    try:
        session_id = await journal.start_session()
        action_id = await journal.begin_action(
            session_id=session_id,
            server_name="email",
            tool_name="send_email",
            reversibility="R4",
            args={"to": "a@example.com"},
        )
        await journal.mark_unrevertable(action_id, reason="emails cannot be unsent")
        record = await journal.get_action(action_id)
        assert record.status == "unrevertable"
        assert record.error == "emails cannot be unsent"
    finally:
        await journal.close()


async def test_double_revert_raises(tmp_path: Path) -> None:
    journal = await _make_journal(tmp_path)
    try:
        session_id = await journal.start_session()
        action_id = await journal.begin_action(
            session_id=session_id,
            server_name="filesystem",
            tool_name="write_file",
            reversibility="R2",
            args={"path": "/tmp/a.txt"},
        )
        await journal.mark_reverted(action_id, note="first revert")
        with pytest.raises(ValueError):
            await journal.mark_reverted(action_id, note="second revert")
    finally:
        await journal.close()


async def test_get_action_missing_raises(tmp_path: Path) -> None:
    journal = await _make_journal(tmp_path)
    try:
        with pytest.raises(KeyError):
            await journal.get_action("does-not-exist")
    finally:
        await journal.close()


async def test_list_actions_filters_by_status(tmp_path: Path) -> None:
    journal = await _make_journal(tmp_path)
    try:
        session_id = await journal.start_session()
        a1 = await journal.begin_action(
            session_id=session_id,
            server_name="fs",
            tool_name="write_file",
            reversibility="R2",
            args={},
        )
        a2 = await journal.begin_action(
            session_id=session_id,
            server_name="fs",
            tool_name="write_file",
            reversibility="R2",
            args={},
        )
        await journal.commit_action(a1, post_state=None, post_fingerprint=None)
        await journal.fail_action(a2, error="boom")

        committed = await journal.list_actions(session_id=session_id, status="committed")
        failed = await journal.list_actions(session_id=session_id, status="failed")

        assert [a.id for a in committed] == [a1]
        assert [a.id for a in failed] == [a2]
    finally:
        await journal.close()


async def test_latest_session_returns_none_when_empty(tmp_path: Path) -> None:
    journal = await _make_journal(tmp_path)
    try:
        assert await journal.latest_session() is None
    finally:
        await journal.close()


async def test_seq_monotonic_under_concurrent_begin_action(tmp_path: Path) -> None:
    journal = await _make_journal(tmp_path)
    try:
        session_id = await journal.start_session()

        async def begin(i: int) -> str:
            return await journal.begin_action(
                session_id=session_id,
                server_name="fs",
                tool_name="write_file",
                reversibility="R2",
                args={"i": i},
            )

        action_ids = await asyncio.gather(*(begin(i) for i in range(25)))
        assert len(set(action_ids)) == 25

        actions = await journal.list_actions(session_id=session_id, limit=100)
        seqs = sorted(a.seq for a in actions)
        assert seqs == list(range(1, 26))
    finally:
        await journal.close()


async def test_open_expands_home_and_creates_parents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("HOME", str(fake_home))

    journal = Journal()
    await journal.open("~/.backstep/nested/journal.db")
    try:
        assert (fake_home / ".backstep" / "nested" / "journal.db").exists()
    finally:
        await journal.close()
