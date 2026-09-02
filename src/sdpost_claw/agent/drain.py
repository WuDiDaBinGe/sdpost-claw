"""Session Drain + Provider-Turn Boundary - core execution model."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sdpost_claw.agent.modes import Agent
from sdpost_claw.agent.permissions import PermissionRuleset
from sdpost_claw.agent.tools import ToolDefinition, ToolContext, ToolResult, ToolRegistry
from sdpost_claw.common.events import Event, EventBus, get_event_bus
from sdpost_claw.common.utils import generate_id
from sdpost_claw.harness import (
    ASSISTANT_MESSAGE,
    CONTEXT_INJECTION,
    TOOL_RESULT,
    USER_MESSAGE,
    Inbox,
    SessionLog,
)
from sdpost_claw.harness.inbox import Inject


@dataclass
class Prompt:
    """User input prompt."""
    id: str = field(default_factory=generate_id)
    text: str = ""
    is_complete: bool = True
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ToolCall:
    """Model tool call."""
    id: str = field(default_factory=generate_id)
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    message_id: str = ""
    permission: str | None = None


@dataclass
class ModelResponse:
    """Model response."""
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    has_tool_calls: bool = False
    usage: dict[str, Any] | None = None
    reasoning: str = ""


@dataclass
class PreparedTurn:
    """Prepared model call."""
    system_context: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    context_update: str | None = None
    admitted: list[Prompt] = field(default_factory=list)
    settled: list[ToolResult] = field(default_factory=list)

    def has_work(self) -> bool:
        """Check if there's work to do."""
        return bool(self.admitted or self.settled)


@dataclass
class DrainResult:
    """Session drain result."""
    status: str = "no_work"  # no_work | tool_execution | text_response | error
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    error: str | None = None


class SessionNotFoundError(Exception):
    """Raised when session is not found."""
    pass


class PromptPromotion:
    """
    Input promotion - atomically promote eligible pending input to Session History.
    """

    def __init__(self, session: "Session"):
        self.session = session

    async def promote(self) -> list[Prompt]:
        """Promote eligible pending input.

        Phase 4: claims next-turn prompts from the session's inbox (draining
        the queue) rather than reading the old ``pending_input`` list. Each
        claimed inbox prompt is rebuilt as a drain :class:`Prompt` for the
        ``admitted`` return value while history + the event log receive the
        user message.
        """
        # Check no pending tool calls before promoting a new user turn.
        if self.session.has_pending_tool_calls():
            return []

        claimed = self.session.inbox.claim_next_turn()
        eligible = [p for p in claimed if p.is_complete]
        if not eligible:
            return []

        admitted: list[Prompt] = []
        for ip in eligible:
            timestamp = datetime.now()
            self.session.history.append({
                "role": "user",
                "content": ip.text,
                "timestamp": timestamp.isoformat(),
            })
            # Phase 1 double-write: mirror to the append-only event log.
            self.session._emit(
                USER_MESSAGE,
                {
                    "content": ip.text,
                    "timestamp": timestamp.isoformat(),
                },
            )
            admitted.append(Prompt(
                id=ip.id, text=ip.text, is_complete=True, timestamp=timestamp
            ))

        return admitted


