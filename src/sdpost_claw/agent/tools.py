"""Type-safe Tool Registry - inspired by opencode's tool design."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from sdpost_claw.common.utils import truncate_text


@dataclass
class ToolContext:
    """Tool execution context."""
    session_id: str
    agent_id: str = ""
    assistant_message_id: str = ""
    tool_call_id: str = ""
    cwd: str = ""
    max_output_chars: int = 2000


@dataclass
class ToolResult:
    """Tool execution result."""
    tool_call_id: str
    name: str
    content: str
    structured_output: Any | None = None
    externalized_path: str | None = None
    is_truncated: bool = False
    is_error: bool = False
    duration_ms: int = 0


@dataclass
class ModelOutput:
    """Model-visible output."""
    text: str
    externalized_path: str | None = None
    is_truncated: bool = False


@dataclass
class ToolDefinition:
    """
    Tool Definition - type-safe tool with schema validation.

    Features:
    - Input/output validation
    - Structured output separate from model-visible output
    - Permission integration
    - Output size limiting
    """

    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema
    execute_fn: Callable[[dict, ToolContext], Awaitable[Any]]
    permission: str | None = None
    max_output_chars: int = 2000
    # Optional sync content-only rewrite applied by the tool pipeline AFTER
    # all normalization (truncation) is done (dsh finalizeContent). Defaults
    # to None — backward compatible, existing tools are unaffected.
    finalize_content: Callable[[str], str] | None = None

    async def execute(self, input_data: dict, context: ToolContext) -> ToolResult:
        """Execute the tool."""
        import time
        start = time.time()

        try:
            # Execute
            output = await self.execute_fn(input_data, context)

            # Format for model
            model_output = self._format_for_model(output)

            duration = int((time.time() - start) * 1000)

            return ToolResult(
                tool_call_id=context.tool_call_id,
                name=self.name,
                content=model_output.text,
                externalized_path=model_output.externalized_path,
                is_truncated=model_output.is_truncated,
                duration_ms=duration,
            )
        except Exception as e:
            duration = int((time.time() - start) * 1000)
            return ToolResult(
                tool_call_id=context.tool_call_id,
                name=self.name,
                content=f"Error: {e}",
                is_error=True,
                duration_ms=duration,
            )

    def _format_for_model(self, output: Any) -> ModelOutput:
        """Format output for model visibility with size limit."""
        text = str(output) if not isinstance(output, str) else output
        if len(text) <= self.max_output_chars:
            return ModelOutput(text=text, is_truncated=False)

        # Truncate: preserve head and tail
        truncated, _ = truncate_text(text, self.max_output_chars)
        return ModelOutput(
            text=truncated,
            is_truncated=True,
        )

    def to_dict(self) -> dict:
        """Convert to OpenAI-compatible tool dict."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


