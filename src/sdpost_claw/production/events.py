"""Event Sourcing - complete event store with hash chain integrity."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import aiofiles


class EventType(Enum):
    """Event types."""
    # Session events
    SESSION_CREATED = "session.created"
    SESSION_RESUMED = "session.resumed"
    SESSION_CLOSED = "session.closed"
    SESSION_EPOCH_CHANGED = "session.epoch_changed"

    # Input events
    PROMPT_SUBMITTED = "prompt.submitted"
    PROMPT_ADMITTED = "prompt.admitted"

    # Tool events
    TOOL_CALLED = "tool.called"
    TOOL_RESULT_SETTLED = "tool.result_settled"
    TOOL_OUTPUT_EXTERNALIZED = "tool.output_externalized"

    # Model events
    MODEL_REQUEST = "model.request"
    MODEL_RESPONSE = "model.response"
    MODEL_ERROR = "model.error"

    # Context events
    CONTEXT_INITIALIZED = "context.initialized"
    CONTEXT_UPDATED = "context.updated"
    CONTEXT_REPLACED = "context.replaced"
    CONTEXT_UNAVAILABLE = "context.unavailable"

    # Compaction events
    COMPACTION_TRIGGERED = "compaction.triggered"
    COMPACTION_COMPLETED = "compaction.completed"

    # Permission events
    PERMISSION_CHECKED = "permission.checked"
    PERMISSION_DENIED = "permission.denied"
    PERMISSION_ASKED = "permission.asked"

    # Agent events
    AGENT_CREATED = "agent.created"
    AGENT_MODE_CHANGED = "agent.mode_changed"
    SUB_AGENT_SPAWNED = "agent.sub_spawned"


@dataclass
class Event:
    """Event with hash chain integrity."""
    type: EventType
    session_id: str
    timestamp: datetime
    data: dict[str, Any] = field(default_factory=dict)
    context_snapshot: dict[str, Any] | None = None
    hash_chain: str | None = None

    def compute_hash(self, previous_hash: str | None = None) -> str:
        """Compute SHA256 hash for chain integrity."""
        content = json.dumps({
            "type": self.type.value,
            "session_id": self.session_id,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
            "previous_hash": previous_hash,
        }, sort_keys=True, default=str)
        return hashlib.sha256(content.encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "session_id": self.session_id,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
            "context_snapshot": self.context_snapshot,
            "hash_chain": self.hash_chain,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        return cls(
            type=EventType(data["type"]),
            session_id=data["session_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            data=data.get("data", {}),
            context_snapshot=data.get("context_snapshot"),
            hash_chain=data.get("hash_chain"),
        )


class EventStore:
    """
    Event Store - JSONL-based event sourcing.

    Features:
    - Append-only writes
    - Hash chain integrity
    - Session replay
    - Type filtering
    """

    def __init__(self, base_path: Path):
        self.base_path = base_path / "events"
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._last_hashes: dict[str, str] = {}

    def _path(self, session_id: str) -> Path:
        """Get event file path."""
        return self.base_path / f"{session_id}.jsonl"

    async def append(self, event: Event) -> None:
        """Append event with hash chain."""
        # Compute hash chain
        session_hash = self._last_hashes.get(event.session_id)
        event.hash_chain = event.compute_hash(session_hash)
        self._last_hashes[event.session_id] = event.hash_chain

        # Write to JSONL
        path = self._path(event.session_id)
        async with aiofiles.open(path, "a", encoding="utf-8") as f:
            await f.write(json.dumps(event.to_dict(), ensure_ascii=False, default=str) + "\n")

    async def replay(self, session_id: str) -> list[Event]:
        """Replay all events for a session."""
        path = self._path(session_id)
        if not path.exists():
            return []

        events: list[Event] = []
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if line:
                    events.append(Event.from_dict(json.loads(line)))
        return events

    async def get_events_by_type(
        self,
        session_id: str,
        event_type: EventType,
    ) -> list[Event]:
        """Get events filtered by type."""
        events = await self.replay(session_id)
        return [e for e in events if e.type == event_type]

    async def verify_integrity(self, session_id: str) -> bool:
        """Verify hash chain integrity."""
        events = await self.replay(session_id)
        previous_hash: str | None = None
        for event in events:
            expected_hash = event.compute_hash(previous_hash)
            if expected_hash != event.hash_chain:
                return False
            previous_hash = event.hash_chain
        return True

    async def clear(self, session_id: str) -> None:
        """Clear events for a session."""
        path = self._path(session_id)
        if path.exists():
            path.unlink()
