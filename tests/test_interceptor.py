"""Tests for the reversibility-policy interceptor wired into backstep.proxy."""

import sys
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp import Client, StdioServerParameters, stdio_client
from mcp.shared.exceptions import MCPError

from backstep.adapters.base import AdapterRegistry, PreState, RevertResult
from backstep.config import BackstepConfig, ToolPolicy, UpstreamServer
from backstep.journal import ActionRecord, Journal
from backstep.proxy import InterceptorConfig, build_registry, build_server
from backstep.registry import ToolRegistry

FIXTURE = Path(__file__).parent / "fixtures" / "echo_server.py"


class FakeAdapter:
    name = "fake"

    def __init__(self, *, fail_capture: bool = False) -> None:
        self.fail_capture = fail_capture
        self.capture_calls: list[tuple[str, dict[str, Any]]] = []
        self.revert_calls: list[ActionRecord] = []

    async def capture(self, tool_name: str, args: dict[str, Any]) -> PreState | None:
        self.capture_calls.append((tool_name, args))
        if self.fail_capture:
            raise RuntimeError("simulated capture failure")
        return PreState(kind="fake", data={"seen_args": args})

    async def revert(self, entry: ActionRecord) -> RevertResult:
        self.revert_calls.append(entry)
        return RevertResult(ok=True, detail="reverted by fake adapter")

    def fingerprint(self, state: dict[str, Any]) -> str:
        return f"fake:{len(state)}"


def _config(tools: list[ToolPolicy], unknown_tool_behavior: str = "warn") -> BackstepConfig:
    return BackstepConfig(
        unknown_tool_behavior=unknown_tool_behavior,  # type: ignore[arg-type]
        servers=[
            UpstreamServer(name="echo", command=sys.executable, args=[str(FIXTURE)], tools=tools),
        ],
    )


@asynccontextmanager
async def _running_proxy(
    config: BackstepConfig,
    tmp_path: Path,
    *,
    adapter_registry: AdapterRegistry | None = None,
    allow_irreversible: bool = False,
) -> AsyncIterator[tuple[Client, Journal, str]]:
    journal = Journal()
    await journal.open(tmp_path / "journal.db")
    session_id = await journal.start_session()
    tool_registry = ToolRegistry(config)

    try:
        async with AsyncExitStack() as exit_stack:
            proxy_registry = await build_registry(config, exit_stack)
            interceptor = InterceptorConfig(
                tool_registry=tool_registry,
                journal=journal,
                adapter_registry=adapter_registry or AdapterRegistry(),
                session_id=session_id,
                allow_irreversible=allow_irreversible,
            )
            server = build_server(proxy_registry, interceptor)
            async with Client(server) as client:
                yield client, journal, session_id
    finally:
        await journal.close()


async def test_r0_forwards_without_journaling(tmp_path: Path) -> None:
    config = _config([ToolPolicy(name="echo", reversibility="R0")])

    async with _running_proxy(config, tmp_path) as (client, journal, session_id):
        result = await client.call_tool("echo", {"text": "hi"})

        assert result.is_error is False
        assert result.content[0].text == "hi"  # type: ignore[union-attr]
        assert await journal.list_actions(session_id=session_id) == []


async def test_unclassified_tool_is_blocked_when_deny(tmp_path: Path) -> None:
    config = _config([], unknown_tool_behavior="deny")

    async with _running_proxy(config, tmp_path) as (client, journal, session_id):
        with pytest.raises(MCPError):
            await client.call_tool("echo", {"text": "hi"})

        assert await journal.list_actions(session_id=session_id) == []


async def test_r4_tool_is_refused_and_journaled_unrevertable(tmp_path: Path) -> None:
    config = _config([ToolPolicy(name="echo", reversibility="R4")])

    async with _running_proxy(config, tmp_path) as (client, journal, session_id):
        result = await client.call_tool("echo", {"text": "hi"})

        assert result.is_error is True
        assert "was not called" in result.content[0].text  # type: ignore[union-attr]

        actions = await journal.list_actions(session_id=session_id)
        assert len(actions) == 1
        assert actions[0].status == "unrevertable"
        assert actions[0].pre_state is None
        assert actions[0].post_state is None