class SafeProviderTurnBoundary:
    """
    Safe Provider-Turn Boundary - ensures safe model calls.

    Before each model call:
    1. All eligible user input promoted to Session History
    2. All completed tool results settled
    3. Context changes safely incorporated in chronological order
    """

    def __init__(self, session: "Session"):
        self.session = session

    async def prepare(
        self,
        system_context: str,
        tools: list[ToolDefinition],
    ) -> PreparedTurn:
        """Prepare a safe model call."""
        # 1. Promote eligible input
        promotion = PromptPromotion(self.session)
        admitted = await promotion.promote()

        # 2. Settle completed tool results
        settled = await self._settle_tool_results()

        # 3. Drain next-step context injections at the step boundary.
        #    Each injection is logged as an ignorable ``context_injection``
        #    event (audit/replay, no leak into derived messages) and folded
        #    into the model-visible system context so the model sees the
        #    update without an extra user turn ("non-waking inject").
        injections = self.session.drain_injections()
        if injections:
            patch = "\n\n## Context Update\n" + "\n\n".join(
                inj.text for inj in injections
            )
            system_context = system_context + patch

        # 4. Assemble messages
        messages = list(self.session.history)

        # 5. Assemble tools
        tool_dicts = [t.to_dict() for t in tools]

        return PreparedTurn(
            system_context=system_context,
            messages=messages,
            tools=tool_dicts,
            admitted=admitted,
            settled=settled,
        )

    async def _settle_tool_results(self) -> list[ToolResult]:
        """Settle completed tool results."""
        settled = []
        for result in self.session.pending_tool_results:
            self.session.history.append({
                "role": "tool",
                "tool_call_id": result.tool_call_id,
                "name": result.name,
                "content": result.content,
            })
            # Phase 1 double-write: mirror to the append-only event log.
            self.session._emit(
                TOOL_RESULT,
                {
                    "call_id": result.tool_call_id,
                    "name": result.name,
                    "content": result.content,
                    "is_error": result.is_error,
                },
            )
            settled.append(result)
        self.session.pending_tool_results.clear()
        return settled


