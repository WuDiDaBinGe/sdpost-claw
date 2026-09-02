"""Context System - System Context Registry, Context Epoch, and Context Sources."""

from sdpost_claw.context.source import (
    ContextSource,
    Unavailable,
    DateContextSource,
    ProjectInstructionsContextSource,
    WorkspaceMemoryContextSource,
    UserPreferencesContextSource,
    SummaryContextSource,
)
from sdpost_claw.context.registry import (
    SystemContextRegistry,
    Generation,
    Snapshot,
    SourceSnapshot,
    ReconcileResult,
    Unchanged,
    Updated,
    ReplacementReady,
    ReplacementBlocked,
    ReplacementResult,
)
from sdpost_claw.context.epoch import ContextEpoch
from sdpost_claw.context.midconv import (
    MidConversationSystemMessage,
    MidConversationSystemMessageHandler,
)

__all__ = [
    "ContextSource",
    "Unavailable",
    "DateContextSource",
    "ProjectInstructionsContextSource",
    "WorkspaceMemoryContextSource",
    "UserPreferencesContextSource",
    "SummaryContextSource",
    "SystemContextRegistry",
    "Generation",
    "Snapshot",
    "SourceSnapshot",
    "ReconcileResult",
    "Unchanged",
    "Updated",
    "ReplacementReady",
    "ReplacementBlocked",
    "ReplacementResult",
    "ContextEpoch",
    "MidConversationSystemMessage",
    "MidConversationSystemMessageHandler",
]