async def test_require_confirmation_tool_is_refused_even_if_not_r4(tmp_path: Path) -> None:
    config = _config(
        [ToolPolicy(name="echo", reversibility="R2", require_confirmation=True)]
    )

    async with _running_proxy(config, tmp_path) as (client, journal, session_id):
        result = await client.call_tool("echo", {"text": "hi"})

        assert result.is_error is True
        actions = await journal.list_actions(session_id=session_id)
        assert len(actions) == 1
        assert actions[0].status == "unrevertable"


async def test_allow_irreversible_bypasses_refusal_and_forwards(tmp_path: Path) -> None:
    config = _config([ToolPolicy(name="echo", reversibility="R4")])

    proxy = _running_proxy(config, tmp_path, allow_irreversible=True)
    async with proxy as (client, journal, session_id):
        result = await client.call_tool("echo", {"text": "hi"})

        assert result.is_error is False
        assert result.content[0].text == "hi"  # type: ignore[union-attr]

        actions = await journal.list_actions(session_id=session_id)
        assert len(actions) == 1
        assert actions[0].status == "unrevertable"
        assert actions[0].error == "R4: this action is irreversible by definition"


async def test_r1_r2_tool_captures_pre_state_and_commits(tmp_path: Path) -> None:
    config = _config(
        [ToolPolicy(name="echo", reversibility="R2", adapter="fake")]
    )
    adapter = FakeAdapter()
    adapters = AdapterRegistry()
    adapters.register(adapter)

    proxy = _running_proxy(config, tmp_path, adapter_registry=adapters)
    async with proxy as (client, journal, session_id):
        result = await client.call_tool("echo", {"text": "hi"})

        assert result.is_error is False
        assert adapter.capture_calls == [("echo", {"text": "hi"})]

        actions = await journal.list_actions(session_id=session_id)
        assert len(actions) == 1
        action = actions[0]
        assert action.status == "committed"
        assert action.pre_state == {"kind": "fake", "data": {"seen_args": {"text": "hi"}}}
        assert action.post_state is not None
        assert action.post_fingerprint == adapter.fingerprint(action.post_state)


async def test_capture_failure_does_not_block_the_call(tmp_path: Path) -> None:
    config = _config(
        [ToolPolicy(name="echo", reversibility="R2", adapter="fake")]
    )
    adapter = FakeAdapter(fail_capture=True)
    adapters = AdapterRegistry()
    adapters.register(adapter)

    proxy = _running_proxy(config, tmp_path, adapter_registry=adapters)
    async with proxy as (client, journal, session_id):
        result = await client.call_tool("echo", {"text": "hi"})

        assert result.is_error is False
        assert result.content[0].text == "hi"  # type: ignore[union-attr]

        actions = await journal.list_actions(session_id=session_id)
        assert len(actions) == 1
        action = actions[0]
        assert action.status == "committed"
        assert action.pre_state is None
        assert action.error is not None
        assert "capture_failed" in action.error


async def test_r3_tool_journals_with_null_pre_state_and_no_capture(tmp_path: Path) -> None:
    config = _config([ToolPolicy(name="echo", reversibility="R3", adapter="fake")])
    adapter = FakeAdapter()
    adapters = AdapterRegistry()
    adapters.register(adapter)

    proxy = _running_proxy(config, tmp_path, adapter_registry=adapters)
    async with proxy as (client, journal, session_id):
        result = await client.call_tool("echo", {"text": "hi"})

        assert result.is_error is False
        assert adapter.capture_calls == []  # R3 never captures pre-state

        actions = await journal.list_actions(session_id=session_id)
        assert len(actions) == 1
        assert actions[0].status == "committed"
        assert actions[0].pre_state is None
        assert actions[0].post_state is not None


async def test_upstream_error_is_journaled_failed_and_returned_verbatim(tmp_path: Path) -> None:
    config = _config([ToolPolicy(name="fail", reversibility="R2")])

    async with _running_proxy(config, tmp_path) as (client, journal, session_id):
        direct_params = StdioServerParameters(command=sys.executable, args=[str(FIXTURE)])
        async with AsyncExitStack() as stack:
            direct_client = await stack.enter_async_context(Client(stdio_client(direct_params)))
            direct_result = await direct_client.call_tool("fail", {"message": "kaboom"})

        proxied_result = await client.call_tool("fail", {"message": "kaboom"})

        assert proxied_result.is_error is True
        assert proxied_result.content == direct_result.content

        actions = await journal.list_actions(session_id=session_id)
        assert len(actions) == 1
        assert actions[0].status == "failed"
        assert actions[0].error is not None
