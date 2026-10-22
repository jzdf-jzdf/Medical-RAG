"""Request-scoped MCP connections used by the agent tool loop."""

from __future__ import annotations

import json
import logging
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Self

import httpx2
from mcp import ClientSession, types
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from app.core.config import MCPServerSettings

logger = logging.getLogger(__name__)


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")[:64] or "tool"


@dataclass(slots=True)
class ToolDefinition:
    name: str
    server: str
    original_name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(slots=True)
class ToolOutput:
    content: Any
    is_error: bool

    def for_model(self) -> str:
        return json.dumps(
            {"content": self.content, "is_error": self.is_error},
            ensure_ascii=False,
            default=str,
        )


@dataclass(slots=True)
class _Connection:
    config: MCPServerSettings
    session: ClientSession


class MCPToolbox:
    """Connect enabled MCP servers and expose one collision-free tool namespace."""

    def __init__(self, servers: list[MCPServerSettings]) -> None:
        self.servers = [server for server in servers if server.enabled]
        self._stack = AsyncExitStack()
        self._connections: list[_Connection] = []
        self._tools: dict[str, tuple[ToolDefinition, ClientSession]] = {}

    async def __aenter__(self) -> Self:
        await self._stack.__aenter__()
        for server in self.servers:
            try:
                await self._connect(server)
            except Exception as exc:  # one broken evidence server must not stop chat
                logger.warning("MCP server %s unavailable: %s", server.name, exc)
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        # MCP transports run background task groups. A transport failure during
        # teardown must not turn an already completed answer into a 502, and it
        # must not replace the original exception that is already propagating.
        try:
            await self._stack.__aexit__(exc_type, exc, traceback)
        except Exception:
            logger.warning("MCP toolbox cleanup failed", exc_info=True)

    async def _connect(self, config: MCPServerSettings) -> None:
        server_stack = AsyncExitStack()
        await server_stack.__aenter__()
        try:
            if config.transport == "stdio":
                streams = await server_stack.enter_async_context(
                    stdio_client(
                        StdioServerParameters(
                            command=str(config.command),
                            args=config.args,
                            env=config.env or None,
                        )
                    )
                )
            elif config.transport == "sse":
                streams = await server_stack.enter_async_context(
                    sse_client(
                        str(config.url),
                        timeout=config.timeout_seconds,
                        httpx_client_factory=_http_client,
                    )
                )
            else:
                http_client = await server_stack.enter_async_context(
                    httpx2.AsyncClient(trust_env=False)
                )
                streams = await server_stack.enter_async_context(
                    streamable_http_client(str(config.url), http_client=http_client)
                )

            read_stream, write_stream = streams[:2]
            session = await server_stack.enter_async_context(
                ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=config.timeout_seconds,
                )
            )
            await session.initialize()
            await self._discover(config, session)
        except Exception:
            try:
                await server_stack.aclose()
            except Exception:
                logger.warning("MCP connection cleanup failed", exc_info=True)
            raise
        self._stack.push_async_callback(server_stack.aclose)
        self._connections.append(_Connection(config=config, session=session))

    async def _discover(self, config: MCPServerSettings, session: ClientSession) -> None:
        allowed = set(config.allowed_tools) or None
        cursor: str | None = None
        while True:
            params = types.PaginatedRequestParams(cursor=cursor) if cursor else None
            page = await session.list_tools(params=params)
            for item in page.tools:
                if allowed is not None and item.name not in allowed:
                    continue
                name = _safe_name(f"{config.name}__{item.name}")
                if name in self._tools:
                    raise RuntimeError(f"Duplicate MCP tool name: {name}")
                definition = ToolDefinition(
                    name=name,
                    server=config.name,
                    original_name=item.name,
                    description=item.description or item.title or item.name,
                    input_schema=item.input_schema,
                )
                self._tools[name] = (definition, session)
            cursor = page.next_cursor
            if not cursor:
                break

    async def list_tools(self) -> list[ToolDefinition]:
        return [definition for definition, _ in self._tools.values()]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutput:
        target = self._tools.get(name)
        if target is None:
            raise ValueError(f"Unknown or disallowed MCP tool: {name}")
        definition, session = target
        result = await session.call_tool(definition.original_name, arguments)
        structured = getattr(result, "structured_content", None)
        if structured is not None:
            content: Any = structured
        else:
            content = [
                block.model_dump(mode="json") if hasattr(block, "model_dump") else str(block)
                for block in result.content
            ]
        return ToolOutput(content=content, is_error=bool(result.is_error))


def _http_client(**kwargs: Any) -> httpx2.AsyncClient:
    """Build MCP HTTP clients without inheriting malformed host proxy settings."""
    return httpx2.AsyncClient(trust_env=False, **kwargs)
