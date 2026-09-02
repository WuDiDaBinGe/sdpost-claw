"""Main entry point for sdpost-claw."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click

from sdpost_claw.config import Config, get_config, DEFAULT_SDPOST_HOME
from sdpost_claw.agent.tools import ToolRegistry, BuiltInTools
from sdpost_claw.agent.permissions import AgentPermissions, PermissionRuleset
from sdpost_claw.agent.modes import Agent, AgentMode, AgentRegistry
from sdpost_claw.agent.drain import Session, SessionRunner
from sdpost_claw.harness.driver import (
    Abort,
    COMPLETED,
    ERROR,
    MAX_STEPS,
    SessionDriver,
)
from sdpost_claw.context.compaction import CompactionConfig, CompactionEngine
from sdpost_claw.context.registry import SystemContextRegistry
from sdpost_claw.context.source import (
    DateContextSource,
    ProjectInstructionsContextSource,
    AgentSkillsContextSource,
    AgentContextSource,
    SummaryContextSource,
)
from sdpost_claw.harness.compaction_bridge import CompactionBridge
from sdpost_claw.runtime.session import SessionStore, SessionManager
from sdpost_claw.runtime.routing import ModelRouter
from sdpost_claw.runtime.providers import create_provider, ModelProvider
from sdpost_claw.runtime.ui import TerminalUI
from sdpost_claw.runtime.transcript import JSONLTranscript, EventType
from sdpost_claw.extensions.skills import SkillRegistry, SkillSource
from sdpost_claw.extensions.experts import ExpertRegistry
from sdpost_claw.production.events import EventStore
from sdpost_claw.production.audit import AuditLog
from sdpost_claw.production.database import Database


def _ruleset_for_mode(mode: str):
    """Build a PermissionRuleset matching the given agent mode."""
    builder = getattr(AgentPermissions, mode.lower(), None)
    if not callable(builder):
        builder = AgentPermissions.build
    return builder()


def _bundled_skill_dirs() -> list[Path]:
    """Candidate dirs for bundled skills (source checkout and wheel install)."""
    return [
        Path(__file__).parent / "skills",          # wheel: sdpost_claw/skills
        Path(__file__).parent.parent / "skills",   # src checkout: src/skills
    ]


class Application:
    """Main application - wires all modules together."""

    def __init__(self, config: Config | None = None):
        self.config = config or get_config()
        self.config.ensure_directories()

        # UI
        self.ui = TerminalUI()

        # Context system
        self.system_context = SystemContextRegistry()

        # Agent system
        self.tool_registry = ToolRegistry()
        self.permission_ruleset = _ruleset_for_mode(self.config.permissions.default_mode)
        self.agent_registry = AgentRegistry()

        # Runtime
        storage_path = self.config.sdpost_home
        self.session_store = SessionStore(storage_path)
        self.session_manager = SessionManager(self.session_store)
        self.model_router = ModelRouter()
        self.transcript = JSONLTranscript(storage_path)

        # Extensions
        self.skill_registry = SkillRegistry()
        self.expert_registry = ExpertRegistry()

        # Production
        self.event_store = EventStore(storage_path)
        self.audit_log = AuditLog(self.event_store)
        self.database = Database(self.config.db_path)

        # Session runner (initialized after model provider set up)
        self._session_runner: SessionRunner | None = None
        # Phase 2: soft-stopping lifecycle hooks consulted by SessionDriver
        # before each turn / step. Replaces the hard max-20 as primary control.
        self._pre_turn_hooks: list = []
        self._pre_step_hooks: list = []
        # Phase 4: held context snapshot so ``reconcile`` can detect
        # mid-conversation context changes and route them through the session
        # inbox (non-waking inject) instead of patching the baseline directly.
        self._context_snapshot = None
        # Phase 5: compaction. The engine holds thresholds + prompt templates
        # (stateless policy); the bridge is the stateful coordinator that runs
        # the per-step pressure test, calls the model via the *existing single
        # provider* (央企国产-only), and stashes the summary on session.summary
        # for ``SummaryContextSource`` to surface next turn.
        self._compaction_engine = None
        self._compaction_bridge = None
        self._summary_source = None

    def setup(self) -> None:
        """Setup all components."""
        # Register skill sources (bundled + user-configured)
        for d in _bundled_skill_dirs():
            if d.exists():
                self.skill_registry.add_source(SkillSource.directory(d))
        for d in self.config.skill_dirs:
            p = Path(d)
            if p.exists():
                self.skill_registry.add_source(SkillSource.directory(p))

        # Register context sources
        self.system_context.register(DateContextSource())
        self.system_context.register(ProjectInstructionsContextSource(Path.cwd()))
        try:
            discovered = asyncio.run(self.skill_registry.list_all())
        except Exception:
            discovered = []
        from sdpost_claw.context.source import SkillInfo as ContextSkillInfo
        self.system_context.register(AgentSkillsContextSource([
            ContextSkillInfo(
                name=s.name,
                description=s.description,
                slash=s.slash,
            )
            for s in discovered
        ]))
        self.system_context.register(AgentContextSource(
            agent_name="sdpost",
            agent_mode=self.config.permissions.default_mode,
        ))
        # Phase 5: summary source — Unavailable (contributes nothing to the
        # baseline) until the first compaction writes session.summary, after
        # which reconcile surfaces it as "## Previous Session Summary".
        self._summary_source = SummaryContextSource()
        self.system_context.register(self._summary_source)

        # Register built-in tools
        BuiltInTools.register_all(self.tool_registry, str(Path.cwd()))

        # Setup model provider
        model_provider = self._create_model_provider()
        if model_provider:
            self.model_router.register("default", model_provider)

        # Create session runner
        self._session_runner = SessionRunner(
            tool_registry=self.tool_registry,
            permission_ruleset=self.permission_ruleset,
            model_provider=model_provider,
        )

        # Phase 5: compaction engine + bridge. The engine is the stateless
        # policy (thresholds + prompt templates from CompactionConfig); the
        # bridge is the stateful coordinator that runs the per-step pressure
        # test and reuses the single model provider (no new adapter —
        # 央企国产-only constraint holds). Disabled gracefully if no provider.
        self._compaction_engine = CompactionEngine(CompactionConfig())
        self._compaction_bridge = CompactionBridge(
            self._compaction_engine, model_provider
        )

        # Phase 4/5: inject the coordinators into SessionRunner so the
        # per-step reconcile + compaction run inside ``run()`` itself — the
        # single funnel every client passes through (run/exec/serve/desktop).
        # ``_context_snapshot`` stays None; ``run()`` lazily initializes it on
        # the first step, so sidecar/desktop (which skip
        # ``Application.initialize_context``) still get reconcile.
        self._session_runner.system_context = self.system_context
        self._session_runner.compaction_bridge = self._compaction_bridge
        self._session_runner.summary_source = self._summary_source

        # Connect database
        self.database.connect()

    def register_pre_turn_hook(self, hook) -> None:
        """Register a hook run before a turn opens (soft-stopping checkpoint)."""
        self._pre_turn_hooks.append(hook)

    def register_pre_step_hook(self, hook) -> None:
        """Register a hook run before each step (soft-stopping checkpoint)."""
        self._pre_step_hooks.append(hook)

    def _create_model_provider(self) -> ModelProvider | None:
        """Create model provider from config.

        If the legacy config.model block lacks base_url / api_key,
        resolve them from the matching model entry in config.all_models.
        """
        provider_name = self.config.model.provider
        model_name = self.config.model.model
        api_key = self.config.model.api_key
        base_url = self.config.model.base_url

        if not base_url or not api_key:
            entry = next(
                (m for m in self.config.all_models
                 if m.model == model_name or m.id == model_name),
                None,
            )
            if entry:
                if not base_url:
                    base_url = entry.base_url or None
                if not api_key:
                    api_key = entry.api_key

        try:
            return create_provider(
                provider_name=provider_name,
                api_key=api_key,
                model=model_name,
                base_url=base_url,
            )
        except Exception as e:
            self.ui.print_warning(f"Model provider not configured: {e}")
            return None

    async def initialize_context(self) -> str:
        """Initialize system context.

        Phase 4: also stashes the generation snapshot so the agent loop can
        ``reconcile`` against it each turn and route ``Updated`` results
        through the session inbox (non-waking inject).
        """
        try:
            generation = await self.system_context.initialize()
            self._context_snapshot = generation.snapshot
            return generation.baseline
        except Exception as e:
            # Return a basic system context if initialization fails
            return f"""## System Context
