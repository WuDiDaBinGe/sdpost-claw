"""Offline smoke test for the Phase 0+1 harness wiring.

No network, no model. Verifies:
  - the harness package imports cleanly (events + session_log),
  - drain.py / session.py / main.py still import after the double-write edits,
  - SessionLog.append -> derive_messages() round-trips to OpenAI format,
  - snapshot() isolates the log from caller mutation,
  - persist() is incremental (watermark advances, no rewrite, no dup),
  - from_events() reconstructs a log that derives the same messages.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path


def main() -> int:
    # 1. Import the new harness package surface.
    from sdpost_claw.harness import (
        SessionLog,
        SessionEvent,
        USER_MESSAGE,
        ASSISTANT_MESSAGE,
        TOOL_RESULT,
    )

    # 2. Import the re-wired modules (verifies no import/syntax breakage).
    from sdpost_claw.agent.drain import Session, SessionRunner  # noqa: F401
    from sdpost_claw.runtime.session import SessionManager, SessionStore  # noqa: F401
    from sdpost_claw import main as main_mod  # noqa: F401

    # 3. Session has a log member now.
    s = Session(cwd=".", title="t", agent_mode="build")
    assert isinstance(s.log, SessionLog), "Session.log must be a SessionLog"
    assert len(s.log) == 0

    # 4. Round-trip: append surface events, derive OpenAI messages.
    log = SessionLog()
    log.append(USER_MESSAGE, {"content": "hello"})
    tool_calls = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "read", "arguments": json.dumps({"path": "a"})},
        }
    ]
    log.append(ASSISTANT_MESSAGE, {"content": "", "tool_calls": tool_calls})
    log.append(
        TOOL_RESULT,
        {"call_id": "call_1", "name": "read", "content": "file body"},
    )
    log.append(ASSISTANT_MESSAGE, {"content": "done"})

    msgs = log.derive_messages()
    assert msgs[0] == {"role": "user", "content": "hello"}, msgs[0]
    assert msgs[1]["role"] == "assistant" and msgs[1]["tool_calls"] == tool_calls
    assert msgs[2] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "name": "read",
        "content": "file body",
    }, msgs[2]
    assert msgs[3] == {"role": "assistant", "content": "done"}, msgs[3]
    assert len(msgs) == 4

    # 5. Snapshot isolation: mutating the caller's dict after append must not
    #    change the frozen event.
    payload = {"content": "orig"}
    log.append(USER_MESSAGE, payload)
    payload["content"] = "tampered"
    last = log.events()[-1]
    assert last.data["content"] == "orig", last.data

    # 6. Non-surface events do not leak into derived messages.
    from sdpost_claw.harness import TURN_START, TURN_END, STEP_START
    log2 = SessionLog()
    log2.append(TURN_START, {"reason": "user"})
    log2.append(USER_MESSAGE, {"content": "q"})
    log2.append(STEP_START, {})
    log2.append(ASSISTANT_MESSAGE, {"content": "a"})
    log2.append(TURN_END, {"reason": "completed"})
    assert log2.derive_messages() == [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
    ], log2.derive_messages()

    # 7. persist() is incremental + idempotent (append, watermark advances).
    async def persist_round_trip() -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "s.events.jsonl"
            log3 = SessionLog()
            log3.append(USER_MESSAGE, {"content": "first"})
            await log3.persist(path)              # writes seq 1
            await log3.persist(path)             # idempotent: no-op
            log3.append(ASSISTANT_MESSAGE, {"content": "second"})
            await log3.persist(path)             # writes seq 2 only
            # Read back.
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 2, lines        # no dup, no loss
            evs = [SessionEvent.from_dict(json.loads(ln)) for ln in lines]
            assert [e.seq for e in evs] == [1, 2]
            # Reconstruct and re-derive.
            rebuilt = SessionLog.from_events(evs)
            assert rebuilt.derive_messages() == [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "second"},
            ], rebuilt.derive_messages()
            # A resumed session (fresh log) appending to the existing file
            # must NOT overwrite prior events.
            resumed = SessionLog()
            resumed.append(USER_MESSAGE, {"content": "third"})
            await resumed.persist(path)
            lines2 = path.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines2) == 3, lines2      # prior 2 preserved

    asyncio.run(persist_round_trip())

    # 8. SessionDriver: turn/step lifecycle events are ignorable, excluded
    #    from derived messages, and a pre_step Abort vetoes with no orphan.
    from sdpost_claw.harness.driver import (
        Abort,
        COMPLETED,
        Continue,
        MAX_STEPS,
        SessionDriver,
    )

    async def driver_lifecycle() -> None:
        s = Session(cwd=".", title="d", agent_mode="build")
        drv = SessionDriver(s)

        # turn opens; no hooks → proceeds (returns None).
        assert await drv.turn_start(reason="user") is None
        # step with no hooks → Continue, STEP_START emitted.
        dec = await drv.step_start()
        assert isinstance(dec, Continue)
        await drv.step_end()
        # surface a message so the turn isn't empty.
        s._emit(USER_MESSAGE, {"content": "hi"})
        await drv.turn_end(reason=COMPLETED)

        types = [e.type for e in s.log.events()]
        assert types == [
            "turn_start", "step_start", "step_end", "user_message", "turn_end"
        ], types
        life = [
            e for e in s.log.events()
            if e.type in ("turn_start", "step_start", "step_end", "turn_end")
        ]
        assert all(e.ignorable for e in life), life
        # lifecycle events do NOT leak into derived messages
        assert s.log.derive_messages() == [{"role": "user", "content": "hi"}]

        # pre_step Abort: veto the step, emit NO step_start (no orphan).
        s2 = Session(cwd=".", title="d2", agent_mode="build")
        drv2 = SessionDriver(s2)
        await drv2.turn_start(reason="user")

        async def veto(_drv):
            return Abort(reason=MAX_STEPS)

        drv2.on_pre_step(veto)
        dec2 = await drv2.step_start()
        assert isinstance(dec2, Abort) and dec2.reason == MAX_STEPS, dec2
        types2 = [e.type for e in s2.log.events()]
        assert "step_start" not in types2, types2  # no orphan
        await drv2.turn_end(reason=dec2.reason)
        assert s2.log.derive_messages() == []  # nothing surfaced

    asyncio.run(driver_lifecycle())

    # 9. tool_pipeline: monotonic deny-only guards, finalize_content, and a
    #    structured pre-decision recorded as an ignorable, non-surface
    #    TOOL_CALL event (does not leak into derive_messages).
    from sdpost_claw.harness.tool_pipeline import ToolExecution, run as run_pipeline
    from sdpost_claw.agent.permissions import AgentPermissions
    from sdpost_claw.agent.tools import ToolDefinition, ToolContext

    async def tool_pipeline_runs() -> None:
        s = Session(cwd=".", title="p", agent_mode="build")

        # (a) a deny-only guard blocks bash before execute; tool not run.
        async def bash_fn(inp, ctx):
            return "SHOULD NOT RUN"
        bash_tool = ToolDefinition(
            name="bash", description="",
            input_schema={"type": "object", "properties": {}},
            execute_fn=bash_fn, permission="shell.execute",
        )
        ex = ToolExecution(
            call_id="c1", name="bash",
            arguments={"command": "echo"}, permission="shell.execute",
        )

        def guard_bash(execution):
            return "bash denied" if execution.name == "bash" else None

        res = await run_pipeline(
            ex, bash_tool,
            ToolContext(session_id=s.id, tool_call_id="c1", cwd="."),
            s, AgentPermissions.build(), guards=[guard_bash],
        )
        assert res.is_error and "permission guard: bash denied" in res.content, res.content

        # (b) finalize_content rewrites content post-execute.
        async def echo_fn(inp, ctx):
            return "raw"
        echo_tool = ToolDefinition(
            name="echo", description="",
            input_schema={"type": "object", "properties": {}},
            execute_fn=echo_fn, permission="tool.echo",
            finalize_content=lambda c: c.upper(),
        )
        ex2 = ToolExecution(call_id="c2", name="echo", arguments={}, permission="tool.echo")
        res2 = await run_pipeline(
            ex2, echo_tool,
            ToolContext(session_id=s.id, tool_call_id="c2", cwd="."),
            s, AgentPermissions.build(),
        )
        assert res2.content == "RAW", res2.content
        assert not res2.is_error

        # (c) plan mode denies file.write; the tool never runs.
        write_ran = False

        async def write_fn(inp, ctx):
            nonlocal write_ran
            write_ran = True
            return "ok"
        write_tool = ToolDefinition(
            name="write", description="",
            input_schema={"type": "object", "properties": {}},
            execute_fn=write_fn, permission="file.write",
        )
        ex3 = ToolExecution(call_id="c3", name="write", arguments={}, permission="file.write")
        res3 = await run_pipeline(
            ex3, write_tool,
            ToolContext(session_id=s.id, tool_call_id="c3", cwd="."),
            s, AgentPermissions.plan(),
        )
        assert res3.is_error and "permission denied" in res3.content, res3.content
        assert not write_ran, "denied tool must not execute"

        # (d) unknown tool (tool=None) → structured error.
        ex4 = ToolExecution(call_id="c4", name="nope", arguments={}, permission=None)
        res4 = await run_pipeline(
            ex4, None,
            ToolContext(session_id=s.id, tool_call_id="c4", cwd="."),
            s, AgentPermissions.build(),
        )
        assert res4.is_error and "Unknown tool" in res4.content, res4.content

        # (e) TOOL_CALL events are ignorable + non-surface; pre_decision recorded.
        types = [e.type for e in s.log.events()]
        assert types.count("tool_call") == 4, types
        tc = [e for e in s.log.events() if e.type == "tool_call"]
        assert all(e.ignorable for e in tc), tc
        assert tc[0].data["pre_decision"]["blocked"] is not None  # (a) bash denied
        assert tc[1].data["pre_decision"]["effect"] == "allow"     # (b) echo
        assert s.log.derive_messages() == [], s.log.derive_messages()

    asyncio.run(tool_pipeline_runs())

    # 10. Inbox: submit_prompt enqueues next_turn; promote claims + drains;
    #     next_step inject → CONTEXT_INJECTION (ignorable, non-surface, no
    #     leak into derive_messages); boundary.prepare folds the drained
    #     injection into the model-visible system_context.
    from sdpost_claw.agent.drain import (
        PromptPromotion,
        SafeProviderTurnBoundary,
    )

    async def inbox_wiring() -> None:
        s = Session(cwd=".", title="i", agent_mode="build")

        # (a) submit_prompt lands in the next-turn queue (not pending_input).
        s.submit_prompt("hello")
        assert s.inbox.pending_turns == 1, s.inbox.pending_turns
        assert not hasattr(s, "pending_input"), "pending_input must be gone"

        # (b) promotion claims + drains the queue; USER_MESSAGE surfaced.
        admitted = await PromptPromotion(s).promote()
        assert len(admitted) == 1 and admitted[0].text == "hello"
        assert s.inbox.pending_turns == 0  # drained
        assert s.log.derive_messages() == [
            {"role": "user", "content": "hello"}
        ], s.log.derive_messages()

        # (c) non-waking inject → CONTEXT_INJECTION event, ignorable +
        #     non-surface (does NOT leak into derived messages).
        s.inbox.inject("context_update", "AGENTS.md changed", "test")
        assert s.inbox.pending_injections == 1
        drained = s.drain_injections()
        assert len(drained) == 1 and drained[0].text == "AGENTS.md changed"
        assert s.inbox.pending_injections == 0  # drained
        ci = [e for e in s.log.events() if e.type == "context_injection"]
        assert len(ci) == 1 and ci[0].ignorable, ci
        assert ci[0].data == {
            "kind": "context_update", "text": "AGENTS.md changed", "source": "test"
        }, ci[0].data
        # injection did not add a derived message
        assert s.log.derive_messages() == [
            {"role": "user", "content": "hello"}
        ], s.log.derive_messages()

        # (d) boundary.prepare folds the drained injection into the
        #     model-visible system_context (no extra user turn).
        s2 = Session(cwd=".", title="i2", agent_mode="build")
        s2.inbox.inject("context_update", "rules updated", "test")
        boundary = SafeProviderTurnBoundary(s2)
        prepared = await boundary.prepare("BASE", tools=[])
        assert prepared.system_context == "BASE\n\n## Context Update\nrules updated", \
            prepared.system_context
        # the injection was drained + logged, not surfaced as a message
        assert s2.inbox.pending_injections == 0
        assert any(e.type == "context_injection" for e in s2.log.events())
        assert s2.log.derive_messages() == [], s2.log.derive_messages()

    asyncio.run(inbox_wiring())

    # 11. Compaction: tiny config + fake provider triggers compaction;
    #     session.summary set, COMPACTION_OCCURRED ignorable + non-surface
    #     (no leak into derive_messages); the re-fire guard blocks a second
    #     immediate compaction (derived history didn't shrink); and
    #     SummaryContextSource surfaces the summary via reconcile (Updated)
    #     + a fresh initialize() baseline ("## Previous Session Summary").
    from sdpost_claw.context.compaction import CompactionConfig, CompactionEngine
    from sdpost_claw.harness.compaction_bridge import CompactionBridge
    from sdpost_claw.harness.events import COMPACTION_OCCURRED
    from sdpost_claw.context.registry import SystemContextRegistry, Updated as UpdatedResult
    from sdpost_claw.context.source import SummaryContextSource
    from sdpost_claw.agent.drain import ModelResponse

    async def compaction_runs() -> None:
        # (a) disabled bridge (no provider) is a no-op, never touches the log.
        eng0 = CompactionEngine(CompactionConfig(max_tokens=1, buffer_tokens=0))
        br0 = CompactionBridge(eng0, provider=None)
        s0 = Session(cwd=".", title="c0", agent_mode="build")
        s0._emit(USER_MESSAGE, {"content": "x" * 100})
        assert await br0.maybe_compact(s0) is False
        assert s0.summary == ""
        assert not any(e.type == COMPACTION_OCCURRED for e in s0.log.events())

        # (b) tiny config + fake provider triggers compaction.
        summary_text = "## Objective\n- finish the harness work"

        class _FakeProvider:
            def __init__(self, text: str):
                self._text = text
                self.calls = 0

            async def generate(self, system, messages, tools=None):
                self.calls += 1
                return ModelResponse(text=self._text)

        fake = _FakeProvider(summary_text)
        eng = CompactionEngine(
            CompactionConfig(max_tokens=10, buffer_tokens=0, keep_tokens=5)
        )
        br = CompactionBridge(eng, provider=fake)
        s = Session(cwd=".", title="c", agent_mode="build")
        # one long user message -> >10 estimated tokens -> should_compact True
        s._emit(USER_MESSAGE, {"content": "x" * 100})
        assert eng.should_compact(eng.count_tokens("x" * 100))  # sanity

        compacted = await br.maybe_compact(s)
        assert compacted is True, compacted
        assert s.summary == summary_text, s.summary
        assert fake.calls == 1

        # COMPACTION_OCCURRED event: ignorable + non-surface.
        evs = [e for e in s.log.events() if e.type == COMPACTION_OCCURRED]
        assert len(evs) == 1, evs
        assert evs[0].ignorable is True, evs[0]
        assert "tokens_before" in evs[0].data and "summary_preview" in evs[0].data
        # does not leak into derived messages
        assert s.log.derive_messages() == [
            {"role": "user", "content": "x" * 100}
        ], s.log.derive_messages()

        # (c) re-fire guard: a second immediate compaction is blocked
        #     (history unchanged, no growth past the buffer); provider
        #     not called again.
        assert await br.maybe_compact(s) is False
        assert fake.calls == 1

        # (d) SummaryContextSource surfaces the summary via reconcile +
        #     a fresh initialize() baseline ("## Previous Session Summary").
        reg = SystemContextRegistry()
        src = SummaryContextSource()
        reg.register(src)
        gen = await reg.initialize()
        # empty summary -> Unavailable -> not in baseline, not in snapshot
        assert "Previous Session Summary" not in gen.baseline
        assert "session/summary" not in gen.snapshot.entries

        src.update_summary(s.summary)
        result = await reg.reconcile(gen.snapshot)
        assert isinstance(result, UpdatedResult), result
        assert "## Previous Session Summary" in result.text, result.text
        # a fresh initialize now includes the summary in the baseline
        gen2 = await reg.initialize()
        assert "## Previous Session Summary" in gen2.baseline, gen2.baseline

    asyncio.run(compaction_runs())

    # 12. SessionRunner.run() carries the Phase 4 reconcile + Phase 5
    #     compaction INLINE (the per-step funnel every client shares), so
    #     clients that bypass ``_process_input`` — sidecar / desktop call
    #     ``run()`` directly from their own loops — still get them. Mirrors
    #     the sidecar/desktop path: no SessionDriver, no
    #     Application.initialize_context, just ``run()``.
    from sdpost_claw.agent.drain import ModelResponse
    from sdpost_claw.agent.tools import ToolRegistry
    from sdpost_claw.agent.permissions import AgentPermissions
    from sdpost_claw.context.compaction import CompactionConfig, CompactionEngine
    from sdpost_claw.context.registry import SystemContextRegistry
    from sdpost_claw.context.source import SummaryContextSource
    from sdpost_claw.harness.compaction_bridge import CompactionBridge

    async def runner_carries_coordinators() -> None:
        class _Provider:
            """Single provider for both turn + compaction (央企国产-only)."""

            def __init__(self):
                self.seen: list[str] = []

            async def generate(self, system, messages, tools=None):
                self.seen.append(system)
                return ModelResponse(text="## Summary\n- did harness work")

        prov = _Provider()
        eng = CompactionEngine(
            CompactionConfig(max_tokens=10, buffer_tokens=0, keep_tokens=5)
        )
        bridge = CompactionBridge(eng, provider=prov)
        summary_src = SummaryContextSource()
        reg = SystemContextRegistry()
        reg.register(summary_src)

        runner = SessionRunner(
            tool_registry=ToolRegistry(),
            permission_ruleset=AgentPermissions.build(),
            model_provider=prov,
        )
        # Coordinators injected post-construction — run() signature unchanged.
        runner.system_context = reg
        runner.compaction_bridge = bridge
        runner.summary_source = summary_src

        # First run(): a long prompt crosses the threshold (>10 tokens) so the
        # compaction pressure check fires inside run() itself. The summary is
        # written to session.summary + pushed into SummaryContextSource. The
        # context snapshot is lazily initialized on this first step (summary
        # source is Unavailable here, so absent from the baseline/snapshot).
        s = Session(cwd=".", title="r", agent_mode="build")
        s.submit_prompt("x" * 100)
        assert runner._context_snapshot is None
        res = await runner.run(s, system_context="BASE", force=True)
        assert res.status == "text_response", res.status
        assert s.summary == "## Summary\n- did harness work", s.summary
        assert summary_src._summary == s.summary  # pushed by run()
        assert runner._context_snapshot is not None  # lazily initialized

        # Second run() on a FRESH session with the SAME runner (the sidecar
        # pattern: one runner, many sessions). reconcile now sees
        # SummaryContextSource as newly-available (it was Unavailable when the
        # snapshot was lazily initialized) → Updated → injected into the
        # inbox → boundary.prepare drains + folds "## Previous Session
        # Summary" into the system context the model actually receives — no
        # extra user turn, no leak into derived messages.
        s2 = Session(cwd=".", title="r2", agent_mode="build")
        s2.submit_prompt("next")
        res2 = await runner.run(s2, system_context="BASE2", force=True)
        assert res2.status == "text_response"
        # the last generate() call is this turn's; the summary was folded in
        # via inject+drain (## Context Update patch), not surfaced as a message
        assert "## Previous Session Summary" in prov.seen[-1], prov.seen[-1]
        msgs = s2.log.derive_messages()
        assert msgs[0] == {"role": "user", "content": "next"}, msgs
        assert msgs[1] == {
            "role": "assistant", "content": "## Summary\n- did harness work"
        }, msgs
        # the injection did NOT become a derived user message
        assert all(m["role"] != "user" or m["content"] == "next" for m in msgs), msgs

    asyncio.run(runner_carries_coordinators())

    print("OK: harness imports, drain/session/main wired, "
          "derive_messages round-trips, snapshot isolates, persist incremental, "
          "driver turn/step lifecycle + soft-stop veto, tool pipeline guards + finalize, "
          "inbox next-turn/next-step + non-waking context injection, "
          "compaction bridge (summary + ignorable event + re-fire guard + reconcile surfacing), "
          "SessionRunner.run() carries reconcile+compaction (all-client funnel).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
