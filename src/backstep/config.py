"""Pydantic models for backstep.toml configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import tomlkit
from pydantic import BaseModel, Field

ReversibilityClass = Literal["R0", "R1", "R2", "R3", "R4"]
UnknownToolBehavior = Literal["deny", "warn", "allow"]


class ToolPolicy(BaseModel):
    name: str
    reversibility: ReversibilityClass
    adapter: str | None = None
    require_confirmation: bool = False
    blocked: bool = False


class UpstreamServer(BaseModel):
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    tools: list[ToolPolicy] = Field(default_factory=list)


class PostgresAdapterConfig(BaseModel):
    """`[adapters.postgres]`. The adapter connects to Postgres itself, separate

    from whatever connection the upstream MCP server uses; `dsn` here (or
    `BACKSTEP_PG_DSN` in the environment, which takes precedence) is that
    connection.
    """

    dsn: str | None = None
    max_captured_rows: int = 1000


class AdaptersConfig(BaseModel):
    postgres: PostgresAdapterConfig | None = None


class BackstepConfig(BaseModel):
    journal_path: str = "~/.backstep/journal.db"
    unknown_tool_behavior: UnknownToolBehavior = "warn"
    servers: list[UpstreamServer] = Field(default_factory=list)
    adapters: AdaptersConfig = Field(default_factory=AdaptersConfig)


def load_config(path: str | Path) -> BackstepConfig:
    """Load a BackstepConfig from a TOML file at the given path."""
    data = tomlkit.parse(Path(path).read_text(encoding="utf-8"))
    return BackstepConfig.model_validate(data.unwrap())
