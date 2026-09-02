"""Integrated Desktop Server - runs sidecar server with full application."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Any

from aiohttp import web
from aiohttp import ClientSession, ClientTimeout

from sdpost_claw.agent.drain import Session, SessionRunner
from sdpost_claw.agent.tools import ToolRegistry, BuiltInTools
from sdpost_claw.config import DEFAULT_MODELS, get_config, ModelEntry
from sdpost_claw.context.compaction import CompactionConfig, CompactionEngine
from sdpost_claw.context.registry import SystemContextRegistry
from sdpost_claw.context.source import (
    DateContextSource,
    ProjectInstructionsContextSource,
    AgentContextSource,
    SummaryContextSource,
)
from sdpost_claw.extensions.experts import ExpertRegistry
from sdpost_claw.extensions.skills import SkillRegistry, SkillSource
from sdpost_claw.harness.compaction_bridge import CompactionBridge
from sdpost_claw.runtime.providers import create_provider
from sdpost_claw.runtime.session import SessionStore, SessionManager

WEB_DIR = Path(__file__).parent / "web"


def _bundled_skill_dirs() -> list[Path]:
    """Candidate dirs for bundled skills (wheel install and source checkout)."""
    return [
        Path(__file__).parent.parent / "skills",          # wheel: sdpost_claw/skills
        Path(__file__).parent.parent.parent / "skills",   # src checkout: src/skills
    ]


def _ruleset_for_mode(mode: str):
    """Build a PermissionRuleset matching the given agent mode."""
    from sdpost_claw.agent.permissions import AgentPermissions
    builder = getattr(AgentPermissions, (mode or "build").lower(), None)
    if not callable(builder):
        builder = AgentPermissions.build
    return builder()


class DesktopServer:
    """
    Integrated Desktop Server - full application server for desktop client.

    Combines:
    - Session management
    - Agent execution
    - Tool registry
    - Context system
    - Model provider
    - Static web UI hosting
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self.config = get_config()
        self.config.ensure_directories()

        # Initialize components
        self.system_context = SystemContextRegistry()
        self.tool_registry = ToolRegistry()
        self.session_store = SessionStore(self.config.sdpost_home)
        self.session_manager = SessionManager(self.session_store)
        self.model_provider = None
        self.session_runner = None

        # Extension registries
        self.skill_registry = SkillRegistry()
        self.expert_registry = ExpertRegistry()

        # Web app
        self._app = web.Application()
        self._setup_routes()

        # SSE: per-session event buffers (replay for late connect) + live queues
        self._sse_buffers: dict[str, list[dict[str, Any]]] = {}
        self._sse_queues: dict[str, list[asyncio.Queue]] = {}

    def setup(self) -> None:
        """Setup all components."""
        # Register context sources
        self.system_context.register(DateContextSource())
        self.system_context.register(ProjectInstructionsContextSource(Path.cwd()))
        self.system_context.register(AgentContextSource(
            agent_name="sdpost",
            agent_mode=self.config.permissions.default_mode,
        ))
        # Phase 5: summary source — Unavailable (contributes nothing to the
        # baseline) until the first compaction writes session.summary, after
        # which reconcile surfaces it as "## Previous Session Summary".
        self._summary_source = SummaryContextSource()
        self.system_context.register(self._summary_source)

        # Register tools
        BuiltInTools.register_all(self.tool_registry, str(Path.cwd()))

        # Register skill sources (bundled + configured)
        for bundled in _bundled_skill_dirs():
            if bundled.exists():
                self.skill_registry.add_source(SkillSource.directory(bundled))
        for d in self.config.skill_dirs:
            p = Path(d)
            if p.exists():
                self.skill_registry.add_source(SkillSource.directory(p))

        # Setup model provider. If the legacy config.model block lacks
        # base_url / api_key (e.g. written by an older UI that only saved
        # provider+model), resolve them from the matching model entry.
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
            self.model_provider = create_provider(
                provider_name=provider_name,
                api_key=api_key,
                model=model_name,
                base_url=base_url,
            )
            # Persist the resolved credentials back so they survive restart
            self.config.model.api_key = api_key
            self.config.model.base_url = base_url
        except Exception as e:
            print(f"Warning: Model provider not configured: {e}")

        # Create session runner
        self.session_runner = SessionRunner(
            tool_registry=self.tool_registry,
            permission_ruleset=_ruleset_for_mode(self.config.permissions.default_mode),
            model_provider=self.model_provider,
        )

        # Phase 4/5: compaction engine + bridge. The engine is the stateless
        # policy (thresholds + prompt templates); the bridge is the stateful
        # coordinator that runs the per-step pressure test and reuses the
        # single model provider (央企国产-only — no new adapter). Disabled
        # gracefully if the provider didn't configure (model_provider is None).
        self._compaction_engine = CompactionEngine(CompactionConfig())
        self._compaction_bridge = CompactionBridge(
            self._compaction_engine, self.model_provider
        )

        # Inject the coordinators into SessionRunner so the per-step reconcile
        # + compaction run inside ``run()`` itself — the single funnel every
        # desktop request passes through. ``_context_snapshot`` stays None;
        # ``run()`` lazily initializes it on the first step.
        self.session_runner.system_context = self.system_context
        self.session_runner.compaction_bridge = self._compaction_bridge
        self.session_runner.summary_source = self._summary_source

    def _setup_routes(self) -> None:
        """Setup HTTP routes."""
        # Web UI
        self._app.router.add_get("/", self.handle_index_page)
        self._app.router.add_get("/static/{path:.*}", self.handle_static)

        # API
        self._app.router.add_get("/api/health", self.handle_health)
        self._app.router.add_get("/api/sessions", self.handle_list_sessions)
        self._app.router.add_post("/api/sessions", self.handle_create_session)
        self._app.router.add_get("/api/sessions/{id}", self.handle_get_session)
        self._app.router.add_delete("/api/sessions/{id}", self.handle_delete_session)
        self._app.router.add_post("/api/sessions/{id}/prompt", self.handle_submit_prompt)
        self._app.router.add_get("/api/sessions/{id}/stream", self.handle_stream)

        # Local filesystem browsing (workspace folder picker)
        self._app.router.add_get("/api/fs/browse", self.handle_fs_browse)

        # Sidebar / extension data
        self._app.router.add_get("/api/skills", self.handle_list_skills)
        self._app.router.add_get("/api/experts", self.handle_list_experts)
        self._app.router.add_get("/api/connectors", self.handle_list_connectors)
        self._app.router.add_get("/api/spaces", self.handle_list_spaces)
        self._app.router.add_get("/api/automations", self.handle_list_automations)
        self._app.router.add_get("/api/library", self.handle_list_library)

        self._app.router.add_get("/api/config", self.handle_get_config)
        self._app.router.add_post("/api/config", self.handle_update_config)

        # Model management (model-level, each entry = one model)
        self._app.router.add_get("/api/models", self.handle_list_models)
        self._app.router.add_get("/api/models/{model_id}", self.handle_get_model)
        self._app.router.add_post("/api/models", self.handle_add_model)
        self._app.router.add_put("/api/models/{model_id}", self.handle_update_model)
        self._app.router.add_post("/api/models/delete", self.handle_batch_delete_models)
        self._app.router.add_post("/api/models/test", self.handle_test_model)

    # ------------------------------------------------------------------
    # SSE helpers
    # ------------------------------------------------------------------
    def _publish(self, session_id: str, event: dict[str, Any]) -> None:
        """Publish an event to a session's SSE subscribers (and buffer it)."""
        self._sse_buffers.setdefault(session_id, []).append(event)
        for q in self._sse_queues.get(session_id, []):
            q.put_nowait(event)

    # ------------------------------------------------------------------
    # Web UI handlers
    # ------------------------------------------------------------------
    async def handle_index_page(self, request: web.Request) -> web.Response:
        """Serve index.html."""
        return web.FileResponse(WEB_DIR / "index.html")

    async def handle_static(self, request: web.Request) -> web.Response:
        """Serve static assets (css/js)."""
        rel = request.match_info["path"]
        path = (WEB_DIR / rel).resolve()
        if not str(path).startswith(str(WEB_DIR.resolve())) or not path.exists():
            return web.Response(status=404)
        ctype, _ = mimetypes.guess_type(str(path))
        return web.FileResponse(path, headers={"Content-Type": ctype or "application/octet-stream"})

    # ------------------------------------------------------------------
    # API handlers
    # ------------------------------------------------------------------
    async def handle_health(self, request: web.Request) -> web.Response:
        """Health check."""
        return web.json_response({
            "status": "ok",
            "model_configured": self.model_provider is not None,
            "version": "0.1.0",
        })

    async def handle_list_sessions(self, request: web.Request) -> web.Response:
        """List all sessions."""
        sessions = await self.session_manager.lifecycle.list_all()
        return web.json_response({"sessions": sessions})

    async def handle_create_session(self, request: web.Request) -> web.Response:
        """Create new session."""
        data = await request.json()
        session = await self.session_manager.create_session(
            cwd=data.get("cwd", str(Path.cwd())),
            title=data.get("title", "新任务"),
            agent_mode=data.get("agent_mode", "build"),
        )
        return web.json_response(session.to_dict())

    async def handle_get_session(self, request: web.Request) -> web.Response:
        """Get session by ID."""
        session_id = request.match_info["id"]
        session = await self.session_manager.get_session(session_id)
        if not session:
            return web.json_response({"error": "Session not found"}, status=404)
        return web.json_response({
            **session.to_dict(),
            "history": session.history,
        })

    async def handle_delete_session(self, request: web.Request) -> web.Response:
        """Delete session."""
        session_id = request.match_info["id"]
        await self.session_manager.lifecycle.delete(session_id)
        self.session_manager.remove_active(session_id)
        self._sse_buffers.pop(session_id, None)
        self._sse_queues.pop(session_id, None)
        return web.json_response({"status": "deleted"})

    async def handle_submit_prompt(self, request: web.Request) -> web.Response:
        """Submit prompt and start processing."""
        session_id = request.match_info["id"]
        data = await request.json()
        text = data.get("text", "")

        session = await self.session_manager.get_session(session_id)
        if not session:
            return web.json_response({"error": "Session not found"}, status=404)

        # Reset buffers for a fresh turn
        self._sse_buffers[session_id] = []
        self._sse_queues.setdefault(session_id, [])

        # Submit prompt
        await self.session_manager.submit_prompt(session_id, text)
        self._publish(session_id, {"type": "user", "role": "user", "content": text})

        # Start processing in background
        asyncio.create_task(self._process_prompt(session, text))

        return web.json_response({"status": "processing"})

    async def _process_prompt(self, session: Session, text: str) -> None:
        """Process prompt through agent loop and stream events."""
        # Per-turn statistics (deepseek-harness style): tools / iterations / duration
        turn_started = time.monotonic()
        turn_tools: list[dict[str, Any]] = []
        turn_iterations = 0
        turn_prompt_chars = len(text)
        turn_reasoning_chars = 0

        def _on_delta(kind: str, chunk: str) -> None:
            """Forward streamed model output to the SSE subscribers."""
            nonlocal turn_reasoning_chars
            if kind == "reasoning":
                turn_reasoning_chars += len(chunk)
            self._publish(session.id, {"type": "delta", "kind": kind, "content": chunk})

        def _publish_turn_stats() -> None:
            self._publish(session.id, {
                "type": "turn_stats",
                "duration_ms": int((time.monotonic() - turn_started) * 1000),
                "iterations": turn_iterations,
                "prompt_chars": turn_prompt_chars,
                "reasoning_chars": turn_reasoning_chars,
                "tool_calls": turn_tools,
                "tool_count": len(turn_tools),
                "tool_errors": sum(1 for t in turn_tools if t.get("is_error")),
                "model": (
                    f"{self.config.model.provider}/{self.config.model.model}"
                    if self.model_provider else None
                ),
            })

        if not self.model_provider:
            msg = "错误: 未配置模型提供商。请在设置中配置 API Key。"
            await self.session_manager.add_assistant_message(session.id, msg)
            self._publish(session.id, {"type": "message", "role": "assistant", "content": msg})
            _publish_turn_stats()
            self._publish(session.id, {"type": "done"})
            return

        # Initialize context
        try:
            generation = await self.system_context.initialize()
            system_context = generation.baseline
        except Exception:
            system_context = "You are sdpost-claw, an AI office assistant."

        # Agent loop
        max_iterations = 20
        iteration = 0
        first_reply = ""

        while iteration < max_iterations:
            iteration += 1
            turn_iterations = iteration

            try:
                result = await self.session_runner.run(
                    session=session,
                    system_context=system_context,
                    force=True,
                    on_delta=_on_delta,
                )
            except Exception as e:
                err = f"错误: {e}"
                await self.session_manager.add_assistant_message(session.id, err)
                self._publish(session.id, {"type": "message", "role": "assistant", "content": err})
                break
            if result.status == "no_work":
                break
            elif result.status == "error":
                await self.session_manager.add_assistant_message(
                    session.id,
                    f"错误: {result.error}"
                )
                self._publish(session.id, {"type": "message", "role": "assistant", "content": f"错误: {result.error}"})
                break
            elif result.status == "text_response":
                if result.content:
                    if not first_reply:
                        first_reply = result.content
                    await self.session_manager.add_assistant_message(session.id, result.content)
                    self._publish(session.id, {"type": "message", "role": "assistant", "content": result.content})
                break
            elif result.status == "tool_execution":
                # Persist the assistant tool_calls message so resumed
                # sessions keep a valid message sequence
                await self.session_manager.add_assistant_tool_calls(
                    session.id,
                    result.tool_calls,
                )
                for tc, tr in zip(result.tool_calls, result.tool_results):
                    await self.session_manager.add_tool_message(
                        session.id,
                        tr.tool_call_id,
                        tr.name,
                        tr.content,
                    )
                    is_error = bool(getattr(tr, "is_error", False))
                    turn_tools.append({
                        "name": tr.name,
                        "is_error": is_error,
                        "result_chars": len(tr.content or ""),
                    })
                    self._publish(session.id, {
                        "type": "tool",
                        "role": "tool",
                        "name": tr.name,
                        "content": tr.content,
                        "is_error": is_error,
                    })
                continue

        _publish_turn_stats()
        self._publish(session.id, {"type": "done"})

        # Auto-title: after the first turn completes, replace the default
        # "新任务" placeholder with a concise generated title.
        await self._maybe_generate_title(session, text, first_reply)

    async def _maybe_generate_title(
        self, session: Session, user_text: str, reply: str
    ) -> None:
        """Generate a concise session title after the first Q&A turn.

        Uses the model (small extra call) summarizing the first user prompt
        + first assistant reply; falls back to truncating the user prompt.
        Persists via SessionManager.rename_session and notifies the client
        through an SSE ``title`` event.
        """
        if session.title not in (None, "", "新任务", "New Session"):
            return

        title = ""
        if self.model_provider:
            try:
                resp = await self.model_provider.generate(
                    system=(
                        "你是任务标题生成器。根据对话内容生成一个简短的任务标题："
                        "不超过14个字，不带标点、引号、书名号或任何解释，"
                        "直接输出标题文本本身。"
                    ),
                    messages=[{
                        "role": "user",
                        "content": (
                            f"用户：{user_text[:300]}\n"
                            f"助手：{reply[:300]}"
                        ),
                    }],
                )
                title = (resp.text or "").strip()
                title = title.splitlines()[0] if title else ""
                title = title.strip('"\'“”《》「」').strip()[:20]
            except Exception:
                title = ""

        if not title:
            title = user_text[:20].strip() or "新任务"

        await self.session_manager.rename_session(session.id, title)
        self._publish(session.id, {"type": "title", "title": title})

    async def handle_stream(self, request: web.Request) -> web.Response:
        """SSE stream for real-time updates."""
        session_id = request.match_info["id"]

        response = web.StreamResponse()
        response.headers["Content-Type"] = "text/event-stream"
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Connection"] = "keep-alive"
        response.headers["X-Accel-Buffering"] = "no"
        await response.prepare(request)

        # Register a live queue
        queue: asyncio.Queue = asyncio.Queue()
        self._sse_queues.setdefault(session_id, []).append(queue)

        try:
            # Replay buffered events first (handles late connect)
            for ev in self._sse_buffers.get(session_id, []):
                await response.write(
                    f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8")
                )

            await response.write(
                f"data: {json.dumps({'type': 'connected', 'session_id': session_id}, ensure_ascii=False)}\n\n".encode("utf-8")
            )

            while True:
                ev = await queue.get()
                await response.write(
                    f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8")
                )
        except (ConnectionResetError, ConnectionError):
            pass
        finally:
            queues = self._sse_queues.get(session_id, [])
            if queue in queues:
                queues.remove(queue)

        return response

    # ------------------------------------------------------------------
    # Sidebar / extension data
    # ------------------------------------------------------------------
    async def handle_list_skills(self, request: web.Request) -> web.Response:
        """List available skills."""
        try:
            skills = await self.skill_registry.list_all()
        except Exception:
            skills = []
        return web.json_response({"skills": [
            {"name": s.name, "description": s.description, "location": str(s.location), "slash": s.slash}
            for s in skills
        ]})

    async def handle_list_experts(self, request: web.Request) -> web.Response:
        """List available experts."""
        experts = self.expert_registry.list_all()
        out = []
        for e in experts:
            mode = e.mode.value if hasattr(e.mode, "value") else str(e.mode)
            out.append({"id": e.id, "name": e.name, "description": e.description, "mode": mode})
        return web.json_response({"experts": out})

    async def handle_list_connectors(self, request: web.Request) -> web.Response:
        """List MCP connectors."""
        return web.json_response({"connectors": self.config.mcp_servers or []})

    async def handle_fs_browse(self, request: web.Request) -> web.Response:
        """Browse local directories for the workspace folder picker.

        No ``path`` (or empty) → roots (Windows drive letters / POSIX "/").
        With ``path`` → that directory's subdirectories + parent for navigation.
        """
        raw = (request.query.get("path") or "").strip()
        if not raw:
            if os.name == "nt":
                import string
                roots = [
                    {"name": f"{letter}:\\", "path": f"{letter}:\\"}
                    for letter in string.ascii_uppercase
                    if Path(f"{letter}:\\").exists()
                ]
            else:
                roots = [{"name": "/", "path": "/"}]
            return web.json_response({"path": "", "parent": None, "dirs": roots})

        try:
            p = Path(raw).resolve()
        except (OSError, ValueError):
            return web.json_response({"error": "invalid path"}, status=400)
        if not p.is_dir():
            return web.json_response({"error": f"not a directory: {raw}"}, status=400)

        dirs: list[dict[str, str]] = []
        try:
            for d in sorted(p.iterdir(), key=lambda x: x.name.lower()):
                if d.is_dir() and not d.name.startswith("."):
                    dirs.append({"name": d.name, "path": str(d)})
        except OSError as e:
            return web.json_response({"error": f"cannot list: {e}"}, status=400)

        parent = str(p.parent) if p.parent != p else ""
        return web.json_response({"path": str(p), "parent": parent, "dirs": dirs})

    async def handle_list_spaces(self, request: web.Request) -> web.Response:
        """List workspaces/spaces derived from session cwds."""
        sessions = await self.session_manager.lifecycle.list_all()
        spaces: dict[str, dict[str, Any]] = {}
        for s in sessions:
            cwd = s.get("cwd", ".")
            if cwd not in spaces:
                spaces[cwd] = {"cwd": cwd, "name": cwd, "tasks": []}
            spaces[cwd]["tasks"].append({
                "id": s.get("id"),
                "title": s.get("title", "未命名"),
                "updated_at": s.get("updated_at"),
            })
        return web.json_response({"spaces": list(spaces.values())})

    async def handle_list_automations(self, request: web.Request) -> web.Response:
        """List automations (placeholder)."""
        return web.json_response({"automations": []})

    async def handle_list_library(self, request: web.Request) -> web.Response:
        """List library items (placeholder)."""
        return web.json_response({"items": []})

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------
    async def handle_get_config(self, request: web.Request) -> web.Response:
        """Get current configuration (full settings, not just model)."""
        return web.json_response({
            # Model (legacy fields kept for the chat topbar / dropdown)
            "provider": self.config.model.provider,
            "model": self.config.model.model,
            "mode": self.config.permissions.default_mode,
            "api_key_set": bool(self.config.model.api_key),
            "version": "0.1.0",
            # General
            "theme": self.config.theme,
            "language": self.config.language,
            "default_mode": self.config.permissions.default_mode,
            # Compaction
            "compaction": {
                "enabled": self.config.compaction.enabled,
                "max_tokens": self.config.compaction.max_tokens,
                "buffer_tokens": self.config.compaction.buffer_tokens,
                "keep_tokens": self.config.compaction.keep_tokens,
            },
            # Extensions
            "skill_dirs": list(self.config.skill_dirs),
            "mcp_servers": list(self.config.mcp_servers),
            # Advanced
            "log_level": self.config.log_level,
            "audit_enabled": self.config.audit_enabled,
            "sdpost_home": str(self.config.sdpost_home),
        })

    async def handle_update_config(self, request: web.Request) -> web.Response:
        """Update configuration.

        Accepts either:
        - ``model_id``: select a configured model entry; its provider /
          model / api_key / base_url are applied in full.
        - explicit ``provider`` / ``model`` / ``api_key`` / ``base_url`` fields.
        - general settings: ``theme`` / ``language`` / ``default_mode`` /
          ``compaction`` / ``skill_dirs`` / ``log_level`` / ``audit_enabled``.
        """
        data = await request.json()

        if "model_id" in data:
            entry = next(
                (m for m in self.config.all_models if m.id == data["model_id"]),
                None,
            )
            if not entry:
                return web.json_response(
                    {"error": f"未找到模型: {data['model_id']}"}, status=404
                )
            self.config.model.provider = entry.provider
            self.config.model.model = entry.model
            self.config.model.api_key = entry.api_key
            self.config.model.base_url = entry.base_url or None
        else:
            if "provider" in data:
                self.config.model.provider = data["provider"]
            if "model" in data:
                self.config.model.model = data["model"]
            if "api_key" in data:
                self.config.model.api_key = data["api_key"]
            if "base_url" in data:
                self.config.model.base_url = data["base_url"]

        # General settings
        if "theme" in data:
            self.config.theme = data["theme"]
        if "language" in data:
            self.config.language = data["language"]
        if "default_mode" in data:
            self.config.permissions.default_mode = data["default_mode"]
        if "mode" in data:
            self.config.permissions.default_mode = data["mode"]
        if "compaction" in data and isinstance(data["compaction"], dict):
            c = data["compaction"]
            if "enabled" in c:
                self.config.compaction.enabled = bool(c["enabled"])
            for k in ("max_tokens", "buffer_tokens", "keep_tokens"):
                if k in c and isinstance(c[k], int):
                    setattr(self.config.compaction, k, max(1, c[k]))
        if "skill_dirs" in data and isinstance(data["skill_dirs"], list):
            self.config.skill_dirs = [str(d).strip() for d in data["skill_dirs"] if str(d).strip()]
        if "log_level" in data:
            self.config.log_level = str(data["log_level"]).upper()
        if "audit_enabled" in data:
            self.config.audit_enabled = bool(data["audit_enabled"])

        self.config.save()

        # Re-setup model provider
        error: str | None = None
        try:
            self.model_provider = create_provider(
                provider_name=self.config.model.provider,
                api_key=self.config.model.api_key,
                model=self.config.model.model,
                base_url=self.config.model.base_url,
            )
            # CRITICAL: SessionRunner holds a provider reference captured
            # at startup — it must be refreshed or chat keeps using the
            # stale (possibly None) provider.
            if self.session_runner is not None:
                self.session_runner.model_provider = self.model_provider
        except Exception as e:
            error = str(e)

        return web.json_response({
            "status": "updated",
            "provider": self.config.model.provider,
            "model": self.config.model.model,
            "api_key_set": bool(self.config.model.api_key),
            "provider_error": error,
        })

    # ------------------------------------------------------------------
    # Model Management (model-level, each entry = one model)
    # ------------------------------------------------------------------
    async def handle_list_models(self, request: web.Request) -> web.Response:
        """List all models (defaults + user-configured)."""
        models = self.config.all_models
        result = []
        for m in models:
            result.append({
                "id": m.id,
                "name": m.name,
                "provider": m.provider,
                "model": m.model,
                "api_key_set": bool(m.api_key),
                "base_url": m.base_url,
                "enabled": m.enabled,
            })
        return web.json_response({"models": result})

    async def handle_get_model(self, request: web.Request) -> web.Response:
        """Get a specific model entry."""
        model_id = request.match_info["model_id"]
        models = self.config.all_models
        target = next((m for m in models if m.id == model_id), None)
        if not target:
            return web.json_response({"error": "Model not found"}, status=404)
        return web.json_response({
            "id": target.id,
            "name": target.name,
            "provider": target.provider,
            "model": target.model,
            "api_key_set": bool(target.api_key),
            "base_url": target.base_url,
            "enabled": target.enabled,
        })

    async def handle_add_model(self, request: web.Request) -> web.Response:
        """Add a new model entry."""
        data = await request.json()
        mid = data.get("id", "").strip() or data.get("model", "").strip()
        if not mid:
            return web.json_response({"error": "Model ID is required"}, status=400)

        entry = ModelEntry(
            id=mid,
            name=data.get("name", mid),
            provider=data.get("provider", ""),
            model=data.get("model", mid),
            api_key=data.get("api_key", ""),
            base_url=data.get("base_url", ""),
            enabled=data.get("enabled", True),
        )

        # Replace or append
        existing = [m for m in self.config.models if m.id == mid]
        if existing:
            i = self.config.models.index(existing[0])
            self.config.models[i] = entry
        else:
            self.config.models.append(entry)

        self.config.save()
        return web.json_response({"status": "saved", "id": mid})

    async def handle_update_model(self, request: web.Request) -> web.Response:
        """Update an existing model entry.

        If the model is a default (not yet in self.config.models),
        promote it to a user-configured entry so changes persist.
        """
        model_id = request.match_info["model_id"]
        data = await request.json()

        existing = [m for m in self.config.models if m.id == model_id]
        if existing:
            entry = existing[0]
            i = self.config.models.index(entry)
            if "name" in data:
                entry.name = data["name"]
            if "provider" in data:
                entry.provider = data["provider"]
            if "model" in data:
                entry.model = data["model"]
            if "api_key" in data:
                entry.api_key = data["api_key"]
            if "base_url" in data:
                entry.base_url = data["base_url"]
            if "enabled" in data:
                entry.enabled = data["enabled"]
            self.config.models[i] = entry
        else:
            # Promote default model to user-configured so it persists
            entry = ModelEntry(
                id=model_id,
                name=data.get("name", model_id),
                provider=data.get("provider", ""),
                model=data.get("model", model_id),
                api_key=data.get("api_key", ""),
                base_url=data.get("base_url", ""),
                enabled=data.get("enabled", True),
            )
            self.config.models.append(entry)

        self.config.save()
        return web.json_response({"status": "updated", "id": model_id})

    async def handle_batch_delete_models(self, request: web.Request) -> web.Response:
        """Batch delete model entries.

        - User-configured models: removed entirely.
        - Default models: they are hardcoded, so deleting writes a single
          disabled override entry that hides them from the list (see
          Config.all_models). Any previous entry for the id (edited or
          already-disabled) is replaced first, otherwise removing the
          override would make the default reappear.
        """
        data = await request.json()
        ids = data.get("ids", [])
        if not ids:
            return web.json_response({"error": "No IDs provided"}, status=400)

        default_ids = {d["id"] for d in DEFAULT_MODELS}
        deleted: list[str] = []

        for mid in ids:
            # Drop every existing entry for this id (user entry or override)
            self.config.models = [m for m in self.config.models if m.id != mid]

            if mid in default_ids:
                dm = next(d for d in DEFAULT_MODELS if d["id"] == mid)
                self.config.models.append(ModelEntry(
                    id=dm["id"],
                    name=dm["name"],
                    provider=dm["provider"],
                    model=dm["model"],
                    base_url=dm["base_url"],
                    enabled=False,
                ))
            deleted.append(mid)

        self.config.save()
        return web.json_response({"status": "deleted", "ids": deleted})

    async def handle_test_model(self, request: web.Request) -> web.Response:
        """Test a model connection by making a simple API call."""
        data = await request.json()
        model_id = data.get("id", "")
        api_key = data.get("api_key", "")
        base_url = data.get("base_url", "")
        model = data.get("model", "")

        if not model_id:
            return web.json_response({"error": "Model ID required"}, status=400)

        # Fall back to saved api_key when the form field was left blank
        if not api_key:
            saved = next((m for m in self.config.all_models if m.id == model_id), None)
            if saved:
                api_key = saved.api_key

        # Local endpoints (e.g. Ollama) don't require an api key
        is_local = "localhost" in base_url or "127.0.0.1" in base_url
        if not api_key and not is_local:
            return web.json_response({
                "status": "error",
                "message": "未设置 API Key",
            })

        try:
            start = time.time()
            url = base_url.rstrip("/")
            headers = {"Authorization": f"Bearer {api_key}"}
            # 所有国产模型均基于 OpenAI 兼容协议，统一走 /chat/completions
            url += "/chat/completions"
            payload = {
                "model": model or "deepseek-chat",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 5,
            }

            async with ClientSession(timeout=ClientTimeout(total=10)) as client:
                resp = await client.post(url, json=payload, headers=headers)
                elapsed = round(time.time() - start, 1)
                body = await resp.text()

            if 200 <= resp.status < 300:
                return web.json_response({
                    "status": "ok",
                    "elapsed": elapsed,
                    "status_code": resp.status,
                })
            elif resp.status in (401, 403):
                return web.json_response({
                    "status": "error",
                    "elapsed": elapsed,
                    "status_code": resp.status,
                    "message": "认证失败 (API Key 无效或未授权)",
                })
            else:
                return web.json_response({
                    "status": "error",
                    "elapsed": elapsed,
                    "status_code": resp.status,
                    "message": f"服务端返回 {resp.status}: {body[:200]}",
                })
        except Exception as e:
            return web.json_response({
                "status": "error",
                "message": str(e),
            })

    async def start(self) -> None:
        """Start the server."""
        runner = web.AppRunner(self._app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        print(f"Desktop server running at http://{self.host}:{self.port}")
