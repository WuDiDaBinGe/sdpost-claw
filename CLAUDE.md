# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

sdpost-claw is a Python 3.11+ open-source AI agent desktop workbench. It provides a terminal UI, sidecar HTTP server, and optional pywebview desktop client. The core loop: user prompt → Session Drain (model call + tool execution) → results back to user, with permissions, context management, and multi-agent collaboration layered in.

## Commands

| Task | Command |
|------|---------|
| Install (editable) | `pip install -e .` |
| Install dev deps | `pip install -e ".[dev]"` |
| Install desktop extras | `pip install -e ".[desktop]"` |
| Run tests | `pytest` |
| Run a single test | `pytest tests/test_<name>.py` or `pytest tests/test_<name>.py::test_fn` |
| Lint | `ruff check src/` |
| Type-check | `mypy src/` |
| Initialize config | `sdpost init` |
| Interactive mode | `sdpost run` |
| One-shot exec | `sdpost exec "prompt text"` |
| Sidecar server | `sdpost serve` |
| Desktop GUI | `sdpost desktop` |
| Status | `sdpost status` |

The CLI entry point is `sdpost_claw.main:cli` (registered via `[project.scripts]`). Config lives at `~/.sdpost/config.yaml` (YAML, loaded by `Config.load()`).

## Architecture (src/sdpost_claw/)

### Entry & Wiring: `main.py`
`Application` wires all subsystems. `setup()` registers skill sources, context sources, built-in tools, creates the model provider, builds the `SessionRunner`, and connects the SQLite database. The interactive loop in `run_interactive()` reads prompts, processes them via `SessionRunner.run()`, and loops until the model returns text (max 20 iterations per turn).

### Agent Core (`agent/`)
- **`drain.py`** — Core execution model. `Session` holds conversation history + pending input/tool results. `SafeProviderTurnBoundary.prepare()` promotes pending user input and settles completed tool results before each model call. `SessionRunner.run()` orchestrates: boundary prep → model call → tool execution (with permission check per tool) → result. The agent loop in `main.py` calls `run()` repeatedly until `no_work` or `text_response`.
- **`tools.py`** — Type-safe `ToolDefinition` with JSON Schema input, async `execute_fn`, output truncation. `ToolRegistry` is a name→definition map. `BuiltInTools.register_all()` registers: read, write, edit, glob, grep, bash, webfetch, question. Each tool declares a permission key (e.g. `file.read`).
- **`permissions.py`** — Wildcard ruleset with Last-Match-Wins. `PermissionRule` matches via regex-converted wildcard. `AgentPermissions` provides presets: `build()` (allow all), `plan()` (read + network, deny write/edit/shell/spawn), `general()` (allow all, deny `shell.rm`).
- **`modes.py`** — `AgentMode` enum (BUILD/PLAN/GENERAL), `Agent` dataclass wrapping a ruleset + skills, `AgentRegistry` for managing instances and creating sub-agents.

### Runtime (`runtime/`)
- **`providers.py`** — `ModelProvider` ABC with a single `OpenAIProvider` implementation. All domestic models (DeepSeek, 通义千问, 智谱GLM, 月之暗面, 豆包, 百川, MiniMax, 阶跃星辰) use the OpenAI-compatible protocol and share this one provider. `create_provider()` factory resolves `base_url` from the provider name via `_PROVIDER_BASE_URLS` and returns an `OpenAIProvider`. API keys can come from config or the `OPENAI_API_KEY` env var.
- **`routing.py`** — `ModelRouter` maps LITE/DEFAULT/CRAFT tiers to providers. `select_tier_for_task()` picks tier by complexity.
- **`session.py`** — `SessionStore` (JSON-backed), `SessionManager` (create/resume/submit_prompt/add_assistant_message).
- **`ui.py`** — `TerminalUI` using `rich` for welcome, prompt, tool call/result display, help table.
- **`transcript.py`** — `JSONLTranscript` writes structured events to disk.
- **`server.py`** — `SidecarServer` (aiohttp) exposes session management over HTTP.

### Context System (`context/`)
- **`registry.py`** — `SystemContextRegistry` manages ordered context sources. `initialize()` produces a baseline string + snapshot. `reconcile()` detects changes and returns `Unchanged` / `Updated` / `ReplacementReady`. Unavailable sources are skipped during init (not raised).
- **`source.py`** — Context source classes: `DateContextSource`, `ProjectInstructionsContextSource`, `AgentSkillsContextSource`, `AgentContextSource`.
- **`compaction.py`**, **`epoch.py`**, **`midconv.py`**, **`reconcile.py`**, **`snapshot.py`** — Context lifecycle support (compaction, epoch tracking, mid-conversation updates).

### Extensions (`extensions/`)
- **`skills.py`** — `SkillRegistry` discovers skills from embedded, directory (recursive `SKILL.md` scan with YAML frontmatter), and URL sources. Cached by source key.
- **`mcp.py`** — MCP server connectors (configured via `config.mcp_servers`).
- **`experts.py`** — Expert agent registry.

### Memory (`memory/`)
- **`workspace.py`** — Workspace-level memory.
- **`user.py`** — User preference/identity memory.
- **`externalize.py`** — Output externalization for large results.

### Production (`production/`)
- **`database.py`** — SQLite database (path from config).
- **`events.py`** — `EventStore` for event sourcing.
- **`audit.py`** — `AuditLog` for governance.
- **`multiagent.py`** — `Supervisor` decomposes tasks via a PLAN agent, executes subtasks in parallel with BUILD agents, aggregates results. `MessageBus` provides priority-based async message passing between agents.
- **`scheduler.py`** — Automation scheduler (cron-based via `croniter`).

### Desktop (`desktop/`)
- **`server.py`** — `DesktopServer` (HTTP API for the GUI).
- **`app.py`** — `DesktopApp` (pywebview window, optional install).

### Common (`common/`)
- **`events.py`** — `EventBus` for in-process pub/sub.
- **`storage.py`** — File-based storage helpers.
- **`utils.py`** — `generate_id()`, `truncate_text()`.

### Bundled Skills (`src/skills/`)
Four built-in skills: `code-review`, `data-analysis`, `ppt-creation`, `writing`. Loaded by `Application.setup()` from both the wheel package path and the `src/skills/` checkout path.

## Key Data Flow

```
User prompt → Session.submit_prompt()
  → SessionRunner.run():
      1. SafeProviderTurnBoundary.prepare() (promote input, settle tool results)
      2. ModelProvider.generate() (via ModelRouter tier)
      3. If tool_calls: PermissionRuleset.evaluate() → execute each tool → results back to session
      4. If text: append to history, return to UI
  → Agent loop repeats until text_response or max_iterations
```

## Important Notes

- The `tests/` directory is currently empty. There are no tests yet.
- This is **not** a git repository at the root level. Two git sub-repositories exist as reference projects: `learn-workbuddy/` (a separate educational workbench) and `opencode/` (a TypeScript/Node open-source project). Do not modify files inside them.
- The `design-docs/` directory contains the v2 system design. The `docs/` directory is present but appears empty.
- Configuration is YAML-based at `~/.sdpost/config.yaml`. The `config.models` list overrides defaults; disabled entries are excluded from `all_models`.
- Model provider API keys can be set in config **or** via the `OPENAI_API_KEY` env var. When config keys are missing, the provider passes `None` to the SDK, which falls back to env.