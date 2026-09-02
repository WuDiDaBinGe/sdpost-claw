"""User Memory - user preferences and profile."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import aiofiles


@dataclass
class UserMemory:
    """
    User Memory - user preferences and profile.

    Stores:
    - Language preference
    - Communication style
    - Expertise level
    - Custom preferences
    """

    user_id: str = "default"
    language: str = "zh-CN"
    communication_style: str = "professional"
    expertise_level: str = "intermediate"
    preferences: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=datetime.now)

    def set_preference(self, key: str, value: Any) -> None:
        """Set a preference."""
        self.preferences[key] = value
        self.updated_at = datetime.now()

    def get_preference(self, key: str, default: Any = None) -> Any:
        """Get a preference."""
        return self.preferences.get(key, default)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict."""
        return {
            "user_id": self.user_id,
            "language": self.language,
            "communication_style": self.communication_style,
            "expertise_level": self.expertise_level,
            "preferences": self.preferences,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserMemory:
        """Create from dict."""
        memory = cls(
            user_id=data.get("user_id", "default"),
            language=data.get("language", "zh-CN"),
            communication_style=data.get("communication_style", "professional"),
            expertise_level=data.get("expertise_level", "intermediate"),
        )
        memory.preferences = data.get("preferences", {})
        if "updated_at" in data:
            memory.updated_at = datetime.fromisoformat(data["updated_at"])
        return memory


class UserMemoryStore:
    """File-based user memory store."""

    def __init__(self, base_path: Path):
        self.base_path = base_path / "user"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _path(self, user_id: str) -> Path:
        """Get memory file path."""
        return self.base_path / f"{user_id}.json"

    async def get(self, user_id: str = "default") -> UserMemory | None:
        """Get user memory."""
        path = self._path(user_id)
        if not path.exists():
            return None
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            content = await f.read()
        data = json.loads(content)
        return UserMemory.from_dict(data)

    async def save(self, memory: UserMemory) -> None:
        """Save user memory."""
        path = self._path(memory.user_id)
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(memory.to_dict(), indent=2, ensure_ascii=False))

    async def get_or_create(self, user_id: str = "default") -> UserMemory:
        """Get or create user memory."""
        memory = await self.get(user_id)
        if memory is None:
            memory = UserMemory(user_id=user_id)
            await self.save(memory)
        return memory
