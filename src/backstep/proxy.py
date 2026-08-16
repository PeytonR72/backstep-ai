"""MCP proxy that sits between an MCP client and upstream MCP servers.

Aggregates tools/resources/prompts from every configured upstream server and
forwards calls to them. Tool calls are additionally routed through an
interceptor that consults `backstep.registry.ToolRegistry` for each tool's
reversibility policy and journals write operations via `backstep.journal`
before/after forwarding — see `_intercept_call_tool`. The interceptor never
alters or swallows the upstream response: if journaling itself fails, that
failure is logged and the call still proceeds as a plain passthrough.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import anyio
import mcp_types as types
from mcp import Client, StdioServerParameters, stdio_client, stdio_server
from mcp.server import Server, ServerRequestContext
from mcp.shared.exceptions import MCPError

from backstep import __version__
from backstep.adapters.base import Adapter, AdapterRegistry, PreState, canonical_fingerprint
from backstep.config import BackstepConfig, ToolPolicy, UpstreamServer, load_config
from backstep.journal import Journal
from backstep.registry import ToolRegistry

logger = logging.getLogger("backstep.proxy")

_T = TypeVar("_T")


def _configure_logging() -> None:
    """Route all logging to stderr; stdout is reserved for the MCP wire protocol."""
    level_name = os.environ.get("BACKSTEP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@dataclass
class ProxyRegistry:
    """Aggregated view of every upstream server's tools, resources, and prompts."""

    connections: dict[str, Client] = field(default_factory=dict)

    tool_routes: dict[str, tuple[str, str]] = field(default_factory=dict)
    """exposed tool name -> (server_name, original_tool_name)"""
    tool_definitions: dict[str, types.Tool] = field(default_factory=dict)

    resource_routes: dict[str, str] = field(default_factory=dict)
    """resource uri -> server_name"""
    resource_definitions: dict[str, types.Resource] = field(default_factory=dict)

    prompt_routes: dict[str, tuple[str, str]] = field(default_factory=dict)
    """exposed prompt name -> (server_name, original_prompt_name)"""
    prompt_definitions: dict[str, types.Prompt] = field(default_factory=dict)

    has_tools_capability: bool = False
    has_resources_capability: bool = False
    has_prompts_capability: bool = False


async def _connect_upstream(server_config: UpstreamServer, exit_stack: AsyncExitStack) -> Client:
    params = StdioServerParameters(
        command=server_config.command,
        args=server_config.args,
        env=server_config.env or None,
    )
    client = Client(stdio_client(params))
    await exit_stack.enter_async_context(client)
    logger.info("Connected upstream server %r via %r", server_config.name, server_config.command)
    return client


async def _list_all_tools(client: Client) -> list[types.Tool]:
    tools: list[types.Tool] = []
    cursor: str | None = None
    while True:
        result = await client.list_tools(cursor=cursor)
        tools.extend(result.tools)
        cursor = result.next_cursor
        if cursor is None:
            return tools


async def _list_all_resources(client: Client) -> list[types.Resource]:
    resources: list[types.Resource] = []
    cursor: str | None = None
    while True:
        result = await client.list_resources(cursor=cursor)
        resources.extend(result.resources)
        cursor = result.next_cursor
        if cursor is None:
            return resources


async def _list_all_prompts(client: Client) -> list[types.Prompt]:
    prompts: list[types.Prompt] = []
    cursor: str | None = None
    while True:
        result = await client.list_prompts(cursor=cursor)
        prompts.extend(result.prompts)
        cursor = result.next_cursor
        if cursor is None:
            return prompts


def _register_tool(registry: ProxyRegistry, server_name: str, tool: types.Tool) -> None:
    exposed_name = tool.name
    if exposed_name in registry.tool_routes:
        exposed_name = f"{server_name}__{tool.name}"
        if exposed_name in registry.tool_routes:
            logger.warning(
                "Tool name collision could not be resolved for %r from server %r; dropping it",
                tool.name,
                server_name,
            )
            return
    registry.tool_routes[exposed_name] = (server_name, tool.name)
    registry.tool_definitions[exposed_name] = tool.model_copy(update={"name": exposed_name})


def _register_prompt(registry: ProxyRegistry, server_name: str, prompt: types.Prompt) -> None:
    exposed_name = prompt.name
    if exposed_name in registry.prompt_routes:
        exposed_name = f"{server_name}__{prompt.name}"
        if exposed_name in registry.prompt_routes:
            logger.warning(
                "Prompt name collision could not be resolved for %r from server %r; dropping it",
                prompt.name,
                server_name,
            )
            return
    registry.prompt_routes[exposed_name] = (server_name, prompt.name)
    registry.prompt_definitions[exposed_name] = prompt.model_copy(update={"name": exposed_name})


