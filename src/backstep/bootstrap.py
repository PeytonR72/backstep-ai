"""LLM-assisted bootstrapping of tool reversibility policies for `backstep.toml`.

`backstep init` connects to every configured upstream, asks an LLM to propose
a reversibility class per tool, and writes the proposals into `backstep.toml`
as `[[servers.tools]]` entries. This is the only place in the codebase an LLM
is ever called; `backstep.registry.ToolRegistry` reads the proposals back
deterministically at runtime, with no further LLM involvement.

Proposals are never trusted implicitly: each written entry carries a comment
naming its confidence, and the file gets a header reminding whoever reads it
that a human owns the final classification.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import tomlkit
from pydantic import BaseModel, Field, TypeAdapter

from backstep.config import BackstepConfig, ReversibilityClass, load_config
from backstep.proxy import ProxyRegistry, build_registry

DEFAULT_MODEL = "claude-sonnet-4-6"
BATCH_SIZE = 10

SYSTEM_PROMPT = """You are classifying MCP tool calls by how reversible their side effects are.

Reversibility classes:
R0 read-only, no state change
R1 fully reversible, prior state can be captured and restored
R2 reversible with loss, e.g. delete where identity or metadata is lost
R3 compensable only, needs a semantic inverse rather than a restore
R4 irreversible

