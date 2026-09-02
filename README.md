# sdpost-claw 🐾

> Open-source full-scenario AI office agent desktop workstation

sdpost-claw is an open-source AI agent desktop workbench that replicates the core functionality of Tencent WorkBuddy. Users can give tasks in natural language, and sdpost-claw autonomously thinks, decomposes tasks, plans execution steps, and delivers verifiable work results.

## Features

- **Natural Language Interaction** - Describe your needs in one sentence
- **Autonomous Planning & Execution** - Agent automatically breaks down and completes tasks
- **Multi-modal Processing** - Supports documents, code, data, design and more
- **Long-term Memory** - Maintains context across sessions
- **Security & Governance** - Tiered permissions and audit logging
- **Open Extension** - Skills/MCP/Experts three-tier extension mechanism
- **Multi-Agent Collaboration** - Complex tasks processed in parallel

## Architecture

sdpost-claw uses a six-layer architecture:

```
┌─────────────────────────────────────────────┐
│ Layer 1: User Interface (Terminal UI)       │
├─────────────────────────────────────────────┤
│ Layer 2: Agent Reasoning (Session Drain)    │
├─────────────────────────────────────────────┤
│ Layer 3: Tool Execution (Type-safe Tools)   │
├─────────────────────────────────────────────┤
│ Layer 4: Extension System (Skills/MCP)      │
├─────────────────────────────────────────────┤
│ Layer 5: Context System (Context Registry)  │
├─────────────────────────────────────────────┤
│ Layer 6: Security & Governance (Audit Log)  │
└─────────────────────────────────────────────┘
```

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/your-org/sdpost-claw.git
cd sdpost-claw

# Install with pip
pip install -e .

# Or install with uv
uv pip install -e .
```

### Configuration

```bash
# Initialize configuration
sdpost init

# Edit config to set your API key
# ~/.sdpost/config.yaml
```

Example config:

```yaml
model:
  provider: openai  # openai | anthropic | deepseek | ollama
  model: gpt-4o
  api_key: sk-your-api-key-here

routing:
  lite_model: gpt-4o-mini
  default_model: gpt-4o
  craft_model: claude-sonnet-4-20250514

permissions:
  default_mode: build  # build | plan | general
```

### Usage

```bash
# Run interactive mode
sdpost run

# Execute a single command
sdpost exec "Analyze the data in report.xlsx"

# Start sidecar server
sdpost serve

# Show status
sdpost status
```

### Interactive Commands

Within the interactive session:

- `help` - Show help
- `new` - Create new session
- `list` - List sessions
- `mode <build|plan|general>` - Switch agent mode
- `clear` - Clear screen
- `quit` - Exit

## Modules

| Module | Description |
|--------|-------------|
| **Agent Core** | Session Drain, Provider-Turn Boundary, Type-safe Tools, Permissions |
| **Desktop Runtime** | Terminal UI, Sidecar Server, Session Management, Model Routing |
| **Context System** | System Context Registry, Context Epoch, Structured Compaction |
| **Extension System** | Multi-Source Skills, MCP Connectors, Agent Modes |
| **Production** | Event Sourcing, Audit Log, Scheduler, Multi-Agent Collaboration |

## Model Providers

Supported providers:

- **OpenAI** - GPT-4o, GPT-4o-mini
- **Anthropic** - Claude Sonnet 4
- **DeepSeek** - DeepSeek Chat
- **Ollama** - Local models (Llama, etc.)

## Built-in Tools

- `read` - Read file contents
- `write` - Write file contents
- `edit` - Edit file with string replacement
- `glob` - Find files by pattern
- `grep` - Search file contents
- `bash` - Execute shell commands
- `webfetch` - Fetch web content
- `question` - Ask user a question

## Agent Modes

- **build** - Full access (default)
- **plan** - Read-only access
- **general** - Sub-agent mode

## Extension

### Skills

Create a `SKILL.md` file in a skills directory:

```markdown
---
name: my-skill
description: My custom skill
---

# My Skill

Skill content in Markdown...
```

### MCP Servers

Configure MCP servers in config:

```yaml
mcp_servers:
  - name: my-server
    transport: stdio
    command: python
    args: ["-m", "my_mcp_server"]
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src/
```

## Design Documents

See [design-docs/](design-docs/) for detailed design documentation.

## License

MIT
