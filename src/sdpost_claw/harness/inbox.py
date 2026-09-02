"""Input inbox — next-turn / next-step queues with a non-waking inject path.

Borrowed from deepseek-harness's ``Inbox``
(``packages/core/agent/src/inbox.ts``):

* ``next_turn`` — prompts that should open a **new turn** (user follow-up).
* ``next_step`` — inputs that merge at the **next step boundary** without
  waking the driver (context injection: file-change notices, AGENTS.md
  updates, skill content, scheduler output).

This generalizes sdpost-claw's current single ``submit_prompt`` path and the
``SystemContextRegistry.reconcile()`` "Updated" result (which today patches
the baseline directly) into a queued, non-waking injection.

``claim_next_turn`` / ``claim_next_step`` drain each queue (they are not
peek-only) so a given item is merged exactly once at its boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from sdpost_claw.common.utils import generate_id


@dataclass
class Prompt:
    """A user prompt enqueued for the next turn."""

    id: str
    text: str
    is_complete: bool = True


@dataclass
class Inject:
    """A context injection enqueued for the next step (does not wake)."""

    kind: str  # e.g. "context_update", "skill", "file_change"
    text: str
    source: str = ""


class Inbox:
    """Two-list input queue for a session.

    ``submit_prompt`` feeds ``next_turn`` (opens a new turn when the boundary
    promotes it). ``inject`` feeds ``next_step`` (merged at the next step
    boundary without waking the driver — the "non-waking inject" path).
    """

    def __init__(self) -> None:
        self._next_turn: list[Prompt] = []
        self._next_step: list[Inject] = []

    @property
    def pending_turns(self) -> int:
        """Number of queued next-turn prompts (peek, does not drain)."""
        return len(self._next_turn)

    @property
    def pending_injections(self) -> int:
        """Number of queued next-step injections (peek, does not drain)."""
        return len(self._next_step)

    def submit_prompt(self, text: str, prompt_id: str = "") -> Prompt:
        """Enqueue a user prompt for the next turn."""
        prompt = Prompt(id=prompt_id or generate_id(), text=text, is_complete=True)
        self._next_turn.append(prompt)
        return prompt

    def inject(self, kind: str, text: str, source: str = "") -> None:
        """Enqueue a context injection for the next step; does NOT wake."""
        self._next_step.append(Inject(kind=kind, text=text, source=source))

    def claim_next_turn(self) -> list[Prompt]:
        """Claim (drain) all next-turn prompts. Called at prepare boundary."""
        items = list(self._next_turn)
        self._next_turn.clear()
        return items

    def claim_next_step(self) -> list[Inject]:
        """Claim (drain) all next-step injections. Called at step boundary."""
        items = list(self._next_step)
        self._next_step.clear()
        return items
