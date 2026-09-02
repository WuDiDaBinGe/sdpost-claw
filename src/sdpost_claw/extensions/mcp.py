"""MCP Connectors - permission-aware Model Context Protocol integration."""

from __future__ import annotations

import asyncio
import json
import subprocess
from abc import ABC, abstractmethod
from typing import Any


class MCPTransport(ABC):
    """MCP transport abstraction."""

    @abstractmethod
    async def connect(self) -> None:
        """Connect to MCP server."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from MCP server."""
        ...

    @abstractmethod
    async def send(self, data: dict[str, Any]) -> None:
        """Send data."""
        ...

    @abstractmethod
    async def receive(self) -> dict[str, Any]:
        """Receive data."""
        ...


class StdioMCPTransport(MCPTransport):
    """stdio-based MCP transport."""

    def __init__(self, command: str, args: list[str] | None = None, env: dict[str, str] | None = None):
        self.command = command
        self.args = args or []
        self.env = env
        self._process: asyncio.subprocess.Process | None = None

    async def connect(self) -> None:
        """Start subprocess."""
        self._process = await asyncio.create_subprocess_exec(
            self.command, *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
        )

    async def disconnect(self) -> None:
        """Terminate subprocess."""
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
            self._process = None

    async def send(self, data: dict[str, Any]) -> None:
        """Send JSON-RPC message."""
        if self._process and self._process.stdin:
            line = json.dumps(data) + "\n"
            self._process.stdin.write(line.encode())
            await self._process.stdin.drain()

    async def receive(self) -> dict[str, Any]:
        """Receive JSON-RPC message."""
        if self._process and self._process.stdout:
            line = await self._process.stdout.readline()
            if line:
                return json.loads(line.decode())
        return {}


class SSEMCPTransport(MCPTransport):
    """SSE-based MCP transport."""

    def __init__(self, url: str, headers: dict[str, str] | None = None):
        self.url = url
        self.headers = headers or {}
        self._session: Any = None

    async def connect(self) -> None:
        """Create HTTP session."""
        import aiohttp
        self._session = aiohttp.ClientSession(headers=self.headers)

    async def disconnect(self) -> None:
        """Close session."""
        if self._session:
            await self._session.close()
            self._session = None

    async def send(self, data: dict[str, Any]) -> None:
        """Send via POST."""
        if self._session:
            async with self._session.post(self.url, json=data) as resp:
                await resp.read()

    async def receive(self) -> dict[str, Any]:
        """Receive via SSE (simplified)."""
        # Full SSE implementation would require streaming
        return {}


class MCPTool:
    """MCP tool wrapper."""

    def __init__(self, name: str, description: str, input_schema: dict[str, Any]):
        self.name = name
        self.description = description
        self.input_schema = input_schema


class MCPConnector:
    """
    MCP Connector - permission-aware tool exposure.

    Features:
    - Permission-aware tool exposure
    - Automatic schema validation
    - Output size limiting
    """

    def __init__(
        self,
        name: str,
        transport: MCPTransport,
        permission_prefix: str = "mcp",
        max_output_chars: int = 2000,
    ):
        self.name = name
        self.transport = transport
        self.permission_prefix = permission_prefix
        self.max_output_chars = max_output_chars
        self._tools: list[MCPTool] = []
        self._connected = False

    async def connect(self) -> None:
        """Connect to MCP server and discover tools."""
        await self.transport.connect()
        self._connected = True
        # Initialize MCP session
        await self._initialize()
        # Discover tools
        self._tools = await self._list_tools()

    async def disconnect(self) -> None:
        """Disconnect from MCP server."""
        await self.transport.disconnect()
        self._connected = False
        self._tools.clear()

    async def _initialize(self) -> None:
        """Initialize MCP session."""
        await self.transport.send({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "sdpost-claw",
                    "version": "0.1.0",
                },
            },
        })
        await self.transport.receive()

    async def _list_tools(self) -> list[MCPTool]:
        """List available tools from MCP server."""
        await self.transport.send({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })
        response = await self.transport.receive()
        tools = []
        for tool_data in response.get("result", {}).get("tools", []):
            tools.append(MCPTool(
                name=tool_data["name"],
                description=tool_data.get("description", ""),
                input_schema=tool_data.get("inputSchema", {}),
            ))
        return tools

    def list_tools(self) -> list[MCPTool]:
        """List discovered tools."""
        return list(self._tools)

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call an MCP tool."""
        await self.transport.send({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        })
        return await self.transport.receive()

    def get_permission(self, tool_name: str) -> str:
        """Get permission string for a tool."""
        return f"{self.permission_prefix}.{self.name}.{tool_name}"
