"""Compaction bridge — wires the existing (previously dead) CompactionEngine in.

``context/compaction.py`` defines :class:`CompactionEngine` with
``should_compact`` / ``build_compaction_prompt`` but, before this phase, nothing
instantiated it — it was dead code. This bridge connects it to the harness
driver's per-step pressure test:

* estimate the current token pressure from the :class:`SessionLog`-derived
  message history (dsh ``agent/pre-step`` pressure test) and run
  :meth:`CompactionEngine.should_compact`,
* on a hit, derive the current messages from the :class:`SessionLog`, build the
  compaction prompt, call the model via the **existing single provider** (no
  new adapter — the 央企国产-only constraint holds), and store the summary on
  ``session.summary`` for :class:`SummaryContextSource` to surface,
* emit an ignorable, non-surface ``compaction_occurred`` log event so the log
  stays replayable/auditable without leaking the compaction into derived
  message history.

How the summary reaches the model (next turn): after ``session.summary`` is
set, the caller updates :class:`SummaryContextSource`. That source was
``Unavailable`` at ``initialize`` time (empty summary) and so is absent from
the held context snapshot; the next turn's ``reconcile`` therefore sees it as
a newly-available source, returns ``Updated`` carrying ``## Previous Session
Summary\\n{summary}``, and the Phase 4 inbox path folds that text into the
model-visible system context at the step boundary — no baseline ``replace``
needed. The optional ``SessionLog.replace(start, end)`` surface op (dsh
``SurfaceOp.replace``) that would physically swap the compacted middle of the
derived history for the summary is left for a later phase.

Re-fire guard: because the deferred surface op means the derived history does
not physically shrink after a compaction, a bare threshold check would re-fire
every step. The bridge (stateful coordinator) remembers the token level at the
last compaction and refuses to compact again until the conversation has grown
by at least one buffer past it. The policy (:class:`CompactionEngine`) stays
stateless, per the plan's "don't touch CompactionConfig / template" rule.
"""

from __future__ import annotations

from typing import Any

from sdpost_claw.harness.events import COMPACTION_OCCURRED


class CompactionBridge:
    """Connects :class:`CompactionEngine` to the harness driver.

    Attributes:
        engine: a :class:`~sdpost_claw.context.compaction.CompactionEngine`
            (holds the thresholds + prompt templates).
        provider: the single model provider (an
            :class:`~sdpost_claw.runtime.providers.OpenAIProvider`) used both
            for normal turns and for the compaction model call.
        _last_compaction_tokens: token level at the last successful
            compaction (0 = never compacted). Re-fire guard state.
    """

    def __init__(self, engine: Any, provider: Any = None) -> None:
        self.engine = engine
        self.provider = provider
        self._last_compaction_tokens: int = 0

    async def maybe_compact(self, session: Any) -> bool:
        """Run compaction if the pressure threshold is crossed.

        Args:
            session: the active :class:`~sdpost_claw.agent.drain.Session`
                (its ``.log`` is the source of truth; its ``.summary`` is the
                sink for the new summary).

        Returns:
            True if a compaction ran and ``session.summary`` was updated;
            False otherwise (disabled / under threshold / nothing to compact /
            re-fire guard / model call failed / empty summary). A False
            return never breaks the turn — the model loop continues with the
            un-compacted history.
        """
        if self.engine is None or self.provider is None:
            return False

        messages = session.log.derive_messages()
        if not messages:
            return False

        total_tokens = self._estimate_tokens(messages)

        if not self.engine.should_compact(total_tokens):
            return False

        # Re-fire guard: don't compact again until the conversation has grown
        # by at least one buffer past the last compaction point.
        if (
            self._last_compaction_tokens
            and total_tokens <= self._last_compaction_tokens + self.engine.buffer_tokens
        ):
            return False

        system, user_content = self.engine.build_compaction_prompt(
            messages, prior_summary=session.summary or None
        )

        try:
            response = await self.provider.generate(
                system=system,
                messages=[{"role": "user", "content": user_content}],
                tools=[],
            )
        except Exception:
            # A failed compaction must never break the turn — the model loop
            # continues with the un-compacted history.
            return False

        summary = (getattr(response, "text", "") or "").strip()
        if not summary:
            return False

        session.summary = summary
        self._last_compaction_tokens = total_tokens
        session._emit(
            COMPACTION_OCCURRED,
            {
                "tokens_before": total_tokens,
                "summary_preview": summary[:200],
            },
            ignorable=True,
        )
        return True

    def _estimate_tokens(self, messages: list[dict]) -> int:
        """Approximate token count over a derived message list.

        Mirrors :meth:`CompactionEngine.format_messages_for_compaction`'s
        handling of str / multi-part content so the pressure estimate lines
        up with what the compaction prompt will actually see.
        """
        total = 0
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                total += self.engine.count_tokens(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += self.engine.count_tokens(part.get("text", ""))
        return total
