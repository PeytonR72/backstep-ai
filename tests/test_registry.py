"""Tests for backstep.registry."""

from pathlib import Path

from backstep.config import BackstepConfig, ToolPolicy, UpstreamServer
from backstep.registry import ToolRegistry


def _config(unknown_tool_behavior: str = "warn") -> BackstepConfig:
    return BackstepConfig(
        unknown_tool_behavior=unknown_tool_behavior,  # type: ignore[arg-type]
        servers=[
            UpstreamServer(
                name="filesystem",
                command="npx",
                tools=[
                    ToolPolicy(name="read_file", reversibility="R0"),
                    ToolPolicy(
                        name="write_file",
                        reversibility="R2",
                        adapter="filesystem",
                        require_confirmation=True,
                    ),
                ],
            ),
        ],
    )


def test_classify_returns_configured_policy_for_known_tool() -> None:
    registry = ToolRegistry(_config())

    policy = registry.classify("write_file")

    assert policy.reversibility == "R2"
    assert policy.adapter == "filesystem"
    assert policy.require_confirmation is True
    assert policy.blocked is False


def test_classify_returns_configured_policy_unmodified_for_readonly_tool() -> None:
    registry = ToolRegistry(_config())

    policy = registry.classify("read_file")

    assert policy.reversibility == "R0"
    assert policy.require_confirmation is False


def test_classify_unknown_tool_deny() -> None:
    registry = ToolRegistry(_config(unknown_tool_behavior="deny"))

    policy = registry.classify("mystery_tool")

    assert policy.name == "mystery_tool"
    assert policy.reversibility == "R4"
    assert policy.require_confirmation is True
    assert policy.blocked is True


def test_classify_unknown_tool_warn() -> None:
    registry = ToolRegistry(_config(unknown_tool_behavior="warn"))

    policy = registry.classify("mystery_tool")

    assert policy.reversibility == "R4"
    assert policy.require_confirmation is False
    assert policy.blocked is False


def test_classify_unknown_tool_allow() -> None:
    registry = ToolRegistry(_config(unknown_tool_behavior="allow"))

    policy = registry.classify("mystery_tool")

    assert policy.reversibility == "R0"
    assert policy.require_confirmation is False
    assert policy.blocked is False


def test_from_config_file_round_trips(tmp_path: Path) -> None:
    toml_text = """
unknown_tool_behavior = "deny"

[[servers]]
name = "db"
command = "python"

[[servers.tools]]
name = "select_rows"
reversibility = "R0"
"""
    config_path = tmp_path / "backstep.toml"
    config_path.write_text(toml_text, encoding="utf-8")

    registry = ToolRegistry.from_config_file(config_path)

    assert registry.classify("select_rows").reversibility == "R0"
    assert registry.classify("delete_rows").blocked is True
