"""Tests for backstep.proxy."""

import sys
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import Client, StdioServerParameters, stdio_client

from backstep.config import BackstepConfig, UpstreamServer
from backstep.proxy import build_registry, build_server

FIXTURE = Path(__file__).parent / "fixtures" / "echo_server.py"


def _upstream(name: str) -> UpstreamServer:
    return UpstreamServer(name=name, command=sys.executable, args=[str(FIXTURE)])


async def test_tools_list_aggregates_with_prefixing() -> None:
    config = BackstepConfig(servers=[_upstream("echo1"), _upstream("echo2")])

    async with AsyncExitStack() as exit_stack:
        registry = await build_registry(config, exit_stack)

        assert registry.has_tools_capability is True
        assert registry.tool_routes["echo"] == ("echo1", "echo")
        assert registry.tool_routes["fail"] == ("echo1", "fail")
        assert registry.tool_routes["echo2__echo"] == ("echo2", "echo")
        assert registry.tool_routes["echo2__fail"] == ("echo2", "fail")

        server = build_server(registry)
        async with Client(server) as proxy_client:
            listing = await proxy_client.list_tools()
            names = {tool.name for tool in listing.tools}
            assert names == {"echo", "fail", "echo2__echo", "echo2__fail"}


async def test_call_tool_round_trips_success_and_error_unchanged() -> None:
    config = BackstepConfig(servers=[_upstream("echo1"), _upstream("echo2")])

    async with AsyncExitStack() as exit_stack:
        # A direct, un-proxied connection to compare against.
        direct_params = StdioServerParameters(command=sys.executable, args=[str(FIXTURE)])
        direct_client = Client(stdio_client(direct_params))
        await exit_stack.enter_async_context(direct_client)
        direct_success = await direct_client.call_tool("echo", {"text": "hello there"})
        direct_failure = await direct_client.call_tool("fail", {"message": "kaboom"})

        registry = await build_registry(config, exit_stack)
        server = build_server(registry)

        async with Client(server) as proxy_client:
            proxied_success = await proxy_client.call_tool("echo", {"text": "hello there"})
            assert proxied_success.is_error is False
            assert proxied_success.content == direct_success.content

            proxied_failure = await proxy_client.call_tool("fail", {"message": "kaboom"})
            assert proxied_failure.is_error is True
            assert proxied_failure.content == direct_failure.content

            # Routing through the collision-prefixed name reaches the same tool.
            prefixed_success = await proxy_client.call_tool("echo2__echo", {"text": "again"})
            assert prefixed_success.is_error is False
            assert prefixed_success.content[0].text == "again"  # type: ignore[union-attr]
