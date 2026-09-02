"""Context Epoch - immutable time span of system context."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sdpost_claw.common.utils import generate_id
from sdpost_claw.context.registry import Generation, Snapshot


@dataclass
class ContextEpoch:
    """
    Context Epoch - a time span where the initial rendered System Context remains unchanged.

    Ends during compaction, session migration, or incompatible context transitions.
    Each Epoch has an immutable Baseline System Context.
    """

    id: str = field(default_factory=generate_id)
    generation: Generation = field(default_factory=lambda: Generation(baseline="", snapshot=Snapshot()))
    started_at: datetime = field(default_factory=datetime.now)
    ended_at: datetime | None = None
    end_reason: str | None = None

    @classmethod
    def initial(cls) -> ContextEpoch:
        """Create an initial empty epoch."""
        return cls()

    @property
    def is_active(self) -> bool:
        """Check if epoch is still active."""
        return self.ended_at is None

    def end(self, reason: str) -> None:
        """End this epoch."""
        self.ended_at = datetime.now()
        self.end_reason = reason

    @property
    def baseline(self) -> str:
        """Get baseline system context."""
        return self.generation.baseline

    @property
    def snapshot(self) -> Snapshot:
        """Get context snapshot."""
        return self.generation.snapshot
