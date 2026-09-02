"""Harness layer — the orchestration core of sdpost-claw.

This package is being migrated, phase by phase, toward the architecture of
deepseek-harness (see ``deepseek-harness/packages/core``). The harness layer
owns:

* the **turn/step state machine** that drives the model loop (``driver.py``),
* the **append-only session event log** that is the single source of truth for
  conversation state (``events.py`` + ``session_log.py``),
* the **tool execution pipeline** (``tool_pipeline.py``),
* the **input inbox** with next-turn / next-step queues (``inbox.py``),
* the **compaction bridge** that wires ``CompactionEngine`` into the loop
  (``compaction_bridge.py``).

Constraint (央企国产-only): the model API layer stays a single ``OpenAIProvider``
for all domestic vendors; this package never imports a foreign adapter.
``deepseek-harness/`` is a read-only reference project — none of its files are
modified by this work.
"""

from sdpost_claw.harness.events import (
    ASSISTANT_CHUNK,
    ASSISTANT_MESSAGE,
    COMPACTION_OCCURRED,
    CONTEXT_INJECTION,
    REQUEST_HEADER,
    SESSION_END,
    STEP_END,
    STEP_START,
    SURFACE_TYPES,
    TOOL_CALL,
    TOOL_RESULT,
    TURN_END,
    TURN_START,
    USER_MESSAGE,
    SessionEvent,
    snapshot,
)
from sdpost_claw.harness.inbox import Inbox, Inject as InboxInject
from sdpost_claw.harness.session_log import SessionLog

__all__ = [
    "SessionLog",
    "Inbox",
    "InboxInject",
    "SessionEvent",
    "snapshot",
    "TURN_START",
    "TURN_END",
    "STEP_START",
    "STEP_END",
    "USER_MESSAGE",
    "ASSISTANT_MESSAGE",
    "ASSISTANT_CHUNK",
    "TOOL_CALL",
    "TOOL_RESULT",
    "REQUEST_HEADER",
    "SESSION_END",
    "COMPACTION_OCCURRED",
    "CONTEXT_INJECTION",
    "SURFACE_TYPES",
]
