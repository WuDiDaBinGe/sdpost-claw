"""Session event log vocabulary — single source of truth for conversation state.

Borrowed from deepseek-harness's ``SessionEventMap``
(``packages/core/session/src/types.ts``), simplified to Python dataclasses.

The event log is append-only; the model-visible message history is *derived*
from it (see :func:`SessionLog.derive_messages`). Only the ``SURFACE_TYPES``
events contribute to derived history — the rest (turn/step boundaries, request
headers, compaction markers) are durable observations that do not reach the
model but make the log replayable and auditable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# --- Event type constants (mirrors dsh SessionEventMap, minus surface/replace) ---

TURN_START = "turn_start"
TURN_END = "turn_end"
STEP_START = "step_start"
STEP_END = "step_end"

USER_MESSAGE = "user_message"
ASSISTANT_CHUNK = "assistant_chunk"  # reserved for future streaming
ASSISTANT_MESSAGE = "assistant_message"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"

REQUEST_HEADER = "request_header"
SESSION_END = "session_end"
COMPACTION_OCCURRED = "compaction_occurred"
CONTEXT_INJECTION = "context_injection"

# Surface event types — those that contribute to derived message history.
# (Borrowed from dsh SurfaceEventType; a SurfaceOp/replace mechanism is added
# in a later phase to support compaction.)
SURFACE_TYPES = frozenset({USER_MESSAGE, ASSISTANT_MESSAGE, TOOL_RESULT})


def snapshot(value: Any) -> Any:
    """Return a JSON-native snapshot of ``value``.

    Mirrors dsh ``snapshotJsonValue``: the value is materialized through a
    JSON round-trip so the log never holds a reference to the caller's mutable
    input. Non-serializable values are stringified (matching the codebase's
    existing ``json.dumps(default=str)`` convention) rather than crashing the
    write — a bad event fails loudly at the *consumer*, not at append time.
    """
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


@dataclass
class SessionEvent:
    """A single append-only log entry.

    Attributes:
        seq: Monotonically increasing sequence number within a session.
        time: Wall-clock time of append.
        type: One of the ``*_MESSAGE`` / ``TURN_*`` / ... constants above.
        data: JSON-native payload; shape depends on ``type``.
        ignorable: If True, a reader that does not recognize ``type`` may skip
            the event rather than rejecting the whole log rebuild. Defaults to
            False — unknown events fail closed (mirrors dsh ``ignorable``).
    """

    seq: int
    time: datetime
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    ignorable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "time": self.time.isoformat(),
            "type": self.type,
            "data": self.data,
            "ignorable": self.ignorable,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SessionEvent":
        return cls(
            seq=d["seq"],
            time=datetime.fromisoformat(d["time"]),
            type=d["type"],
            data=d.get("data", {}),
            ignorable=d.get("ignorable", False),
        )