class ToolRegistry:
    """Tool registry - manages tool definitions."""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool."""
        self._tools.pop(name, None)

    def get(self, name: str) -> ToolDefinition | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_all(self) -> list[ToolDefinition]:
        """List all registered tools."""
        return list(self._tools.values())

    def list_for_model(self) -> list[dict]:
        """List tools in OpenAI-compatible format."""
        return [t.to_dict() for t in self._tools.values()]

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


class BuiltInTools:
    """Built-in tools - file ops, shell, network, etc."""

    @staticmethod
    def register_all(registry: ToolRegistry, cwd: str = "") -> None:
        """Register all built-in tools."""

        # === File Operations ===

        registry.register(ToolDefinition(
            name="read",
            description="Read file contents",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"},
                    "offset": {"type": "integer", "description": "Line offset to start from"},
                    "limit": {"type": "integer", "description": "Number of lines to read"},
                },
                "required": ["path"],
            },
            execute_fn=lambda inp, ctx: BuiltInTools._read_file(inp, ctx),
            permission="file.read",
        ))

        registry.register(ToolDefinition(
            name="write",
            description="Write content to a file",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to write"},
                    "content": {"type": "string", "description": "Content to write"},
                },
                "required": ["path", "content"],
            },
            execute_fn=lambda inp, ctx: BuiltInTools._write_file(inp, ctx),
            permission="file.write",
        ))

        registry.register(ToolDefinition(
            name="edit",
            description="Edit file with exact string replacement",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "old_string": {"type": "string", "description": "String to replace"},
                    "new_string": {"type": "string", "description": "Replacement string"},
                },
                "required": ["path", "old_string", "new_string"],
            },
            execute_fn=lambda inp, ctx: BuiltInTools._edit_file(inp, ctx),
            permission="file.edit",
        ))

        registry.register(ToolDefinition(
            name="glob",
            description="Find files by glob pattern",
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern (e.g., '**/*.py')"},
                    "path": {"type": "string", "description": "Base directory"},
                },
                "required": ["pattern"],
            },
            execute_fn=lambda inp, ctx: BuiltInTools._glob(inp, ctx),
            permission="file.read",
        ))

        registry.register(ToolDefinition(
            name="grep",
            description="Search file contents with regex",
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern"},
                    "path": {"type": "string", "description": "File or directory to search"},
                    "glob": {"type": "string", "description": "Glob filter for files"},
                },
                "required": ["pattern"],
            },
            execute_fn=lambda inp, ctx: BuiltInTools._grep(inp, ctx),
            permission="file.read",
        ))

        # === Shell Execution ===

        registry.register(ToolDefinition(
            name="bash",
            description="Execute shell command",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default 60)"},
                },
                "required": ["command"],
            },
            execute_fn=lambda inp, ctx: BuiltInTools._bash(inp, ctx),
            permission="shell.execute",
            max_output_chars=4000,
        ))

        # === Network ===

        registry.register(ToolDefinition(
            name="webfetch",
            description="Fetch web content",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                    "max_chars": {"type": "integer", "description": "Max characters to return"},
                },
                "required": ["url"],
            },
            execute_fn=lambda inp, ctx: BuiltInTools._webfetch(inp, ctx),
            permission="network.request",
            max_output_chars=8000,
        ))

        # === User Interaction ===

        registry.register(ToolDefinition(
            name="question",
            description="Ask the user a question",
            input_schema={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Question to ask"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional answer choices",
                    },
                },
                "required": ["question"],
            },
            execute_fn=lambda inp, ctx: BuiltInTools._question(inp, ctx),
            permission="user.interact",
        ))

    @staticmethod
    async def _read_file(inp: dict, ctx: ToolContext) -> str:
        path = Path(inp["path"])
        if not path.is_absolute():
            path = Path(ctx.cwd) / path
        if not path.exists():
            return f"Error: File not found: {path}"

        content = path.read_text(encoding="utf-8")
        lines = content.splitlines()

        offset = inp.get("offset", 1)
        limit = inp.get("limit")

        if offset > 1 or limit:
            start = max(0, offset - 1)
            end = start + limit if limit else len(lines)
            lines = lines[start:end]

        # Add line numbers
        numbered = [f"{i + offset}: {line}" for i, line in enumerate(lines)]
        return "\n".join(numbered)

    @staticmethod
    async def _write_file(inp: dict, ctx: ToolContext) -> str:
        path = Path(inp["path"])
        if not path.is_absolute():
            path = Path(ctx.cwd) / path

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(inp["content"], encoding="utf-8")
        return f"Written {len(inp['content'])} chars to {path}"

    @staticmethod
    async def _edit_file(inp: dict, ctx: ToolContext) -> str:
        path = Path(inp["path"])
        if not path.is_absolute():
            path = Path(ctx.cwd) / path
        if not path.exists():
            return f"Error: File not found: {path}"

        content = path.read_text(encoding="utf-8")
        old_string = inp["old_string"]
        new_string = inp["new_string"]

        if old_string not in content:
            return f"Error: String not found in {path}"

        # Replace only first occurrence
        new_content = content.replace(old_string, new_string, 1)
        path.write_text(new_content, encoding="utf-8")
        return f"Edited {path}"

    @staticmethod
    async def _glob(inp: dict, ctx: ToolContext) -> str:
        pattern = inp["pattern"]
        base_path = Path(inp.get("path", ctx.cwd))

        matches = list(base_path.glob(pattern))
        if not matches:
            return f"No files matching: {pattern}"

        return "\n".join(str(m.relative_to(base_path)) for m in sorted(matches))

    @staticmethod
    async def _grep(inp: dict, ctx: ToolContext) -> str:
        pattern = inp["pattern"]
        search_path = Path(inp.get("path", ctx.cwd))
        glob_filter = inp.get("glob")

        results: list[str] = []
        regex = re.compile(pattern)

        if search_path.is_file():
            files = [search_path]
        else:
            if glob_filter:
                files = list(search_path.rglob(glob_filter))
            else:
                files = [f for f in search_path.rglob("*") if f.is_file()]

        for file_path in files:
            try:
                content = file_path.read_text(encoding="utf-8")
                for i, line in enumerate(content.splitlines(), 1):
                    if regex.search(line):
                        rel_path = file_path.relative_to(search_path) if not search_path.is_file() else file_path
                        results.append(f"{rel_path}:{i}: {line}")
            except (OSError, UnicodeDecodeError):
                continue

        if not results:
            return f"No matches for pattern: {pattern}"

        return "\n".join(results[:100])  # Limit results

    @staticmethod
    async def _bash(inp: dict, ctx: ToolContext) -> str:
        command = inp["command"]
        timeout = inp.get("timeout", 60)

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=ctx.cwd,
            )
            output = result.stdout
            if result.stderr:
                output += f"\n[stderr]: {result.stderr}"
            if result.returncode != 0:
                output += f"\n[exit code: {result.returncode}]"
            return output.strip() or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {timeout}s"
        except Exception as e:
            return f"Error: {e}"

    @staticmethod
    async def _webfetch(inp: dict, ctx: ToolContext) -> str:
        url = inp["url"]
        max_chars = inp.get("max_chars", 5000)

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    text = await resp.text()
                    if len(text) > max_chars:
                        text = text[:max_chars] + "\n... (truncated)"
                    return text
        except Exception as e:
            return f"Error fetching {url}: {e}"

    @staticmethod
    async def _question(inp: dict, ctx: ToolContext) -> str:
        # This is handled specially by the UI layer
        return f"QUESTION: {inp['question']}"
