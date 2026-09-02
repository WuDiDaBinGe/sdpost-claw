"""Session Management - store, lifecycle, and manager."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import aiofiles

from sdpost_claw.agent.drain import Session, Prompt
from sdpost_claw.common.utils import generate_id


class SessionStore:
    """
    Session storage - persists sessions to filesystem.

    Structure:
    - sessions/<id>.json: Session metadata
    - messages/<id>.jsonl: Message history
    """

    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.sessions_path = base_path / "sessions"
        self.messages_path = base_path / "messages"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """Ensure directories exist."""
        self.sessions_path.mkdir(parents=True, exist_ok=True)
        self.messages_path.mkdir(parents=True, exist_ok=True)

    async def save(self, session: Session) -> None:
        """Save session metadata."""
        path = self.sessions_path / f"{session.id}.json"
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(session.to_dict(), indent=2, ensure_ascii=False))

    async def load(self, session_id: str) -> dict[str, Any] | None:
        """Load session metadata."""
        path = self.sessions_path / f"{session_id}.json"
        if not path.exists():
            return None
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            content = await f.read()
        return json.loads(content)

    async def list_sessions(self) -> list[dict[str, Any]]:
        """List all sessions."""
        sessions = []
        for path in sorted(self.sessions_path.glob("*.json"), reverse=True):
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                content = await f.read()
            sessions.append(json.loads(content))
        return sessions

    async def delete(self, session_id: str) -> None:
        """Delete a session."""
        path = self.sessions_path / f"{session_id}.json"
        if path.exists():
            path.unlink()
        msg_path = self.messages_path / f"{session_id}.jsonl"
        if msg_path.exists():
            msg_path.unlink()

    async def append_message(self, session_id: str, message: dict[str, Any]) -> None:
        """Append message to JSONL."""
        path = self.messages_path / f"{session_id}.jsonl"
        async with aiofiles.open(path, "a", encoding="utf-8") as f:
            await f.write(json.dumps(message, ensure_ascii=False, default=str) + "\n")

    async def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Get all messages for a session."""
        path = self.messages_path / f"{session_id}.jsonl"
        if not path.exists():
            return []
        messages = []
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if line:
                    messages.append(json.loads(line))
        return messages


class SessionLifecycle:
    """Session lifecycle management."""

    def __init__(self, store: SessionStore):
        self.store = store

    async def create(
        self,
        cwd: str,
        title: str | None = None,
        agent_mode: str = "build",
    ) -> Session:
        """Create a new session."""
        session = Session(
            id=generate_id(),
            cwd=cwd,
            title=title or "New Session",
            agent_mode=agent_mode,
        )
        await self.store.save(session)
        return session

    async def get(self, session_id: str) -> Session | None:
        """Get session by ID."""
        data = await self.store.load(session_id)
        if not data:
            return None
        session = Session(
            id=data["id"],
            cwd=data["cwd"],
            title=data["title"],
            agent_mode=data.get("agent_mode", "build"),
        )
        session.status = data.get("status", "active")
        session.token_count = data.get("token_count", 0)

        # Load history
        session.history = await self.store.get_messages(session_id)
        return session

    async def update(self, session: Session) -> None:
        """Update session."""
        session.updated_at = datetime.now()
        await self.store.save(session)

    async def close(self, session: Session) -> None:
        """Close a session."""
        session.status = "closed"
        await self.update(session)

    async def list_all(self) -> list[dict[str, Any]]:
        """List all sessions."""
        return await self.store.list_sessions()

    async def delete(self, session_id: str) -> None:
        """Delete a session."""
        await self.store.delete(session_id)


class SessionManager:
    """
    High-level session manager.
    Combines lifecycle with runtime operations.
    """

    def __init__(self, store: SessionStore):
        self.store = store
        self.lifecycle = SessionLifecycle(store)
        self._active_sessions: dict[str, Session] = {}

    async def create_session(
        self,
        cwd: str,
        title: str | None = None,
        agent_mode: str = "build",
    ) -> Session:
        """Create and track a new session."""
        session = await self.lifecycle.create(cwd, title, agent_mode)
        self._active_sessions[session.id] = session
        return session

    async def get_session(self, session_id: str) -> Session | None:
        """Get session, loading from store if needed."""
        if session_id in self._active_sessions:
            return self._active_sessions[session_id]
        session = await self.lifecycle.get(session_id)
        if session:
            self._active_sessions[session_id] = session
        return session

    async def rename_session(self, session_id: str, title: str) -> None:
        """Rename a session (updates in-memory + persisted metadata)."""
        session = await self.get_session(session_id)
        if not session:
            return
        session.title = title
        await self.lifecycle.update(session)

    async def submit_prompt(self, session_id: str, text: str) -> Prompt | None:
        """Submit a prompt to a session."""
        session = await self.get_session(session_id)
        if not session:
            return None
        prompt = session.submit_prompt(text)
        await self.store.append_message(session_id, {
            "role": "user",
            "content": text,
            "timestamp": datetime.now().isoformat(),
        })
        return prompt

    async def add_assistant_message(self, session_id: str, content: str) -> None:
        """Persist assistant message.

        In-memory history is owned by SessionRunner (drain); this only
        writes to disk so resumed sessions see the full conversation.
        """
        session = await self.get_session(session_id)
        if session:
            await self.store.append_message(session_id, {
                "role": "assistant",
                "content": content,
            })

    async def add_assistant_tool_calls(
        self,
        session_id: str,
        tool_calls: list[Any],
    ) -> None:
        """Persist the assistant message that carries tool calls.

        Without this, a resumed session would contain tool results that
        don't follow any tool_calls message, which the model APIs reject.
        """
        session = await self.get_session(session_id)
        if not session:
            return
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.input, ensure_ascii=False, default=str),
                    },
                }
                for tc in tool_calls
            ],
        }
        await self.store.append_message(session_id, msg)

    async def add_tool_message(
        self,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        content: str,
    ) -> None:
        """Persist tool result message.

        In-memory history is owned by SessionRunner (drain) via
        _settle_tool_results; this only writes to disk.
        """
        session = await self.get_session(session_id)
        if session:
            msg = {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "content": content,
            }
            await self.store.append_message(session_id, msg)

    def remove_active(self, session_id: str) -> None:
        """Remove from active sessions."""
        self._active_sessions.pop(session_id, None)

    async def persist_log(self, session: Session) -> None:
        """Persist the session's append-only event log to disk.

        Called by the agent loop (main.py) in a ``finally`` block so the log
        is flushed after every turn, even on error. Phase 1 keeps the legacy
        ``messages/<id>.jsonl`` path; this writes a parallel
        ``sessions/<id>.events.jsonl`` that will become the source of truth
        in a later phase.
        """
        path = self.store.sessions_path / f"{session.id}.events.jsonl"
        await session.log.persist(path)
