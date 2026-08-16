"""Tests for backstep.bootstrap, using a mocked Anthropic client."""

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backstep.bootstrap import (
    HEADER_SENTINEL,
    ToolClassification,
    ToolInfo,
    apply_classifications,
    classify_tools,
    run_init,
)
from backstep.config import load_config

FIXTURE = Path(__file__).parent / "fixtures" / "echo_server.py"


@dataclass
class _TextBlock:
    text: str


@dataclass
class _FakeMessage:
    content: list[_TextBlock]


class _FakeMessages:
    """Records every call and answers with a classification for each requested tool."""

    def __init__(self, reversibility_by_tool: dict[str, str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._reversibility_by_tool = reversibility_by_tool or {}

    async def create(self, **kwargs: Any) -> _FakeMessage:
        self.calls.append(kwargs)
        requested = json.loads(kwargs["messages"][0]["content"])["tools"]
        response = {
            tool["name"]: {
                "reversibility": self._reversibility_by_tool.get(tool["name"], "R1"),
                "reasoning": f"stubbed classification for {tool['name']}",
                "confidence": 0.75,
            }
            for tool in requested
        }
        return _FakeMessage(content=[_TextBlock(text=json.dumps(response))])


class _FakeClient:
    def __init__(self, reversibility_by_tool: dict[str, str] | None = None) -> None:
        self.messages = _FakeMessages(reversibility_by_tool)


def _tool_info(name: str, server: str = "echo") -> ToolInfo:
    return ToolInfo(
        exposed_name=name, server_name=server, description=f"the {name} tool", input_schema={}
    )


async def test_classify_tools_batches_by_batch_size() -> None:
    tools = [_tool_info(f"tool_{i}") for i in range(25)]
    client = _FakeClient()

    results = await classify_tools(tools, client=client, batch_size=10)

    assert len(client.messages.calls) == 3  # 10 + 10 + 5
    assert set(results) == {t.exposed_name for t in tools}
    assert all(isinstance(r, ToolClassification) for r in results.values())


async def test_classify_tools_empty_list_makes_no_calls() -> None:
    client = _FakeClient()

    results = await classify_tools([], client=client)

    assert results == {}
    assert client.messages.calls == []


async def test_classify_tools_parses_fenced_json() -> None:
    class _FencedMessages:
        async def create(self, **kwargs: Any) -> _FakeMessage:
            classification = {"reversibility": "R0", "reasoning": "reads only", "confidence": 0.9}
            body = json.dumps({"a": classification})
            return _FakeMessage(content=[_TextBlock(text=f"```json\n{body}\n```")])

    class _FencedClient:
        messages = _FencedMessages()

    results = await classify_tools([_tool_info("a")], client=_FencedClient())

    assert results["a"].reversibility == "R0"
    assert results["a"].confidence == 0.9


def test_apply_classifications_writes_comment_and_header() -> None:
    doc_text = """journal_path = "~/.backstep/journal.db"

[[servers]]
name = "echo"
command = "python"
"""
    tool_infos = [_tool_info("echo")]
    classifications = {
        "echo": ToolClassification(reversibility="R0", reasoning="read-only echo", confidence=0.82)
    }

    new_text = apply_classifications(
        doc_text, tool_infos, classifications, servers_to_populate={"echo"}
    )

    assert HEADER_SENTINEL in new_text
    assert "confidence 0.82" in new_text
    assert "review before trusting" in new_text
    assert 'reversibility = "R0"' in new_text


def test_apply_classifications_leaves_untouched_servers_alone() -> None:
    doc_text = """[[servers]]
name = "echo"
command = "python"

[[servers.tools]]
name = "keepme"
reversibility = "R1"
"""
    new_text = apply_classifications(
        doc_text, tool_infos=[], classifications={}, servers_to_populate=set()
    )

    assert "keepme" in new_text
    assert 'reversibility = "R1"' in new_text


async def test_run_init_round_trips_and_respects_force(tmp_path: Path) -> None:
    config_path = tmp_path / "backstep.toml"
    config_path.write_text(
        f"""journal_path = "~/.backstep/journal.db"

[[servers]]
name = "echo"
command = {json.dumps(sys.executable)}
args = [{json.dumps(str(FIXTURE))}]
""",
        encoding="utf-8",
    )

    client = _FakeClient(reversibility_by_tool={"echo": "R0", "fail": "R4"})
    result = await run_init(config_path, client=client)

    assert result.skipped_servers == []
    assert set(result.classifications) == {"echo", "fail"}

    reloaded = load_config(config_path)
    tools_by_name = {t.name: t for t in reloaded.servers[0].tools}
    assert tools_by_name["echo"].reversibility == "R0"
    assert tools_by_name["fail"].reversibility == "R4"
    assert HEADER_SENTINEL in config_path.read_text(encoding="utf-8")

    # Running again without --force must not touch the already-classified server.
    second_client = _FakeClient(reversibility_by_tool={"echo": "R3", "fail": "R3"})
    second_result = await run_init(config_path, client=second_client)

    assert second_result.skipped_servers == ["echo"]
    assert second_result.classifications == {}
    assert second_client.messages.calls == []

    unchanged = load_config(config_path)
    unchanged_reversibility = {t.name: t.reversibility for t in unchanged.servers[0].tools}
    assert unchanged_reversibility == {"echo": "R0", "fail": "R4"}


async def test_run_init_force_overwrites_existing_policies(tmp_path: Path) -> None:
    config_path = tmp_path / "backstep.toml"
    config_path.write_text(
        f"""[[servers]]
name = "echo"
command = {json.dumps(sys.executable)}
args = [{json.dumps(str(FIXTURE))}]

[[servers.tools]]
name = "echo"
reversibility = "R1"

[[servers.tools]]
name = "fail"
reversibility = "R1"
""",
        encoding="utf-8",
    )

    client = _FakeClient(reversibility_by_tool={"echo": "R0", "fail": "R4"})
    result = await run_init(config_path, force=True, client=client)

    assert result.skipped_servers == []
    reloaded = load_config(config_path)
    tools_by_name = {t.name: t.reversibility for t in reloaded.servers[0].tools}
    assert tools_by_name == {"echo": "R0", "fail": "R4"}