def _register_resource(registry: ProxyRegistry, server_name: str, resource: types.Resource) -> None:
    if resource.uri in registry.resource_routes:
        logger.warning(
            "Resource uri collision: %r is already served by %r; ignoring the copy from %r",
            resource.uri,
            registry.resource_routes[resource.uri],
            server_name,
        )
        return
    registry.resource_routes[resource.uri] = server_name
    registry.resource_definitions[resource.uri] = resource


async def build_registry(config: BackstepConfig, exit_stack: AsyncExitStack) -> ProxyRegistry:
    """Connect to every configured upstream server and aggregate their capabilities.

    Upstream subprocesses are entered onto `exit_stack`, so closing it tears
    down every connection (and its subprocess) in reverse order.
    """
    registry = ProxyRegistry()

    for server_config in config.servers:
        client = await _connect_upstream(server_config, exit_stack)
        registry.connections[server_config.name] = client
        capabilities = client.server_capabilities

        if capabilities.tools is not None:
            registry.has_tools_capability = True
            for tool in await _list_all_tools(client):
                _register_tool(registry, server_config.name, tool)

        if capabilities.resources is not None:
            registry.has_resources_capability = True
            for resource in await _list_all_resources(client):
                _register_resource(registry, server_config.name, resource)

        if capabilities.prompts is not None:
            registry.has_prompts_capability = True
            for prompt in await _list_all_prompts(client):
                _register_prompt(registry, server_config.name, prompt)

    return registry


@dataclass
class InterceptorConfig:
    """Everything `_intercept_call_tool` needs beyond the tool being called."""

    tool_registry: ToolRegistry
    journal: Journal
    adapter_registry: AdapterRegistry
    session_id: str
    allow_irreversible: bool = False


async def _best_effort(coro: Awaitable[_T], description: str) -> _T | None:
    """Await `coro`, logging (not raising) on failure.

    Journaling must never be able to break or alter the tool call it is
    observing; every journal write in the interceptor goes through this.
    """
    try:
        return await coro
    except Exception:
        logger.exception("backstep journaling step failed (%s); continuing without it", description)
        return None


def _result_error_text(result: types.CallToolResult) -> str:
    parts = [text for block in result.content if (text := getattr(block, "text", None)) is not None]
    return "; ".join(parts) if parts else "tool call returned is_error=True"


def _refusal_result(exposed_name: str, policy: ToolPolicy) -> types.CallToolResult:
    reasons = [f"reversibility={policy.reversibility}"]
    if policy.require_confirmation:
        reasons.append("require_confirmation=true")
    message = (
        f"Tool {exposed_name!r} was not called ({', '.join(reasons)}). This action "
        "cannot be undone once performed, so it requires explicit human approval. "
        "A human can grant that by setting require_confirmation = false for this "
        "tool in backstep.toml, or by restarting backstep with "
        "`backstep serve --allow-irreversible`."
    )
    content: list[types.ContentBlock] = [types.TextContent(type="text", text=message)]
    return types.CallToolResult(content=content, is_error=True)


async def _journal_refusal(
    interceptor: InterceptorConfig,
    server_name: str,
    upstream_tool_name: str,
    policy: ToolPolicy,
    args: dict[str, Any],
) -> None:
    action_id = await interceptor.journal.begin_action(
        session_id=interceptor.session_id,
        server_name=server_name,
        tool_name=upstream_tool_name,
        reversibility=policy.reversibility,
        args=args,
        adapter=policy.adapter,
        pre_state=None,
    )
    await interceptor.journal.mark_unrevertable(action_id, "refused: requires human confirmation")


async def _capture_post_state(
    adapter: Adapter | None, pre_state_obj: PreState | None, result: types.CallToolResult
) -> dict[str, Any]:
    """Snapshot state right after a successful call, for journaling and drift detection.

    Prefers an adapter-native snapshot (`adapter.capture_post`, if the adapter
    implements it) over the raw MCP result: a post-state built the same way
    the adapter builds its own pre-states is directly comparable, later, to
    whatever `backstep.adapters.postgres.current_fingerprint` re-reads — the
    raw `CallToolResult` dump generally isn't (it's the tool's response, not
    the affected rows). Falls back to the raw result dump when the adapter
    has no such hook, or it declines (returns `None`).
    """
    raw_result = result.model_dump(mode="json", exclude_none=True)
    capture_post = getattr(adapter, "capture_post", None)
    if adapter is not None and pre_state_obj is not None and capture_post is not None:
        adapter_post_state = await _best_effort(
            capture_post(pre_state_obj, raw_result), "capture_post"
        )
        if adapter_post_state is not None:
            return adapter_post_state
    return raw_result


