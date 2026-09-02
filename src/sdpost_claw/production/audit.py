"""Audit Log - security and operation auditing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sdpost_claw.agent.permissions import PermissionDecision
from sdpost_claw.production.events import Event, EventType, EventStore


class AuditLog:
    """
    Audit Log - records all security-relevant operations.

    Records:
    - Permission checks
    - Context changes
    - Tool executions
    - Model invocations
    """

    def __init__(self, event_store: EventStore):
        self.event_store = event_store

    async def log_permission_check(
        self,
        session_id: str,
        action: str,
        decision: PermissionDecision,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Record permission check."""
        event_type = (
            EventType.PERMISSION_DENIED if decision.effect == "deny"
            else EventType.PERMISSION_ASKED if decision.effect == "ask"
            else EventType.PERMISSION_CHECKED
        )
        event = Event(
            type=event_type,
            session_id=session_id,
            timestamp=datetime.now(),
            data={
                "action": action,
                "effect": decision.effect,
                "rule": decision.rule.action if decision.rule else None,
                "context": context,
            },
        )
        await self.event_store.append(event)

    async def log_context_change(
        self,
        session_id: str,
        change_type: str,
        source_key: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Record context change."""
        event = Event(
            type=EventType.CONTEXT_UPDATED if change_type == "update" else EventType.CONTEXT_REPLACED,
            session_id=session_id,
            timestamp=datetime.now(),
            data={
                "change_type": change_type,
                "source_key": source_key,
                "details": details,
            },
        )
        await self.event_store.append(event)

    async def log_tool_call(
        self,
        session_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        duration_ms: int = 0,
    ) -> None:
        """Record tool call."""
        event = Event(
            type=EventType.TOOL_CALLED,
            session_id=session_id,
            timestamp=datetime.now(),
            data={
                "tool_name": tool_name,
                "input": tool_input,
                "duration_ms": duration_ms,
            },
        )
        await self.event_store.append(event)

    async def log_tool_result(
        self,
        session_id: str,
        tool_name: str,
        is_error: bool = False,
        is_truncated: bool = False,
    ) -> None:
        """Record tool result."""
        event = Event(
            type=EventType.TOOL_RESULT_SETTLED,
            session_id=session_id,
            timestamp=datetime.now(),
            data={
                "tool_name": tool_name,
                "is_error": is_error,
                "is_truncated": is_truncated,
            },
        )
        await self.event_store.append(event)

    async def log_model_request(
        self,
        session_id: str,
        model: str,
        message_count: int,
        tool_count: int,
    ) -> None:
        """Record model request."""
        event = Event(
            type=EventType.MODEL_REQUEST,
            session_id=session_id,
            timestamp=datetime.now(),
            data={
                "model": model,
                "message_count": message_count,
                "tool_count": tool_count,
            },
        )
        await self.event_store.append(event)

    async def log_model_response(
        self,
        session_id: str,
        model: str,
        usage: dict[str, Any] | None = None,
    ) -> None:
        """Record model response."""
        event = Event(
            type=EventType.MODEL_RESPONSE,
            session_id=session_id,
            timestamp=datetime.now(),
            data={
                "model": model,
                "usage": usage,
            },
        )
        await self.event_store.append(event)

    async def log_error(
        self,
        session_id: str,
        error_type: str,
        message: str,
    ) -> None:
        """Record error."""
        event = Event(
            type=EventType.MODEL_ERROR,
            session_id=session_id,
            timestamp=datetime.now(),
            data={
                "error_type": error_type,
                "message": message,
            },
        )
        await self.event_store.append(event)

    async def get_audit_trail(
        self,
        session_id: str,
        event_type: EventType | None = None,
    ) -> list[Event]:
        """Get audit trail for a session."""
        if event_type:
            return await self.event_store.get_events_by_type(session_id, event_type)
        return await self.event_store.replay(session_id)
