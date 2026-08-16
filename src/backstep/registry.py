"""Registry of upstream servers, tools, and their reversibility policies.

Resolution is fully deterministic from `backstep.toml`: no LLM calls, no
network I/O, ever happen here. `backstep init` (see `backstep.bootstrap`) is
the only place an LLM proposes classifications, and it writes its proposals
into the config file for this registry to read back.
"""

from __future__ import annotations

from pathlib import Path

from backstep.config import BackstepConfig, ToolPolicy, UnknownToolBehavior, load_config


class ToolRegistry:
    """Deterministic `exposed_tool_name -> ToolPolicy` lookup, built from config."""

    def __init__(self, config: BackstepConfig) -> None:
        self._config = config
        self._policies: dict[str, ToolPolicy] = {}
        for server in config.servers:
            for tool in server.tools:
                self._policies[tool.name] = tool

    @classmethod
    def from_config_file(cls, path: str | Path) -> ToolRegistry:
        return cls(load_config(path))

    def classify(self, tool_name: str) -> ToolPolicy:
        """Return the configured policy for `tool_name`, or a synthetic fallback.

        The fallback is derived from `unknown_tool_behavior` when the tool has
        no explicit entry in any server's `tools` list.
        """
        policy = self._policies.get(tool_name)
        if policy is not None:
            return policy
        return _synthetic_policy(tool_name, self._config.unknown_tool_behavior)


def _synthetic_policy(tool_name: str, behavior: UnknownToolBehavior) -> ToolPolicy:
    if behavior == "deny":
        return ToolPolicy(
            name=tool_name, reversibility="R4", require_confirmation=True, blocked=True
        )
    if behavior == "allow":
        return ToolPolicy(name=tool_name, reversibility="R0")
    return ToolPolicy(name=tool_name, reversibility="R4")