async def _intercept_call_tool(
    interceptor: InterceptorConfig,
    exposed_name: str,
    server_name: str,
    upstream_tool_name: str,
    arguments: dict[str, Any] | None,
    forward: Callable[[], Awaitable[types.CallToolResult]],
) -> types.CallToolResult:
    """Apply reversibility policy to one tools/call, journaling as it goes.

    Steps (see backstep.registry.ToolRegistry.classify for policy derivation):
      R0                                -> forward, no journal.
      blocked (unclassified + "deny")   -> MCP-level error, no journal, no forward.
      R4 or require_confirmation        -> refuse (unless --allow-irreversible),
                                            journal the refusal as unrevertable.
      R1/R2                             -> best-effort adapter.capture(), then
                                            journal + forward; commit on success.
      R3                                -> journal with pre_state=None, then
                                            forward; commit on success.
      R4 (bypassed via --allow-irreversible) -> journal, forward, then always
                                            mark unrevertable (never "committed").
    Any exception raised by `forward()` propagates unchanged after being
    journaled as a failure.
    """
    policy = interceptor.tool_registry.classify(exposed_name)
    args = arguments or {}

    if policy.reversibility == "R0":
        return await forward()

    if policy.blocked:
        raise MCPError(
            code=types.INVALID_REQUEST,
            message=(
                f"Tool {exposed_name!r} is not classified in backstep.toml, and "
                'unknown_tool_behavior is "deny", so it is treated as irreversible '
                "(R4) and blocked outright. Ask a human to classify this tool "
                "(see `backstep init`) before it can be called."
            ),
        )

    needs_confirmation = policy.reversibility == "R4" or policy.require_confirmation
    if needs_confirmation and not interceptor.allow_irreversible:
        refusal = _refusal_result(exposed_name, policy)
        await _best_effort(
            _journal_refusal(interceptor, server_name, upstream_tool_name, policy, args),
            "journal refusal",
        )
        return refusal

    adapter: Adapter | None = None
    note: str | None = None
    if policy.adapter:
        adapter = interceptor.adapter_registry.get_adapter(policy.adapter)
        if adapter is None:
            note = f"capture_failed: adapter {policy.adapter!r} is not registered"
            logger.warning("Tool %r references unknown adapter %r", exposed_name, policy.adapter)

    pre_state_obj: PreState | None = None
    pre_state: dict[str, Any] | None = None
    if policy.reversibility in ("R1", "R2") and adapter is not None:
        try:
            pre_state_obj = await adapter.capture(exposed_name, args)
            if pre_state_obj is not None:
                pre_state = pre_state_obj.model_dump()
            else:
                note = "capture_failed: adapter could not capture pre-state for this call"
        except Exception as exc:  # adapter misbehavior must never block the call
            note = f"capture_failed: {exc}"
            logger.exception(
                "Adapter %r failed to capture pre-state for %r", policy.adapter, exposed_name
            )

    action_id = await _best_effort(
        interceptor.journal.begin_action(
            session_id=interceptor.session_id,
            server_name=server_name,
            tool_name=upstream_tool_name,
            reversibility=policy.reversibility,
            args=args,
            adapter=policy.adapter,
            pre_state=pre_state,
            note=note,
        ),
        "begin_action",
    )

    try:
        result = await forward()
    except Exception as exc:
        if action_id is not None:
            await _best_effort(interceptor.journal.fail_action(action_id, str(exc)), "fail_action")
        raise

    if action_id is None:
        return result

    if result.is_error:
        await _best_effort(
            interceptor.journal.fail_action(action_id, _result_error_text(result)), "fail_action"
        )
    elif policy.reversibility == "R4":
        await _best_effort(
            interceptor.journal.mark_unrevertable(
                action_id, "R4: this action is irreversible by definition"
            ),
            "mark_unrevertable",
        )
    else:
        post_state = await _capture_post_state(adapter, pre_state_obj, result)
        if adapter is not None:
            fingerprint = adapter.fingerprint(post_state)
        else:
            fingerprint = canonical_fingerprint(post_state)
        commit = interceptor.journal.commit_action(
            action_id, post_state=post_state, post_fingerprint=fingerprint
        )
        await _best_effort(commit, "commit_action")

    return result


