"""Tool execution pipeline — three-stage pre / execute / post with monotonic guards.

Borrowed from deepseek-harness's ``ToolRuntime``
(``packages/core/tools/src/index.ts``), adapted to Python and the existing
:class:`ToolRegistry` / :class:`PermissionRuleset`:

1. **pre-execute** — :class:`PermissionRuleset` decision + a list of monotonic
   *guards*. Guards are deny-only (they return a reason string or ``None``);
   no guard can re-permit a denied call (dsh ``ToolGuard``).
2. **execute** — an *around* hook seam (placeholder for future timeout / retry /
   audit); defaults to a direct ``tool.execute``.
3. **post-execute** — optional :class:`PostDecision` (accept / block / replace
   content) from post-guards, then the tool's optional ``finalize_content``
   callback (dsh ``finalizeContent``): a sync, content-only rewrite that runs
   once after all normalization (e.g. truncation) is done.

For multiple tool calls the caller (``SessionRunner._execute_tools``) invokes
:func:`run` once per call **in model-return order** and commits each result;
the skeleton supports a future concurrent scheduler but the current path is
serial.

This module imports nothing from ``agent.tools`` at module top level (lazy
import inside :func:`run`) so ``tools.py`` may grow a back-reference without
forming an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from sdpost_claw.harness.events import TOOL_CALL


@dataclass
class ToolExecution:
    """A single tool call scheduled for execution."""

    call_id: str
    name: str
    arguments: dict[str, Any]
    permission: str | None = None  # resolved action; None → fall back to tool.permission


@dataclass
class PreDecision:
    """pre-execute decision. allow | deny(reason) | ask(reason)."""

    effect: str  # "allow" | "deny" | "ask"
    reason: str | None = None


@dataclass
class PostDecision:
    """post-execute decision. accept(content?) | block(feedback)."""

    effect: str  # "accept" | "block"
    content: str | None = None
    feedback: str | None = None
    additional_contexts: list[str] = field(default_factory=list)


# A monotonic pre-guard: returns a deny reason, or None to abstain.
# Mirrors dsh ToolGuard — deny-only, identity-protected, no allow result.
ToolGuard = Callable[[ToolExecution], str | None]

# A post-guard: may rewrite content (accept) or block the result.
# Returns None to abstain.
PostGuard = Callable[[ToolExecution, Any], "PostDecision | None"]

# An around hook wrapping the actual tool.execute (future: timeout/retry/audit).
AroundHook = Callable[[ToolExecution, Any, Any], Awaitable[Any]]


async def run(
    execution: ToolExecution,
    tool: Any,
    context: Any,
    session: Any,
    ruleset: Any,
    guards: list[ToolGuard] | None = None,
    post_guards: list[PostGuard] | None = None,
    around: AroundHook | None = None,
) -> Any:
    """Execute a tool call through the three-stage pipeline.

    Returns a :class:`ToolResult` for every outcome (denied / unknown /
    executed), so the caller can commit uniformly. Emits one ignorable,
    non-surface ``tool_call`` observation event carrying the structured
    pre-decision (audit record); the ``tool_result`` surface event stays owned
    by the boundary settlement path, so there is no double-emit.
    """
    from sdpost_claw.agent.tools import ToolResult  # lazy: avoid top-level cycle

    # --- 1. pre-execute ---------------------------------------------------
    action = (
        execution.permission
        or getattr(tool, "permission", None)
        or f"tool.{execution.name}"
    )
    decision = ruleset.evaluate(action)
    pre_effect = decision.effect  # "allow" | "deny" | "ask"

    deny_reason: str | None = None
    if pre_effect == "deny":
        deny_reason = f"permission denied: {action}"
    elif pre_effect == "ask":
        # Automated mode: ask is auto-denied (matches legacy behavior).
        deny_reason = f"permission requires confirmation (auto-denied): {action}"

    # Monotonic guards (deny-only; first deny wins, none can re-permit).
    guard_reason: str | None = None
    for guard in guards or []:
        reason = guard(execution)
        if reason:
            guard_reason = f"permission guard: {reason}"
            break

    blocked = guard_reason or deny_reason

    # Record the structured pre-decision as a non-surface observation.
    session.log.append(
        TOOL_CALL,
        {
            "call_id": execution.call_id,
            "name": execution.name,
            "arguments": execution.arguments,
            "permission": action,
            "pre_decision": {"effect": pre_effect, "blocked": blocked},
        },
        ignorable=True,
    )

    if blocked:
        return ToolResult(
            tool_call_id=execution.call_id,
            name=execution.name,
            content=blocked,
            is_error=True,
        )

    if tool is None:
        return ToolResult(
            tool_call_id=execution.call_id,
            name=execution.name,
            content=f"Unknown tool: {execution.name}",
            is_error=True,
        )

    # --- 2. execute (around seam) ----------------------------------------
    if around is not None:
        result = await around(execution, tool, context)
    else:
        result = await tool.execute(execution.arguments, context)

    # --- 3. post-execute --------------------------------------------------
    for pg in post_guards or []:
        verdict = pg(execution, result)
        if verdict is None:
            continue
        if verdict.effect == "block":
            return ToolResult(
                tool_call_id=execution.call_id,
                name=execution.name,
                content=verdict.feedback or "blocked by post-guard",
                is_error=True,
            )
        if verdict.effect == "accept" and verdict.content is not None:
            result.content = verdict.content

    # finalize_content: sync, content-only rewrite, runs once after
    # normalization (truncation already happened inside tool.execute).
    finalize = getattr(tool, "finalize_content", None)
    if finalize is not None:
        result.content = finalize(result.content)

    return result
