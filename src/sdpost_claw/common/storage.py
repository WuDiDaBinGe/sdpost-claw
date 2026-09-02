"""Storage abstraction layer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiofiles


class Storage:
    """File-based storage abstraction."""

    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _path(self, *parts: str) -> Path:
        """Get path relative to base."""
        return self.base_path.joinpath(*parts)

    async def read_json(self, *parts: str) -> dict[str, Any] | None:
        """Read JSON file."""
        path = self._path(*parts)
        if not path.exists():
            return None
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            content = await f.read()
        return json.loads(content)

    async def write_json(self, *parts: str, data: Any) -> None:
        """Write JSON file."""
        path = self._path(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, indent=2, ensure_ascii=False, default=str))

    async def read_text(self, *parts: str) -> str | None:
        """Read text file."""
        path = self._path(*parts)
        if not path.exists():
            return None
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            return await f.read()

    async def write_text(self, *parts: str, content: str) -> None:
        """Write text file."""
        path = self._path(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(content)

    async def read_jsonl(self, *parts: str) -> list[dict[str, Any]]:
        """Read JSONL file."""
        path = self._path(*parts)
        if not path.exists():
            return []
        lines = []
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if line:
                    lines.append(json.loads(line))
        return lines

    async def append_jsonl(self, *parts: str, data: dict[str, Any]) -> None:
        """Append to JSONL file."""
        path = self._path(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, "a", encoding="utf-8") as f:
            await f.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")

    async def list_files(self, *parts: str, pattern: str = "*") -> list[Path]:
        """List files matching pattern."""
        path = self._path(*parts)
        if not path.exists():
            return []
        return list(path.glob(pattern))

    async def delete(self, *parts: str) -> None:
        """Delete file."""
        path = self._path(*parts)
        if path.exists():
            path.unlink()

    def exists(self, *parts: str) -> bool:
        """Check if path exists."""
        return self._path(*parts).exists()
