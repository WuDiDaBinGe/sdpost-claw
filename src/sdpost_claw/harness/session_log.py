"""Append-only session event log — the single source of truth.

Borrowed from deepseek-harness's ``Session`` class
(``packages/core/session/src/index.ts``):

* :meth:`append` is the **only** write point — it snapshots the data and
  assigns a monotonic ``seq``. Callers never hold a reference to the stored
  payload.
* :meth:`derive_messages` projects the OpenAI-format message history from the
  ordered *surface* events (``user_message`` / ``assistant_message`` /
  ``tool_result``). This is the dsh ``deriveMessages`` pattern: "model-visible
  means logged".

In this phase the log is written in parallel with the legacy
``Session.history`` list (double-write) and persisted to a JSONL file; later
phases switch readers onto :meth:`derive_messages` and retire the direct
history mutations.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import aiofiles

from sdpost_claw.harness.events import (
    ASSISTANT_MESSAGE,
    TOOL_RESULT,
    USER_MESSAGE,
    SessionEvent,
    snapshot,
)


class SessionLog:
    """Append-only event log for one session."""

    def __init__(self) -> None:
        self._events: list[SessionEvent] = []
        self._seq: int = 0
        self._persisted_seq: int = 0  # highest seq durably persisted

    def append(
        self,
        type: str,
        data: dict[str, Any] | None = None,
        ignorable: bool = False,
    ) -> SessionEvent:
        """The only write point.

        Snapshots ``data`` so subsequent mutation by the caller cannot corrupt
        the log. Returns the frozen event.
        """
        self._seq += 1
        event = SessionEvent(
            seq=self._seq,
            time=datetime.now(),
            type=type,
            data=snapshot(data or {}),
            ignorable=ignorable,
        )
        self._events.append(event)
        return event

    def events(self) -> list[SessionEvent]:
        """Return a fresh list of all events (callers may mutate freely)."""
        return list(self._events)

    def own_events(self) -> list[SessionEvent]:
        """All events appended by this process.

        Reserved for a future fork/resume split where a session may inherit a
        prefix of inherited events from a parent session (dsh
        ``inheritedEventCount`` / ``firstLiveSeq``). For now identical to
        :meth:`events`.
        """
        return list(self._events)

    def derive_messages(self) -> list[dict[str, Any]]:
        """Project OpenAI-format message history from the ordered surface events.

        Mirrors dsh ``Session.deriveMessages``. Only ``user_message`` /
        ``assistant_message`` / ``tool_result`` events contribute; ordering
        follows log order. Non-surface events (turn/step boundaries, request
        headers, compaction markers) are skipped here but remain in the log.
        """
        messages: list[dict[str, Any]] = []
        for event in self._events:
            if event.type == USER_MESSAGE:
                messages.append(
                    {"role": "user", "content": event.data.get("content", "")}
                )
            elif event.type == ASSISTANT_MESSAGE:
                msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": event.data.get("content", ""),
                }
                tool_calls = event.data.get("tool_calls")
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                messages.append(msg)
            elif event.type == TOOL_RESULT:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": event.data.get("call_id", ""),
                        "name": event.data.get("name", ""),
                        "content": event.data.get("content", ""),
                    }
                )
        return messages

    async def persist(self, path: Path) -> None:
        """Append any not-yet-persisted events to the JSONL file at ``path``.

        Incremental and idempotent: only events with ``seq`` above the
        last-persisted watermark are written, then the watermark advances.
        Uses append mode so a resumed session (whose in-memory log starts
        empty) never overwrites prior events — Phase 1 still reads resume
        state from the legacy ``messages/<id>.jsonl``; restoring the event
        log itself on resume is a later phase.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        new_events = [e for e in self._events if e.seq > self._persisted_seq]
        if not new_events:
            return
        async with aiofiles.open(path, "a", encoding="utf-8") as f:
            for event in new_events:
                await f.write(
                    json.dumps(event.to_dict(), ensure_ascii=False) + "\n"
                )
        self._persisted_seq = new_events[-1].seq

    @classmethod
    def from_events(cls, events: list[SessionEvent]) -> "SessionLog":
        """Reconstruct a log from a list of events (e.g. loaded from disk)."""
        log = cls()
        for event in events:
            log._events.append(event)
            if event.seq > log._seq:
                log._seq = event.seq
        return log

    def __len__(self) -> int:
        return len(self._events)