class Session:
    """
    Session - persistent entity for conversation state.
    """

    def __init__(
        self,
        id: str | None = None,
        cwd: str = "",
        title: str = "New Session",
        agent_mode: str = "build",
    ):
        self.id = id or generate_id()
        self.cwd = cwd
        self.title = title
        self.agent_mode = agent_mode
        self.status: str = "active"
        self.created_at: datetime = datetime.now()
        self.updated_at: datetime = datetime.now()
        self.history: list[dict[str, Any]] = []
        # Append-only event log — single source of truth (Phase 1: double-write
        # alongside the legacy `history` list; later phases switch readers onto
        # `log.derive_messages()` and retire the direct mutations).
        self.log = SessionLog()
        # Phase 4: input inbox. ``next_turn`` holds user prompts awaiting the
        # turn boundary (``submit_prompt`` enqueues here); ``next_step`` holds
        # non-waking context injections (file changes, AGENTS.md updates,
        # scheduler output) drained at the next step boundary by
        # :meth:`drain_injections`. Generalizes the old single ``pending_input``
        # list + the ``reconcile`` "Updated" baseline-patch into one queued form.
        self.inbox = Inbox()
        self.pending_tool_results: list[ToolResult] = []
        self.context_snapshot: dict[str, Any] = {}
        self.baseline_system_context: str = ""
        self.token_count: int = 0
        # Phase 5: structured compaction summary, surfaced into the baseline by
        # ``SummaryContextSource`` once a compaction has run.
        self.summary: str = ""

    def submit_prompt(self, text: str) -> Prompt:
        """Submit a new prompt.

        Enqueues the prompt onto the inbox's next-turn queue (Phase 4). The
        :class:`SafeProviderTurnBoundary` claims it at the turn boundary and
        promotes it to history + the event log. The returned :class:`Prompt`
        keeps the public signature stable for callers (``SessionManager`` /
        ``_process_input``).
        """
        prompt = Prompt(text=text, is_complete=True)
        self.inbox.submit_prompt(text, prompt_id=prompt.id)
        self.updated_at = datetime.now()
        return prompt

    def drain_injections(self) -> list[Inject]:
        """Drain pending next-step context injections at the step boundary.

        Each claimed injection is recorded as an ignorable, non-surface
        ``context_injection`` event (auditable/replayable, but does not leak
        into :meth:`SessionLog.derive_messages`). The caller (the turn
        boundary) folds the text into the model-visible system context so the
        model sees the update without an extra user turn ("non-waking inject").
        """
        claimed = self.inbox.claim_next_step()
        for inj in claimed:
            self._emit(
                CONTEXT_INJECTION,
                {"kind": inj.kind, "text": inj.text, "source": inj.source},
                ignorable=True,
            )
        return claimed

    def has_pending_tool_calls(self) -> bool:
        """Check if there are pending tool calls."""
        return len(self.pending_tool_results) > 0

    def add_tool_result(self, result: ToolResult) -> None:
        """Add a tool result to be settled."""
        self.pending_tool_results.append(result)

    def _emit(
        self,
        type: str,
        data: dict[str, Any] | None = None,
        ignorable: bool = False,
    ) -> None:
        """Double-write helper: append a structured event to the session log.

        Phase 1 keeps the legacy ``history.append`` callsite for backward
        compatibility; this mirror write establishes the log as the future
        source of truth. A failure here propagates (no swallow) so the model
        loop aborts rather than producing inconsistent state.
        """
        self.log.append(type, data, ignorable=ignorable)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict."""
        return {
            "id": self.id,
            "cwd": self.cwd,
            "title": self.title,
            "agent_mode": self.agent_mode,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "token_count": self.token_count,
        }


class SessionRunner:
    """
    Session Runner - executes a single Session Drain.

    Coordinates:
    - Safe Provider-Turn Boundary
    - Model invocation
    - Tool execution
    - Permission checking
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        permission_ruleset: PermissionRuleset,
        model_provider: Any = None,
        event_bus: EventBus | None = None,
    ):
        self.tool_registry = tool_registry
        self.permission_ruleset = permission_ruleset
        self.model_provider = model_provider
        self.event_bus = event_bus or get_event_bus()
        # Phase 4/5 coordinators (optional, injected post-construction so the
        # ``run`` signature stays unchanged). When set, every step reconciles
        # the context snapshot + runs the compaction pressure check — so *all*
        # clients (run/exec via ``_process_input``, serve/desktop via their own
        # loops) get them, not just the terminal path. Left as ``Any`` and
        # duck-typed in ``run`` to keep the agent layer from hard-depending on
        # the context layer.
        self.system_context: Any = None
        self._context_snapshot: Any = None
        self.compaction_bridge: Any = None
        self.summary_source: Any = None

    async def run(
        self,
        session: Session,
        system_context: str,
        force: bool = False,
        on_delta: Any = None,
    ) -> DrainResult:
        """
        Execute a Session Drain.

        Args:
            session: The session to drain
            system_context: Current system context
            force: Execute even without eligible work
            on_delta: Optional callback(kind, chunk) for streamed model
                output (``"text"`` | ``"reasoning"``); None = one-shot.

        Returns:
            DrainResult with execution status
        """
        # 1. Reconcile context against the held snapshot (Phase 4, per-step).
        #    Any ``Updated`` result is routed through the session inbox as a
        #    non-waking ``context_update`` injection so the boundary's
        #    ``drain_injections`` below folds it into the model-visible system
        #    context this very step — no baseline patch, no extra user turn.
        #    The snapshot is lazily initialized on the first step (``initialize``)
        #    so clients that bypass ``Application.initialize_context`` (sidecar,
        #    desktop) still get reconcile. Duck-typed (``text`` + ``snapshot``)
        #    to keep the agent layer from hard-depending on the context layer.
        if self.system_context is not None:
            try:
                if self._context_snapshot is None:
                    gen = await self.system_context.initialize()
                    self._context_snapshot = gen.snapshot
                else:
                    result = await self.system_context.reconcile(
                        self._context_snapshot
                    )
                    if (
                        result is not None
                        and getattr(result, "text", None)
                        and getattr(result, "snapshot", None) is not None
                    ):
                        session.inbox.inject(
                            "context_update",
                            result.text,
                            "system_context",
                        )
                        self._context_snapshot = result.snapshot
            except Exception:
                pass

        # 2. Prepare Safe Provider-Turn Boundary
        boundary = SafeProviderTurnBoundary(session)
        tools = self.tool_registry.list_all()
        prepared = await boundary.prepare(system_context, tools)

        # 3. Check if there's work
        if not force and not prepared.has_work():
            return DrainResult(status="no_work")

        # 4. Compaction pressure check (Phase 5, per-step). If the
        #    SessionLog-derived history has crossed the threshold, summarize
        #    it via the *existing single provider* (央企国产-only) and stash
        #    the summary on ``session.summary``. The summary is pushed into
        #    ``SummaryContextSource`` so the next step's reconcile surfaces it
        #    as "## Previous Session Summary" via the Phase 4 inject path
        #    above. A failure here is swallowed — compaction must never break
        #    the turn. Placed after prepare (so the promoted user message is
        #    in the derived history) and before the model call.
        if self.compaction_bridge is not None:
            try:
                if await self.compaction_bridge.maybe_compact(session):
                    if self.summary_source is not None:
                        self.summary_source.update_summary(session.summary)
            except Exception:
                pass

        # 5. Call model
        if not self.model_provider:
            return DrainResult(
                status="error",
                error="No model provider configured",
            )

        try:
            if on_delta is not None and hasattr(self.model_provider, "generate_stream"):
                response = await self.model_provider.generate_stream(
                    system=prepared.system_context,
                    messages=prepared.messages,
                    tools=prepared.tools,
                    on_delta=on_delta,
                )
            else:
                response = await self.model_provider.generate(
                    system=prepared.system_context,
                    messages=prepared.messages,
                    tools=prepared.tools,
                )
        except Exception as e:
            return DrainResult(
                status="error",
                error=f"Model error: {e}",
            )

        # 6. Handle response
        if response.has_tool_calls:
            results = await self._execute_tools(response.tool_calls, session)
            return DrainResult(
                status="tool_execution",
                tool_calls=response.tool_calls,
                tool_results=results,
            )
        else:
            # Add assistant response to history
            session.history.append({
                "role": "assistant",
                "content": response.text,
            })
            # Phase 1 double-write: mirror to the append-only event log.
            session._emit(
                ASSISTANT_MESSAGE,
                {"content": response.text},
            )
            return DrainResult(
                status="text_response",
                content=response.text,
            )

    async def _execute_tools(
        self,
        tool_calls: list[ToolCall],
        session: Session,
    ) -> list[ToolResult]:
        """Execute tool calls with permission checking."""
        results: list[ToolResult] = []

        # Add assistant message with tool calls to history
        # NOTE: arguments must be a JSON string, not a Python repr
        tool_calls_json = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.input, ensure_ascii=False, default=str),
                },
            }
            for tc in tool_calls
        ]
        session.history.append({
            "role": "assistant",
            "content": "",
            "tool_calls": tool_calls_json,
        })
        # Phase 1 double-write: mirror to the append-only event log.
        session._emit(
            ASSISTANT_MESSAGE,
            {"content": "", "tool_calls": tool_calls_json},
        )

        # Phase 3: each call runs through the three-stage tool pipeline
        # (pre-permission + monotonic guards → execute → post + finalize).
        # Results come back uniformly; the boundary settles them into
        # history + the tool_result surface event.
        from sdpost_claw.harness.tool_pipeline import (
            ToolExecution,
            run as run_pipeline,
        )

        for call in tool_calls:
            execution = ToolExecution(
                call_id=call.id,
                name=call.name,
                arguments=call.input,
                permission=call.permission or self._infer_permission(call.name),
            )
            tool = self.tool_registry.get(call.name)
            context = ToolContext(
                session_id=session.id,
                tool_call_id=call.id,
                cwd=session.cwd,
            )
            result = await run_pipeline(
                execution=execution,
                tool=tool,
                context=context,
                session=session,
                ruleset=self.permission_ruleset,
            )
            results.append(result)
            session.add_tool_result(result)

        return results

    def _infer_permission(self, tool_name: str) -> str:
        """Infer permission from tool name."""
        permission_map = {
            "read": "file.read",
            "write": "file.write",
            "edit": "file.edit",
            "glob": "file.read",
            "grep": "file.read",
            "bash": "shell.execute",
            "webfetch": "network.request",
            "websearch": "network.search",
            "question": "user.interact",
        }
        return permission_map.get(tool_name, f"tool.{tool_name}")
