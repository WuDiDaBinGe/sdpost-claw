"""Structured Compaction - high-quality context compression."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sdpost_claw.context.epoch import ContextEpoch
    from sdpost_claw.context.registry import Generation


# Compaction template - inspired by opencode's SUMMARY_TEMPLATE
COMPACTION_TEMPLATE = """Output exactly the Markdown structure shown inside <template> and keep the section order unchanged. Do not include the <template> tags in your response.
<template>
## Objective
- [one or two brief sentences describing what the user is trying to accomplish]

## Important Details
- [constraints/preferences, decisions and why, important facts/assumptions, exact context needed to continue, or "(none)"]

## Work State
### Completed
- [finished work, verified facts, or changes made; otherwise "(none)"]

### Active
- [current work, partial changes, or investigation state; otherwise "(none)"]

### Blocked
- [blockers, failing commands, or unknowns; otherwise "(none)"]

## Next Move
1. [immediate concrete action, or "(none)"]
2. [next action if known, or "(none)"]

## Relevant Files
- [file or directory path: why it matters, or "(none)"]
</template>

Rules:
- Keep every section, even when empty.
- Use terse bullets, not prose paragraphs.
- Preserve exact file paths, symbols, commands, error strings, URLs, and identifiers when known.
- Do not mention the summary process or that context was compacted."""


COMPACTION_UPDATE_INSTRUCTIONS = """The <prior-summary> summarizes everything that happened before the <conversation>. Construct a new summary that combines both. The <prior-summary> is discarded after this: anything you do not carry into the new summary is lost.

When combining:
- Carry forward objectives, constraints, user directives, decisions, and parallel workstreams from the <prior-summary> even when the <conversation> does not mention them. Drop only what is finished and no longer needed.
- The <conversation> is more recent than the <prior-summary>. Where they conflict, the conversation wins: state the corrected fact and drop the old claim.
- Add new progress, decisions, constraints, and context from the conversation.
- Move completed work from "Active" to "Completed".
- If a blocker has been resolved, update the summary to reflect that while keeping any details still needed to continue the work.
- Update "Objective" and "Next Move" to reflect the current work state."""


@dataclass
class CompactionConfig:
    """Compaction configuration."""
    enabled: bool = True
    max_tokens: int = 100000
    buffer_tokens: int = 20000
    keep_tokens: int = 8000


@dataclass
class CompactionResult:
    """Compaction result."""
    epoch_id: str
    summary: str
    tokens_before: int
    tokens_after: int


class CompactionEngine:
    """
    Compaction Engine - structured compression inspired by opencode.

    Generates high-quality structured summaries when context exceeds limits.
    """

    def __init__(self, config: CompactionConfig):
        self.config = config
        self.buffer_tokens = config.buffer_tokens
        self.keep_tokens = config.keep_tokens

    def should_compact(self, total_tokens: int) -> bool:
        """Check if compaction is needed."""
        if not self.config.enabled:
            return False
        return total_tokens > (self.config.max_tokens - self.buffer_tokens)

    def format_messages_for_compaction(self, messages: list[dict]) -> str:
        """Format message history for compaction prompt."""
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Handle multi-part content
                text_parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                content = "\n".join(text_parts)
            parts.append(f"[{role}]: {content}")
        return "\n\n".join(parts)

    def build_compaction_prompt(
        self,
        messages: list[dict],
        prior_summary: str | None = None,
    ) -> tuple[str, str]:
        """
        Build compaction prompt.

        Returns: (system_prompt, user_content)
        """
        if prior_summary:
            system = COMPACTION_TEMPLATE + "\n\n" + COMPACTION_UPDATE_INSTRUCTIONS
            user_content = (
                f"<prior-summary>\n{prior_summary}\n</prior-summary>\n\n"
                f"<conversation>\n{self.format_messages_for_compaction(messages)}\n</conversation>"
            )
        else:
            system = COMPACTION_TEMPLATE
            user_content = self.format_messages_for_compaction(messages)

        return system, user_content

    def count_tokens(self, text: str) -> int:
        """Approximate token count."""
        return max(1, len(text) // 3)
