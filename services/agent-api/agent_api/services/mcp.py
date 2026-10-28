import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from agent_api.core.config import MCPServerSettings


@dataclass(slots=True)
class ToolResult:
    server: str
    tool: str
    arguments: dict[str, Any]
    result: Any
    result_hash: str | None
    latency_ms: int
    status: str
    error: str | None = None


class MCPOrchestrator:
    """Runs explicitly mapped MCP tools concurrently with per-server isolation.

    Transport-specific clients are injected so this layer remains testable. It never
    falls back to an arbitrary first tool and never performs local vector search.
    """

    def __init__(
        self,
        servers: list[MCPServerSettings],
        caller: Callable[[MCPServerSettings, str, dict], Awaitable[Any]],
    ) -> None:
        self.servers = {server.name: server for server in servers if server.enabled}
        self.caller = caller

    async def call(self, server_name: str, tool: str, arguments: dict[str, Any]) -> ToolResult:
        server = self.servers[server_name]
        if tool not in server.allowed_tools:
            raise ValueError(f"Tool {tool!r} is not allowed for MCP server {server_name!r}")
        started = perf_counter()
        try:
            result = await asyncio.wait_for(
                self.caller(server, tool, arguments), timeout=server.timeout_seconds
            )
            canonical = json.dumps(
                result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
            )
            return ToolResult(
                server_name,
                tool,
                arguments,
                result,
                hashlib.sha256(canonical.encode()).hexdigest(),
                int((perf_counter() - started) * 1000),
                "completed",
            )
        except Exception as exc:
            return ToolResult(
                server_name,
                tool,
                arguments,
                None,
                None,
                int((perf_counter() - started) * 1000),
                "failed",
                type(exc).__name__,
            )

    async def call_many(self, calls: list[tuple[str, str, dict[str, Any]]]) -> list[ToolResult]:
        return list(await asyncio.gather(*(self.call(*call) for call in calls)))
