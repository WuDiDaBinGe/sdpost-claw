"""Event bus for inter-module communication."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable


@dataclass
class Event:
    """Event data."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    source: str | None = None


EventHandler = Callable[[Event], Awaitable[None]]


class EventBus:
    """Async event bus for module communication."""

    def __init__(self):
        self._handlers: dict[str, list[EventHandler]] = {}
        self._global_handlers: list[EventHandler] = []

    def subscribe(self, event_type: str, handler: EventHandler) -> Callable[[], None]:
        """Subscribe to an event type. Returns unsubscribe function."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

        def unsubscribe():
            self._handlers[event_type].remove(handler)

        return unsubscribe

    def subscribe_all(self, handler: EventHandler) -> Callable[[], None]:
        """Subscribe to all events."""
        self._global_handlers.append(handler)

        def unsubscribe():
            self._global_handlers.remove(handler)

        return unsubscribe

    async def publish(self, event: Event) -> None:
        """Publish an event to all subscribers."""
        # Global handlers
        for handler in self._global_handlers:
            try:
                await handler(event)
            except Exception:
                pass  # Don't let handlers crash the event bus

        # Type-specific handlers
        handlers = self._handlers.get(event.type, [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                pass

    async def publish_and_wait(self, event: Event) -> None:
        """Publish and wait for all handlers to complete."""
        handlers = list(self._global_handlers)
        handlers.extend(self._handlers.get(event.type, []))

        await asyncio.gather(
            *[handler(event) for handler in handlers],
            return_exceptions=True,
        )


# Global event bus instance
_global_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get global event bus instance."""
    global _global_event_bus
    if _global_event_bus is None:
        _global_event_bus = EventBus()
    return _global_event_bus