You will receive a JSON object with a "tools" array; each entry has "name",
"description", and "input_schema" for one MCP tool. Respond with JSON only:
a single JSON object mapping each tool's exact name to
{"reversibility": "R0"|"R1"|"R2"|"R3"|"R4", "reasoning": "<one sentence>", "confidence": <0..1>}.
No prose, no markdown code fences, no keys other than the tool names."""

HEADER_SENTINEL = "Tool reversibility classifications in this file were proposed by `backstep init`"
HEADER = f"""# {HEADER_SENTINEL} using an LLM.
# These are proposals, not guarantees. You, the human operator, own the
# final classification — review every entry, especially R3/R4, before
# trusting it in production.
"""


@dataclass(frozen=True)
class ToolInfo:
    exposed_name: str
    server_name: str
    description: str | None
    input_schema: dict[str, Any]


class ToolClassification(BaseModel):
    reversibility: ReversibilityClass
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)


class _MessagesClient(Protocol):
    async def create(self, **kwargs: Any) -> Any: ...


class AnthropicClient(Protocol):
    """The minimal shape `classify_tools` needs; `anthropic.AsyncAnthropic` satisfies it."""

    @property
    def messages(self) -> _MessagesClient: ...


def _default_client() -> Any:
    """Build the real Anthropic client; typed `Any` since its full API dwarfs `AnthropicClient`."""
    import anthropic

    return anthropic.AsyncAnthropic()


def _chunked(items: Sequence[ToolInfo], size: int) -> Iterator[Sequence[ToolInfo]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _extract_text(message: Any) -> str:
    parts = [block.text for block in message.content if getattr(block, "text", None) is not None]
    return "".join(parts)


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[len("json") :]
        stripped = stripped.strip()
    result: dict[str, Any] = json.loads(stripped)
    return result


async def _classify_batch(
    client: AnthropicClient, model: str, batch: Sequence[ToolInfo]
) -> dict[str, ToolClassification]:
    payload = {
        "tools": [
            {
                "name": t.exposed_name,
                "description": t.description or "",
                "input_schema": t.input_schema,
            }
            for t in batch
        ]
    }
    message = await client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(payload)}],
    )
    parsed = _parse_json_object(_extract_text(message))
    adapter: TypeAdapter[dict[str, ToolClassification]] = TypeAdapter(dict[str, ToolClassification])
    return adapter.validate_python(parsed)


async def classify_tools(
    tools: Sequence[ToolInfo],
    *,
    client: AnthropicClient | None = None,
    model: str = DEFAULT_MODEL,
    batch_size: int = BATCH_SIZE,
) -> dict[str, ToolClassification]:
    """Classify `tools` via the Anthropic API, batching ~`batch_size` tools per call."""
    if not tools:
        return {}
    anthropic_client = client or _default_client()
    results: dict[str, ToolClassification] = {}
    for batch in _chunked(tools, batch_size):
        results.update(await _classify_batch(anthropic_client, model, batch))
    return results


def _tool_infos_from_registry(registry: ProxyRegistry) -> list[ToolInfo]:
    infos = []
    for exposed_name, tool in registry.tool_definitions.items():
        server_name, _original_name = registry.tool_routes[exposed_name]
        infos.append(
            ToolInfo(
                exposed_name=exposed_name,
                server_name=server_name,
                description=tool.description,
                input_schema=tool.input_schema,
            )
        )
    return infos


def _servers_to_populate(config: BackstepConfig, *, force: bool) -> tuple[set[str], list[str]]:
    """Split configured servers into those to (re)classify and those to leave alone."""
    populate: set[str] = set()
    skipped: list[str] = []
    for server in config.servers:
        if server.tools and not force:
            skipped.append(server.name)
        else:
            populate.add(server.name)
    return populate, skipped


def apply_classifications(
    doc_text: str,
    tool_infos: Sequence[ToolInfo],
    classifications: dict[str, ToolClassification],
    servers_to_populate: set[str],
) -> str:
    """Return `doc_text` with a `[[servers.tools]]` proposal written for each classified tool.

    Only servers in `servers_to_populate` are touched; every other server's
    existing TOML (including any hand-edited `tools` entries) is left as-is.
    """
    doc = tomlkit.parse(doc_text)
    servers_aot = doc.get("servers")
    if servers_aot is None:
        return doc_text

    by_server: dict[str, list[tuple[ToolInfo, ToolClassification]]] = {}
    for info in tool_infos:
        if info.server_name not in servers_to_populate:
            continue
        classification = classifications.get(info.exposed_name)
        if classification is None:
            continue
        by_server.setdefault(info.server_name, []).append((info, classification))

    for server_table in servers_aot:
        entries = by_server.get(server_table.get("name"))
        if not entries:
            continue

        tools_aot = tomlkit.aot()
        for info, classification in entries:
            table = tomlkit.table()
            table.add(
                tomlkit.comment(
                    f"proposed by backstep init, confidence {classification.confidence:.2f}"
                    " — review before trusting"
                )
            )
            table.add("name", info.exposed_name)
            table.add("reversibility", classification.reversibility)
            tools_aot.append(table)
        server_table["tools"] = tools_aot

    text = tomlkit.dumps(doc)
    if HEADER_SENTINEL not in text:
        text = HEADER + "\n" + text
    return text


@dataclass(frozen=True)
class InitResult:
    tool_infos: list[ToolInfo] = field(default_factory=list)
    classifications: dict[str, ToolClassification] = field(default_factory=dict)
    populated_servers: set[str] = field(default_factory=set)
    skipped_servers: list[str] = field(default_factory=list)


async def run_init(
    config_path: str | Path,
    *,
    force: bool = False,
    client: AnthropicClient | None = None,
    model: str = DEFAULT_MODEL,
) -> InitResult:
    """Discover upstream tools, classify them, and write proposals into `config_path`."""
    path = Path(config_path)
    config = load_config(path)
    servers_to_populate, skipped_servers = _servers_to_populate(config, force=force)

    async with AsyncExitStack() as exit_stack:
        registry = await build_registry(config, exit_stack)
        all_infos = _tool_infos_from_registry(registry)
        tool_infos = [info for info in all_infos if info.server_name in servers_to_populate]

    classifications = await classify_tools(tool_infos, client=client, model=model)

    if classifications:
        doc_text = path.read_text(encoding="utf-8")
        new_text = apply_classifications(doc_text, tool_infos, classifications, servers_to_populate)
        path.write_text(new_text, encoding="utf-8")

    return InitResult(
        tool_infos=tool_infos,
        classifications=classifications,
        populated_servers=servers_to_populate,
        skipped_servers=skipped_servers,
    )
