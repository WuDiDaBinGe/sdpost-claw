"""Mid-Conversation System Message - state change notifications during conversation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sdpost_claw.context.registry import SystemContextRegistry, Snapshot, Unchanged, Updated, ReplacementReady, ReplacementBlocked


@dataclass
class MidConversationSystemMessage:
    """
    Mid-Conversation System Message - inspired by opencode design.

    Used to deliver state change instructions during conversation.
    - Incorporated into Session History in chronological order
    - Synchronized with Context Snapshot updates
    - Handled in Safe Provider-Turn Boundary
    """

    text: str
    source_key: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "role": "system",
            "content": self.text,
            "metadata": {
                "type": "mid_conversation_update",
                "source_key": self.source_key,
                "timestamp": self.timestamp.isoformat(),
            },
        }


class ContextUnavailableError(Exception):
    """Raised when context is unavailable and blocks the turn."""
    def __init__(self, unavailable_keys: list[str]):
        self.unavailable_keys = unavailable_keys
        super().__init__(f"Context unavailable: {unavailable_keys}")


class MidConversationSystemMessageHandler:
    """
    Mid-Conversation System Message Handler.

    Coordinates context changes before each Provider Turn.
    """

    def __init__(self, system_context: SystemContextRegistry):
        self.system_context = system_context

    async def handle(
        self,
        snapshot: Snapshot,
    ) -> tuple[MidConversationSystemMessage | None, Snapshot | None]:
        """
        Handle context reconciliation.

        1. Get current snapshot
        2. Reconcile context
        3. If changed, generate Mid-Conversation System Message
        4. Update snapshot

        Returns: (message, new_snapshot) - either may be None
        """
        result = await self.system_context.reconcile(snapshot)

        if isinstance(result, Unchanged):
            return None, None

        elif isinstance(result, Updated):
            # Update snapshot
            return MidConversationSystemMessage(
                text=result.text,
                source_key=None,
            ), result.snapshot

        elif isinstance(result, ReplacementReady):
            # Need to replace baseline, start new Epoch
            return None, None

        elif isinstance(result, ReplacementBlocked):
            # Unavailable context, block
            raise ContextUnavailableError(result.unavailable_keys)

        return None, None