def build_server(
    registry: ProxyRegistry, interceptor: InterceptorConfig | None = None
) -> Server[object]:
    """Build the lowlevel Server that fronts the aggregated upstream registry.

    Handlers close over `registry`/`interceptor` directly rather than going
    through per-request lifespan context, since both are built once at
    startup and never mutated afterwards. `interceptor=None` keeps tools/call
    a pure passthrough, e.g. for tests that don't care about journaling.
    """

    async def on_list_tools(
        _ctx: ServerRequestContext[object], _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=list(registry.tool_definitions.values()))

    async def on_call_tool(
        _ctx: ServerRequestContext[object], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        route = registry.tool_routes.get(params.name)
        if route is None:
            raise MCPError(code=types.INVALID_PARAMS, message=f"Unknown tool: {params.name}")
        server_name, upstream_tool_name = route
        client = registry.connections[server_name]

        async def forward() -> types.CallToolResult:
            return await client.call_tool(upstream_tool_name, params.arguments)

        if interceptor is None:
            return await forward()

        return await _intercept_call_tool(
            interceptor, params.name, server_name, upstream_tool_name, params.arguments, forward
        )

    async def on_list_resources(
        _ctx: ServerRequestContext[object], _params: types.PaginatedRequestParams | None
    ) -> types.ListResourcesResult:
        return types.ListResourcesResult(resources=list(registry.resource_definitions.values()))

    async def on_read_resource(
        _ctx: ServerRequestContext[object], params: types.ReadResourceRequestParams
    ) -> types.ReadResourceResult:
        server_name = registry.resource_routes.get(params.uri)
        if server_name is None:
            raise MCPError(code=types.INVALID_PARAMS, message=f"Unknown resource: {params.uri}")
        client = registry.connections[server_name]
        return await client.read_resource(params.uri)

    async def on_list_prompts(
        _ctx: ServerRequestContext[object], _params: types.PaginatedRequestParams | None
    ) -> types.ListPromptsResult:
        return types.ListPromptsResult(prompts=list(registry.prompt_definitions.values()))

    async def on_get_prompt(
        _ctx: ServerRequestContext[object], params: types.GetPromptRequestParams
    ) -> types.GetPromptResult:
        route = registry.prompt_routes.get(params.name)
        if route is None:
            raise MCPError(code=types.INVALID_PARAMS, message=f"Unknown prompt: {params.name}")
        server_name, upstream_prompt_name = route
        client = registry.connections[server_name]
        return await client.get_prompt(upstream_prompt_name, params.arguments)

    return Server(
        "backstep",
        version=__version__,
        on_list_tools=on_list_tools if registry.has_tools_capability else None,
        on_call_tool=on_call_tool if registry.has_tools_capability else None,
        on_list_resources=on_list_resources if registry.has_resources_capability else None,
        on_read_resource=on_read_resource if registry.has_resources_capability else None,
        on_list_prompts=on_list_prompts if registry.has_prompts_capability else None,
        on_get_prompt=on_get_prompt if registry.has_prompts_capability else None,
    )


async def _watch_for_shutdown_signals(cancel_scope: anyio.CancelScope) -> None:
    """Cancel `cancel_scope` on SIGTERM/SIGINT, where the platform supports it."""
    candidates = (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None))
    stop_signals = tuple(sig for sig in candidates if sig is not None)
    if not stop_signals:
        return
    try:
        with anyio.open_signal_receiver(*stop_signals) as signals:
            async for sig in signals:
                logger.info("Received signal %s; shutting down", sig)
                cancel_scope.cancel()
                return
    except NotImplementedError:
        logger.debug("Signal-based shutdown is not supported on this platform")


async def serve(config_path: str | Path, *, allow_irreversible: bool = False) -> None:
    """Load `config_path`, connect upstream servers, and serve the proxy over stdio.

    Opens the journal and a session for the lifetime of the process, and
    wires tools/call through `_intercept_call_tool` for every configured
    upstream. `allow_irreversible` mirrors `serve --allow-irreversible`.
    """
    _configure_logging()
    config = load_config(config_path)
    tool_registry = ToolRegistry(config)
    adapter_registry = AdapterRegistry()

    journal = Journal()
    await journal.open(config.journal_path)
    try:
        session_id = await journal.start_session(client_name="backstep-proxy")
        try:
            async with AsyncExitStack() as exit_stack:
                registry = await build_registry(config, exit_stack)
                interceptor = InterceptorConfig(
                    tool_registry=tool_registry,
                    journal=journal,
                    adapter_registry=adapter_registry,
                    session_id=session_id,
                    allow_irreversible=allow_irreversible,
                )
                server = build_server(registry, interceptor)
                init_options = server.create_initialization_options()

                async with (
                    stdio_server() as (read_stream, write_stream),
                    anyio.create_task_group() as tg,
                ):
                    tg.start_soon(_watch_for_shutdown_signals, tg.cancel_scope)
                    await server.run(read_stream, write_stream, init_options)
                    tg.cancel_scope.cancel()
        finally:
            await journal.end_session(session_id)
    finally:
        await journal.close()
