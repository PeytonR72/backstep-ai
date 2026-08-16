"""Trivial MCP server used as a test fixture for the backstep proxy.

Run directly (`python echo_server.py`) as a stdio MCP server exposing two
tools: `echo`, which always succeeds, and `fail`, which always raises so its
call result comes back with `is_error=True`.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

server: MCPServer = MCPServer("echo")


@server.tool()
def echo(text: str) -> str:
    """Echo the given text back unchanged."""
    return text


@server.tool()
def fail(message: str = "boom") -> str:
    """Always raise, producing a tool call result with is_error=True."""
    raise ValueError(message)


if __name__ == "__main__":
    server.run()
