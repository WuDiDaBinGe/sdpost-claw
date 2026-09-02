"""Sidecar Server - HTTP API and SSE for external integration."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from aiohttp import web


class SidecarServer:
    """
    Sidecar Server - HTTP API for sdpost-claw.

    Provides:
    - REST API for session management
    - SSE for real-time event streaming
    - JSON-RPC 2.0 for ACP protocol
    """

    def __init__(
        self,
        session_manager: Any,
        host: str = "127.0.0.1",
        port: int = 8765,
        session_runner: Any = None,
        system_context: Any = None,
    ):
        self.session_manager = session_manager
        self.host = host
        self.port = port
        self.session_runner = session_runner
        self.system_context = system_context
        self._app = web.Application()
        self._setup_routes()
        self._sse_queues: dict[str, list] = {}

    def _setup_routes(self) -> None:
        """Setup HTTP routes."""
        self._app.router.add_get("/", self.handle_index)
        self._app.router.add_get("/api/health", self.handle_health)
        self._app.router.add_get("/api/sessions", self.handle_list_sessions)
        self._app.router.add_post("/api/sessions", self.handle_create_session)
        self._app.router.add_get("/api/sessions/{id}", self.handle_get_session)
        self._app.router.add_delete("/api/sessions/{id}", self.handle_delete_session)
        self._app.router.add_post("/api/sessions/{id}/prompt", self.handle_submit_prompt)
        self._app.router.add_get("/api/sessions/{id}/events", self.handle_sse)
        self._app.router.add_post("/api/rpc", self.handle_jsonrpc)

    async def handle_index(self, request: web.Request) -> web.Response:
        """Index endpoint."""
        return web.json_response({
            "name": "sdpost-claw",
            "version": "0.1.0",
            "status": "running",
        })

    async def handle_health(self, request: web.Request) -> web.Response:
        """Health check."""
        return web.json_response({"status": "ok"})

    async def handle_list_sessions(self, request: web.Request) -> web.Response:
        """List all sessions."""
        sessions = await self.session_manager.lifecycle.list_all()
        return web.json_response({"sessions": sessions})

    async def handle_create_session(self, request: web.Request) -> web.Response:
        """Create a new session."""
        data = await request.json()
        session = await self.session_manager.create_session(
            cwd=data.get("cwd", "."),
            title=data.get("title"),
            agent_mode=data.get("agent_mode", "build"),
        )
        return web.json_response(session.to_dict())

    async def handle_get_session(self, request: web.Request) -> web.Response:
        """Get session by ID."""
        session_id = request.match_info["id"]
        session = await self.session_manager.get_session(session_id)
        if not session:
            return web.json_response({"error": "Session not found"}, status=404)
        return web.json_response(session.to_dict())

    async def handle_delete_session(self, request: web.Request) -> web.Response:
        """Delete a session."""
        session_id = request.match_info["id"]
        await self.session_manager.lifecycle.delete(session_id)
        return web.json_response({"status": "deleted"})

    async def handle_submit_prompt(self, request: web.Request) -> web.Response:
        """Submit a prompt to a session."""
        session_id = request.match_info["id"]
        data = await request.json()
        text = data.get("text", "")

        session = await self.session_manager.get_session(session_id)
        if not session:
            return web.json_response({"error": "Session not found"}, status=404)

        prompt = await self.session_manager.submit_prompt(session_id, text)
        if not prompt:
            return web.json_response({"error": "Session not found"}, status=404)

        # Process the prompt through the agent loop in the background,
        # otherwise submitted prompts were never drained.
        if self.session_runner is not None:
            asyncio.create_task(self._process_prompt(session, text))

        return web.json_response({
            "status": "submitted",
            "prompt_id": prompt.id,
        })

    async def _process_prompt(self, session: Any, text: str) -> None:
        """Process a submitted prompt through the agent loop."""
        if not self.session_runner:
            return

        # System context
        if self.system_context is not None:
            try:
                generation = await self.system_context.initialize()
                system_context = generation.baseline
            except Exception:
                system_context = "You are sdpost-claw, an AI office assistant."
        else:
            system_context = "You are sdpost-claw, an AI office assistant."

        if not system_context.strip():
            system_context = "You are sdpost-claw, an AI office assistant."

        max_iterations = 20
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            try:
                result = await self.session_runner.run(
                    session=session,
                    system_context=system_context,
                    force=True,
                )
            except Exception as e:
                await self.session_manager.add_assistant_message(session.id, f"错误: {e}")
                break

            if result.status == "no_work":
                break
            elif result.status == "error":
                await self.session_manager.add_assistant_message(
                    session.id, f"错误: {result.error}"
                )
                break
            elif result.status == "text_response":
                if result.content:
                    await self.session_manager.add_assistant_message(session.id, result.content)
                break
            elif result.status == "tool_execution":
                await self.session_manager.add_assistant_tool_calls(
                    session.id, result.tool_calls
                )
                for tr in result.tool_results:
                    await self.session_manager.add_tool_message(
                        session.id, tr.tool_call_id, tr.name, tr.content
                    )
                continue

    async def handle_sse(self, request: web.Request) -> web.Response:
        """Server-Sent Events endpoint."""
        session_id = request.match_info["id"]

        response = web.StreamResponse()
        response.headers["Content-Type"] = "text/event-stream"
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Connection"] = "keep-alive"
        await response.prepare(request)

        # Send initial event
        await response.write(f"data: {json.dumps({'type': 'connected', 'session_id': session_id})}\n\n".encode())

        # Keep connection alive
        try:
            while True:
                await response.write(f": keepalive\n\n".encode())
                await asyncio.sleep(30)
        except (ConnectionResetError, ConnectionError):
            pass

        return response

    async def handle_jsonrpc(self, request: web.Request) -> web.Response:
        """JSON-RPC 2.0 endpoint (ACP protocol)."""
        data = await request.json()

        # Basic JSON-RPC handling
        rpc_id = data.get("id")
        method = data.get("method")
        params = data.get("params", {})

        result = await self._dispatch_rpc(method, params)

        return web.json_response({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": result,
        })

    async def _dispatch_rpc(self, method: str, params: dict) -> Any:
        """Dispatch JSON-RPC method."""
        if method == "sessions.list":
            return await self.session_manager.lifecycle.list_all()
        elif method == "sessions.create":
            session = await self.session_manager.create_session(
                cwd=params.get("cwd", "."),
                title=params.get("title"),
                agent_mode=params.get("agent_mode", "build"),
            )
            return session.to_dict()
        elif method == "sessions.prompt":
            prompt = await self.session_manager.submit_prompt(
                params.get("session_id"),
                params.get("text", ""),
            )
            return {"prompt_id": prompt.id if prompt else None}
        else:
            return {"error": f"Unknown method: {method}"}

    async def start(self) -> None:
        """Start the server."""
        runner = web.AppRunner(self._app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        print(f"Sidecar server running at http://{self.host}:{self.port}")

    async def stop(self) -> None:
        """Stop the server."""
        # Cleanup
        pass
