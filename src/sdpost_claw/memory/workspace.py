"""Workspace Memory - project-level persistent memory."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import aiofiles


@dataclass
class WorkspaceMemory:
    """
    Workspace Memory - project-level persistent memory.

    Stores:
    - Daily summaries
    - Key decisions
    - Important facts
    """

    workspace_id: str
    daily_summary: str | None = None
    recent_decisions: list[str] = field(default_factory=list)
    key_facts: list[str] = field(default_factory=list)
    updated_at: datetime = field(default_factory=datetime.now)

    # Limits
    MAX_DECISIONS: int = 50
    MAX_FACTS: int = 100

    def add_decision(self, decision: str) -> None:
        """Add a decision record."""
        self.recent_decisions.append(decision)
        if len(self.recent_decisions) > self.MAX_DECISIONS:
            self.recent_decisions = self.recent_decisions[-self.MAX_DECISIONS:]
        self.updated_at = datetime.now()

    def add_fact(self, fact: str) -> None:
        """Add a key fact."""
        self.key_facts.append(fact)
        if len(self.key_facts) > self.MAX_FACTS:
            self.key_facts = self.key_facts[-self.MAX_FACTS:]
        self.updated_at = datetime.now()

    def update_daily_summary(self, summary: str) -> None:
        """Update daily summary."""
        self.daily_summary = summary
        self.updated_at = datetime.now()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict."""
        return {
            "workspace_id": self.workspace_id,
            "daily_summary": self.daily_summary,
            "recent_decisions": self.recent_decisions,
            "key_facts": self.key_facts,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceMemory:
        """Create from dict."""
        memory = cls(workspace_id=data["workspace_id"])
        memory.daily_summary = data.get("daily_summary")
        memory.recent_decisions = data.get("recent_decisions", [])
        memory.key_facts = data.get("key_facts", [])
        if "updated_at" in data:
            memory.updated_at = datetime.fromisoformat(data["updated_at"])
        return memory


class WorkspaceMemoryStore:
    """File-based workspace memory store."""

    def __init__(self, base_path: Path):
        self.base_path = base_path / "workspace"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _path(self, workspace_id: str) -> Path:
        """Get memory file path."""
        return self.base_path / f"{workspace_id}.json"

    async def get(self, workspace_id: str) -> WorkspaceMemory | None:
        """Get workspace memory."""
        path = self._path(workspace_id)
        if not path.exists():
            return None
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            content = await f.read()
        data = json.loads(content)
        return WorkspaceMemory.from_dict(data)

    async def save(self, memory: WorkspaceMemory) -> None:
        """Save workspace memory."""
        path = self._path(memory.workspace_id)
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(memory.to_dict(), indent=2, ensure_ascii=False))

    async def delete(self, workspace_id: str) -> None:
        """Delete workspace memory."""
        path = self._path(workspace_id)
        if path.exists():
            path.unlink()
