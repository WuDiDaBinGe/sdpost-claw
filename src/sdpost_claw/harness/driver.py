"""Turn/step state machine — the harness driver.

Borrowed from deepseek-harness's ``ReactLoopAgent``
(``packages/core/agent-loop/src/agent.ts``):

* A **turn** = zero or more steps, bounded by ``turn_start`` / ``turn_end``
  events with a structured ``reason`` (completed / blocked / aborted / error
  / max_tokens / max_steps / interrupted).
* A **step** = one model request + its tool calls, bounded by ``step_start`` /
  ``step_end``.
* A **soft-stopping checkpoint** (dsh ``agent/turn-stopping``) lets registered
  ``pre_step`` hooks veto continuation before each step, replacing the legacy
  hard-coded ``max_iterations`` ceiling as the *primary* control. The old
  ceiling survives only as a safety backstop (``MAX_STEPS``).

The driver is a thin lifecycle + policy layer: it owns the turn/step boundary
events and consults hooks, but does **not** call the model — that stays in
``SessionRunner.run`` so the per-step executor's signature is untouched. The
agent loop in ``main.py._process_input`` drives ``turn_start`` →
``step_start`` → ``SessionRunner.run`` → ``step_end`` → ``turn_end``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from sdpost_claw.harness.events import (
    STEP_END,
    STEP_START,
    TURN_END,
    TURN_START,
)


# Turn-end reason (mirrors dsh TurnEndReason, plus MAX_STEPS below).
COMPLETED = "completed"
BLOCKED = "blocked"
ABORTED = "aborted"
ERROR = "error"
MAX_TOKENS = "max_tokens"
INTERRUPTED = "interrupted"
# Domestic extension: the safety backstop (max_iterations) was hit. Distinct
# from a hook-driven soft stop, which carries the hook's own reason.
MAX_STEPS = "max_steps"


@dataclass
class Continue:
    """``pre_turn`` / ``pre_step`` hook result: proceed with the turn/step.

    ``messages`` is an optional rewrite hint reserved for a future phase (dsh
    ``pre-step`` may return a rewritten step message list); it is ignored by
    the driver today.
    """

    messages: list[dict] | None = None


@dataclass
class Abort:
    """``pre_turn`` / ``pre_step`` hook result: stop the turn with a reason."""

    reason: str = BLOCKED


# A lifecycle hook. Returns Continue to proceed, Abort to stop the turn.
# (In dsh this is a Koa-style waterfall that must call next(); we keep the
# simpler first-Abort-wins semantics — hooks are deny-only checkpoints.)
LifecycleHook = Callable[["SessionDriver"], Awaitable["Continue | Abort"]]


class SessionDriver:
    """Owns the turn/step lifecycle for one session.

    Wraps a :class:`~sdpost_claw.agent.drain.Session` (whose ``.log`` is the
    append-only :class:`SessionLog`) and emits durable, ignorable turn/step
    boundary events. Registered ``pre_turn`` / ``pre_step`` hooks are consulted
    before a turn opens / before each step; the first ``Abort`` wins.

    The driver never calls the model and never executes tools — it only
    structures the loop. This keeps ``SessionRunner.run`` the single per-step
    executor and leaves its signature intact.
    """

    def __init__(self, session: Any) -> None:
        self.session = session
        self._pre_turn_hooks: list[LifecycleHook] = []
        self._pre_step_hooks: list[LifecycleHook] = []

    def on_pre_turn(self, hook: LifecycleHook) -> None:
        """Register a hook run before a turn opens."""
        self._pre_turn_hooks.append(hook)

    def on_pre_step(self, hook: LifecycleHook) -> None:
        """Register a hook run before each step (soft-stopping checkpoint)."""
        self._pre_step_hooks.append(hook)

    async def turn_start(self, reason: str = "user") -> Abort | None:
        """Open a turn.

        Emits ``turn_start`` (ignorable), then runs registered ``pre_turn``
        hooks in order. Returns the first ``Abort`` (if any) so the caller can
        skip the step loop and go straight to :meth:`turn_end` — the turn is
        already open and will be closed exactly once by the caller's
        ``finally``. Returns ``None`` to proceed.

        Note: this method does *not* emit ``turn_end`` on abort; the caller
        owns the single ``turn_end`` write (avoids a double-close).
        """
        self.session.log.append(
            TURN_START, {"reason": reason}, ignorable=True
        )
        for hook in self._pre_turn_hooks:
            verdict = await hook(self)
            if isinstance(verdict, Abort):
                return verdict
        return None

    async def turn_end(self, reason: str = COMPLETED) -> None:
        """Close a turn with a structured reason.

        Emits ``turn_end`` (ignorable). The caller is responsible for calling
        this exactly once per :meth:`turn_start` (typically in a ``finally``
        block) so the log always has balanced turn boundaries — even on error.
        """
        self.session.log.append(
            TURN_END, {"reason": reason}, ignorable=True
        )

    async def step_start(self) -> Continue | Abort:
        """Open a step, consulting ``pre_step`` hooks first.

        Hooks run *before* ``step_start`` is emitted so a vetoed step leaves
        no orphan boundary event in the log (the step never began). Returns the
        first ``Abort`` (no event emitted) or :class:`Continue` (step_start
        emitted).
        """
        for hook in self._pre_step_hooks:
            verdict = await hook(self)
            if isinstance(verdict, Abort):
                return verdict
        self.session.log.append(STEP_START, {}, ignorable=True)
        return Continue()

    async def step_end(self) -> None:
        """Close a step. Emits ``step_end`` (ignorable).

        Pair with :meth:`step_start`; call in a position that runs after
        ``SessionRunner.run`` for every step that opened.
        """
        self.session.log.append(STEP_END, {}, ignorable=True)
