"""Tests for backstep.config."""

from pathlib import Path

from backstep.config import BackstepConfig, ToolPolicy, UpstreamServer, load_config

SAMPLE_TOML = """
journal_path = "~/.backstep/journal.db"
unknown_tool_behavior = "warn"

[[servers]]
name = "filesystem"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]

[servers.env]
FOO = "bar"

[[servers.tools]]
name = "write_file"
reversibility = "R2"
adapter = "filesystem"
require_confirmation = true

[[servers.tools]]
name = "read_file"
reversibility = "R0"
"""


def test_backstep_config_defaults() -> None:
    config = BackstepConfig()
    assert config.journal_path == "~/.backstep/journal.db"
    assert config.unknown_tool_behavior == "warn"
    assert config.servers == []


def test_tool_policy_defaults() -> None:
    policy = ToolPolicy(name="write_file", reversibility="R2")
    assert policy.adapter is None
    assert policy.require_confirmation is False


def test_upstream_server_defaults() -> None:
    server = UpstreamServer(name="filesystem", command="npx")
    assert server.args == []
    assert server.env == {}
    assert server.tools == []


def test_load_config_round_trip(tmp_path: Path) -> None:
    config_path = tmp_path / "backstep.toml"
    config_path.write_text(SAMPLE_TOML, encoding="utf-8")

    config = load_config(config_path)

    assert config.journal_path == "~/.backstep/journal.db"
    assert config.unknown_tool_behavior == "warn"
    assert len(config.servers) == 1

    server = config.servers[0]
    assert server.name == "filesystem"
    assert server.command == "npx"
    assert server.args == ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    assert server.env == {"FOO": "bar"}
    assert len(server.tools) == 2

    write_tool, read_tool = server.tools
    assert write_tool.name == "write_file"
    assert write_tool.reversibility == "R2"
    assert write_tool.adapter == "filesystem"
    assert write_tool.require_confirmation is True

    assert read_tool.name == "read_file"
    assert read_tool.reversibility == "R0"
    assert read_tool.adapter is None
    assert read_tool.require_confirmation is False
