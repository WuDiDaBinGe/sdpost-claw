"""JSONL Transcript - event sourcing for session recording."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import aiofiles


class EventType(Enum):
    """Event types for transcript."""
    SESSION_CREATED = "session.created"
    SESSION_RESUMED = "session.resumed"
    SESSION_CLOSED = "session.closed"
    PROMPT_SUBMITTED = "prompt.submitted"
    MODEL_REQUEST = "model.request"
    MODEL_RESPONSE = "model.response"
    TOOL_CALLED = "tool.called"
    TOOL_RESULT = "tool.result"
    CONTEXT_UPDATED = "context.updated"
    COMPACTION = "compaction.occurred"
    ERROR = "error.occurred"


@dataclass
class TranscriptEvent:
    """Transcript event."""
    type: EventType
    session_id: str
    timestamp: datetime
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "session_id": self.session_id,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TranscriptEvent:
        return cls(
            type=EventType(data["type"]),
            session_id=data["session_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            data=data.get("data", {}),
        )


class JSONLTranscript:
    """
    JSONL Transcript - event sourcing for session recording.

    Records all state changes for:
    - Session replay
    - Audit trail
    - Debugging
    """

    def __init__(self, base_path: Path):
        self.base_path = base_path / "transcripts"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        """Get transcript path for session."""
        return self.base_path / f"{session_id}.jsonl"

    async def record(self, event: TranscriptEvent) -> None:
        """Record an event."""
        path = self._path(event.session_id)
        async with aiofiles.open(path, "a", encoding="utf-8") as f:
            await f.write(json.dumps(event.to_dict(), ensure_ascii=False, default=str) + "\n")

    async def record_simple(
        self,
        event_type: EventType,
        session_id: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Record a simple event."""
        event = TranscriptEvent(
            type=event_type,
            session_id=session_id,
            timestamp=datetime.now(),
            data=data or {},
        )
        await self.record(event)

    async def get_events(self, session_id: str) -> list[TranscriptEvent]:
        """Get all events for a session."""
        path = self._path(session_id)
        if not path.exists():
            return []
        events = []
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if line:
                    events.append(TranscriptEvent.from_dict(json.loads(line)))
        return events

    async def replay(self, session_id: str) -> list[TranscriptEvent]:
        """Replay session events (alias for get_events)."""
        return await self.get_events(session_id)

    async def get_events_by_type(
        self,
        session_id: str,
        event_type: EventType,
    ) -> list[TranscriptEvent]:
        """Get events filtered by type."""
        events = await self.get_events(session_id)
        return [e for e in events if e.type == event_type]

    async def clear(self, session_id: str) -> None:
        """Clear transcript for a session."""
        path = self._path(session_id)
        if path.exists():
            path.unlink()