You are sdpost-claw, an AI office assistant.
Current working directory: {Path.cwd()}
Date: {__import__('datetime').datetime.now().strftime('%Y-%m-%d')}

Help the user accomplish their tasks using the available tools."""

    async def run_interactive(self) -> None:
        """Run interactive terminal session."""
        self.ui.print_welcome()

        # Initialize context
        system_context = await self.initialize_context()

        # Create session
        session = await self.session_manager.create_session(
            cwd=str(Path.cwd()),
            title="Interactive Session",
            agent_mode=self.config.permissions.default_mode,
        )

        self.ui.print_session_header(session)
        self.ui.print_info("Type 'help' for commands, 'quit' to exit")

        # Record session creation
        await self.transcript.record_simple(
            EventType.SESSION_CREATED,
            session.id,
            {"title": session.title, "mode": session.agent_mode},
        )

        while True:
            try:
                user_input = self.ui.input_prompt(session.title)
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input.strip():
                continue

            command = user_input.strip().lower()

            if command in ("quit", "exit", "q"):
                break
            elif command == "help":
                self.ui.print_help()
                continue
            elif command == "new":
                session = await self.session_manager.create_session(
                    cwd=str(Path.cwd()),
                    title="New Session",
                )
                self.ui.print_success(f"Created new session: {session.id[:8]}")
                continue
            elif command == "clear":
                self.ui.console.clear()
                continue
            elif command.startswith("mode "):
                mode = command.split()[1] if len(command.split()) > 1 else "build"
                if mode not in ("build", "plan", "general"):
                    self.ui.print_warning(f"Unknown mode: {mode} (build/plan/general)")
                    continue
                session.agent_mode = mode
                # Rebuild permission ruleset so the mode actually takes effect
                self.permission_ruleset = _ruleset_for_mode(mode)
                if self._session_runner:
                    self._session_runner.permission_ruleset = self.permission_ruleset
                self.ui.print_info(f"Switched to {mode} mode")
                continue
            elif command.startswith("resume "):
                session_id = command.split()[1]
                resumed = await self.session_manager.get_session(session_id)
                if not resumed:
                    # Allow partial ID match
                    all_sessions = await self.session_manager.lifecycle.list_all()
                    match = next(
                        (s for s in all_sessions if s["id"].startswith(session_id)),
                        None,
                    )
                    if match:
                        resumed = await self.session_manager.get_session(match["id"])
                if not resumed:
                    self.ui.print_warning(f"Session not found: {session_id}")
                    continue
                session = resumed
                self.permission_ruleset = _ruleset_for_mode(session.agent_mode)
                if self._session_runner:
                    self._session_runner.permission_ruleset = self.permission_ruleset
                self.ui.print_session_header(session)
                self.ui.print_info(f"Resumed session with {len(session.history)} messages")
                continue
            elif command == "list":
                sessions = await self.session_manager.lifecycle.list_all()
                if sessions:
                    self.ui.print_table(
                        ["ID", "Title", "Mode", "Status"],
                        [[s["id"][:8], s.get("title", ""), s.get("agent_mode", ""), s.get("status", "")] for s in sessions],
                        title="Sessions",
                    )
                else:
                    self.ui.print_info("No sessions")
                continue

            # Process user input
            await self._process_input(session, user_input, system_context)

        # Cleanup
        await self.transcript.record_simple(
            EventType.SESSION_CLOSED,
            session.id,
        )
        self.ui.print_info("Goodbye!")

    async def _process_input(
        self,
        session: Session,
        user_input: str,
        system_context: str,
    ) -> None:
        """Process user input through the agent loop."""
        # Submit prompt
        await self.session_manager.submit_prompt(session.id, user_input)

        # Record prompt
        await self.transcript.record_simple(
            EventType.PROMPT_SUBMITTED,
            session.id,
            {"text": user_input},
        )

        if not self._session_runner:
            self.ui.print_error("No model provider configured. Set your API key in config.")
            return

        # Agent loop - driven by SessionDriver turn/step lifecycle.
        # max_iterations is now a SAFETY BACKSTOP only; the primary control
        # is the soft-stopping pre_step hook (SessionDriver.step_start).
        max_iterations = 20
        iteration = 0
        driver = SessionDriver(session)
        for hook in self._pre_turn_hooks:
            driver.on_pre_turn(hook)
        for hook in self._pre_step_hooks:
            driver.on_pre_step(hook)
        turn_reason = COMPLETED

        try:
            abort = await driver.turn_start(reason="user")
            if isinstance(abort, Abort):
                # A pre_turn hook vetoed before any step ran.
                turn_reason = abort.reason
            else:
                # Phase 4 reconcile + Phase 5 compaction now run inside
                # ``SessionRunner.run()`` (the per-step funnel every client
                # shares), so this loop only owns the driver lifecycle
                # (turn/step soft-stopping). ``run()`` lazily initializes the
                # context snapshot on its first step, so sidecar/desktop (which
                # skip ``Application.initialize_context``) get reconcile too.
                while iteration < max_iterations:
                    iteration += 1

                    # Soft-stopping checkpoint: a pre_step Abort stops the
                    # turn without emitting an orphan step_start.
                    decision = await driver.step_start()
                    if isinstance(decision, Abort):
                        turn_reason = decision.reason
                        break

                    # Run drain (the per-step executor; signature unchanged).
                    # Phase 4 reconcile + Phase 5 compaction now run inside
                    # ``run()`` itself, so every client gets them.
                    result = await self._session_runner.run(
                        session=session,
                        system_context=system_context,
                        force=True,
                    )
                    await driver.step_end()

                    if result.status == "no_work":
                        break
                    elif result.status == "error":
                        self.ui.print_error(result.error or "Unknown error")
                        turn_reason = ERROR
                        break
                    elif result.status == "text_response":
                        if result.content:
                            self.ui.print(f"\n[bold cyan]sdpost:[/bold cyan] {result.content}\n")
                            await self.session_manager.add_assistant_message(session.id, result.content)
                        break
                    elif result.status == "tool_execution":
                        # Persist the assistant tool_calls message so resumed
                        # sessions keep a valid message sequence
                        await self.session_manager.add_assistant_tool_calls(
                            session.id,
                            result.tool_calls,
                        )

                        # Display tool calls and results
                        for tc, tr in zip(result.tool_calls, result.tool_results):
                            self.ui.print_tool_call(tc.name, tc.input)
                            self.ui.print_tool_result(tr.name, tr.content, tr.is_error)

                            # Persist tool result message
                            await self.session_manager.add_tool_message(
                                session.id,
                                tr.tool_call_id,
                                tr.name,
                                tr.content,
                            )

                            # Record in transcript
                            await self.transcript.record_simple(
                                EventType.TOOL_CALLED,
                                session.id,
                                {"tool": tc.name, "input": tc.input},
                            )
                            await self.transcript.record_simple(
                                EventType.TOOL_RESULT,
                                session.id,
                                {"tool": tr.name, "is_error": tr.is_error},
                            )

                        # Continue loop for next iteration
                        continue
                else:
                    # Safety backstop hit (no break): the soft-stop hook did
                    # not stop the turn within max_iterations.
                    turn_reason = MAX_STEPS
                    self.ui.print_warning("Reached maximum iterations")
        finally:
            # Close the turn (always exactly once) then flush the event log.
            await driver.turn_end(reason=turn_reason)
            await self.session_manager.persist_log(session)


@click.group()
def cli():
    """sdpost-claw: Open-source AI office agent desktop workstation."""
    pass


@cli.command()
@click.option("--config", type=click.Path(), help="Config file path")
def run(config):
    """Run sdpost-claw in interactive mode."""
    cfg = Config.load(Path(config) if config else None)
    app = Application(cfg)
    app.setup()
    asyncio.run(app.run_interactive())


@cli.command()
@click.argument("prompt", nargs=-1, required=True)
@click.option("--config", type=click.Path(), help="Config file path")
@click.option("--mode", default="build", help="Agent mode (build/plan/general)")
@click.option("--session", help="Session ID to resume")
def exec(prompt, config, mode, session):
    """Execute a single command."""
    cfg = Config.load(Path(config) if config else None)
    app = Application(cfg)
    app.setup()

    prompt_text = " ".join(prompt)
    system_context = asyncio.run(app.initialize_context())

    # Create or resume session
    if session:
        sess = asyncio.run(app.session_manager.get_session(session))
        if not sess:
            click.echo(f"Session not found: {session}", err=True)
            sys.exit(1)
    else:
        sess = asyncio.run(app.session_manager.create_session(
            cwd=str(Path.cwd()),
            title="Quick Exec",
            agent_mode=mode,
        ))

    asyncio.run(app._process_input(sess, prompt_text, system_context))


@cli.command()
def init():
    """Initialize sdpost-claw configuration."""
    config = get_config()
    config.ensure_directories()
    config.save()
    click.echo(f"Configuration saved to {config.sdpost_home / 'config.yaml'}")
    click.echo("Edit this file to set your API key and model preferences.")


@cli.command()
def status():
    """Show sdpost-claw status."""
    config = get_config()
    click.echo(f"sdpost-claw v0.1.0")
    click.echo(f"Config: {config.sdpost_home / 'config.yaml'}")
    click.echo(f"Database: {config.db_path}")
    click.echo(f"Model: {config.model.provider}/{config.model.model}")
    click.echo(f"Mode: {config.permissions.default_mode}")


@cli.command()
@click.option("--config", type=click.Path(), help="Config file path")
def serve(config):
    """Start sidecar server."""
    from aiohttp import web
    from sdpost_claw.runtime.server import SidecarServer

    cfg = Config.load(Path(config) if config else None)
    app = Application(cfg)
    app.setup()

    server = SidecarServer(
        session_manager=app.session_manager,
        host="127.0.0.1",
        port=8765,
        session_runner=app._session_runner,
        system_context=app.system_context,
    )

    async def run_server():
        await server.start()
        # Keep running
        while True:
            await asyncio.sleep(3600)

    asyncio.run(run_server())


@cli.command()
@click.option("--config", type=click.Path(), help="Config file path")
def desktop(config):
    """Launch desktop GUI client."""
    from sdpost_claw.desktop.server import DesktopServer

    cfg = Config.load(Path(config) if config else None)

    server = DesktopServer(host="127.0.0.1", port=8765)
    server.setup()

    # Start server in background thread
    import threading

    def run_server():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.start())
        loop.run_forever()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # Launch desktop window
    try:
        from sdpost_claw.desktop.app import DesktopApp

        app = DesktopApp(
            host="127.0.0.1",
            port=8765,
            title="sdpost-claw",
            width=1200,
            height=800,
        )
        app.start()
    except ImportError:
        click.echo("Desktop GUI requires pywebview. Install with: pip install pywebview")
        click.echo("Server is running at http://127.0.0.1:8765")
        click.echo("Open this URL in your browser as fallback.")
        # Keep running
        import time
        while True:
            time.sleep(60)


if __name__ == "__main__":
    cli()
