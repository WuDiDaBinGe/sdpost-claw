# sdpost-claw 系统设计总文档 v2

> **v2.1 实现对齐说明（2026-09-03）**：本文档已按 `src/sdpost_claw` 最新实现更新。全局性变更：
> - 新增 **harness/** 包（SessionDriver / Inbox / SessionLog / ToolPipeline / CompactionBridge）作为执行骨架
> - 新增 **desktop/** 包（DesktopServer + pywebview 原生窗口 + Web UI）
> - 模型接入实际只有 **OpenAIProvider**（OpenAI 兼容协议，国产模型"央企国产-only"约束）；Anthropic Provider 未实现
> - CLI 四入口：`run`（交互终端）/ `exec`（单次执行）/ `serve`（Sidecar）/ `desktop`（桌面端），全部经同一 `SessionRunner.run()` 漏斗
> - 生产组件（production/）为独立组件集合，尚未接入桌面主链路

## 1. 项目概述

### 1.1 项目定位
**sdpost-claw** 是一个开源的全场景 AI 办公智能体桌面工作台，复刻腾讯 WorkBuddy 的核心功能。用户可以自然语言下达任务，sdpost-claw 自主思考、拆解任务、规划执行步骤，最终交付可直接验收的工作结果。

### 1.2 核心理念
> "模型是大脑，Harness 是操作系统。sdpost-claw 是让大脑能够长期工作、使用工具、保持上下文、交付文件、接受治理的操作系统。"

### 1.3 设计原则

| 原则 | 说明 |
|------|------|
| **模块化** | 每个模块独立设计、独立实现、独立测试 |
| **可扩展** | Skills/MCP/Experts 三层扩展机制 |
| **安全可控** | 分级权限 + 审计日志 + 沙盒边界 |
| **多模型** | 统一走 OpenAI 兼容协议接入国产大模型（DeepSeek / 通义千问 / 智谱GLM / 月之暗面 / 豆包 / 百川 / MiniMax / 阶跃星辰），"央企国产-only" 约束 |
| **本地优先** | 数据存储在本地，保护用户隐私 |
| **上下文可组合** | 借鉴 opencode，Context Source 可注册、可刷新、可协调 |

### 1.4 v2 版本变更说明

本版本在初版设计基础上，吸收了 **opencode** 的优秀设计思路：

| 改进点 | 初版设计 | v2 改进 |
|--------|----------|---------|
| **上下文管理** | 简单 Prompt 组装 | System Context Registry + Context Epoch |
| **执行模型** | 简单 Agent Loop | Session Drain + Provider-Turn Boundary |
| **工具系统** | 基础 Tool Dispatch | Type-safe Tool Definition + Schema 验证 |
| **权限系统** | AUTO/NOTIFY/CONFIRM/DENY 四级 | Wildcard Permission Ruleset |
| **压缩策略** | 简单截断/摘要 | Structured Compaction Template |
| **Agent 模式** | 单一 Agent | build/plan/general 多模式 |
| **状态变更** | 无明确机制 | Mid-Conversation System Message |

---

## 2. 系统架构

### 2.1 六层架构（v2 增强）

```
┌─────────────────────────────────────────────────────────────────────┐
│ Layer 1: 用户界面 (User Interface)                                   │
│  - Desktop GUI (desktop/: pywebview + Web UI) / Terminal UI / SSE    │
│  - 目标: 功能丰富但不压垮用户                                        │
├─────────────────────────────────────────────────────────────────────┤
│ Layer 2: Agent 推理 (Agent Reasoning)                                │
│  - Session Drain / Safe Provider-Turn Boundary / Harness 骨架        │
│    (SessionDriver turn/step 状态机 + Inbox 双队列 + SessionLog)      │
│  - 目标: 自主决策但可被编排                                          │
├─────────────────────────────────────────────────────────────────────┤
│ Layer 3: 工具执行 (Tool Execution)                                   │
│  - Type-safe Tool Registry / JSON Schema 验证 / 三阶段工具管道       │
│    (pre-execute → execute → post-execute) / Output Bounding          │
│  - 目标: 能力强大但有安全边界                                        │
├─────────────────────────────────────────────────────────────────────┤
│ Layer 4: 扩展系统 (Extension System)                                 │
│  - Multi-Source Skills / Permission-Aware MCP / Experts              │
│  - 目标: 开放生态但可治理                                            │
├─────────────────────────────────────────────────────────────────────┤
│ Layer 5: 上下文系统 (Context System)                                 │
│  - System Context Registry / Context Epoch / Context Snapshot        │
│  - Mid-Conversation 更新（Inbox 注入路径）/ Structured Compaction    │
│  - 目标: 上下文可组合、可刷新、可协调                                │
├─────────────────────────────────────────────────────────────────────┤
│ Layer 6: 安全治理 (Security & Governance)                            │
│  - Wildcard Permission / Event Sourcing / Audit Log                  │
│  - 目标: 本地执行但可审批、可审计、可回滚                            │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 系统总图（v2 增强）

```
┌─────────────────────────────────────────────────────────────────────┐
│                         sdpost-claw System v2                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │              Clients (Layer 1，四个 CLI 入口)                  │   │
│  │  run(终端交互) / exec(单次) / serve(Sidecar) / desktop(GUI)    │   │
│  │  Desktop: pywebview 原生窗口 + Web UI；Sidecar: HTTP+JSON-RPC  │   │
│  └──────────────────────────┬───────────────────────────────────┘   │
│                              │  所有入口经同一 SessionRunner 漏斗    │
│                              ▼                                       │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │        Harness 骨架 + Agent Core (Layer 2, agent/ + harness/)  │   │
│  │  ┌──────────────────────────────────────────────────────┐    │   │
│  │  │   SessionDriver (turn/step 状态机 + soft-stopping)    │    │   │
│  │  │   Inbox (next_turn / next_step 双队列)                │    │   │
│  │  │   SessionLog (append-only 事件日志 → derive_messages) │    │   │
│  │  ├──────────────────────────────────────────────────────┤    │   │
│  │  │        SafeProviderTurnBoundary (agent/drain.py)      │    │   │
│  │  │  - Prompt Promotion      - Tool Result Settlement     │    │   │
│  │  │  - Reconcile → Context Update 注入（step 边界）       │    │   │
│  │  └──────────────────────────────────────────────────────┘    │   │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐             │   │
│  │  │ Agent Mode │  │ 三阶段     │  │ Permission │             │   │
│  │  │ (build/    │  │ Tool       │  │ Ruleset    │             │   │
│  │  │  plan/gen) │  │ Pipeline   │  │ (wildcard) │             │   │
│  │  └────────────┘  └────────────┘  └────────────┘             │   │
│  └──────────────────────────┬───────────────────────────────────┘   │
│                              │                                       │
│            ┌─────────────────┼─────────────────┐                   │
│            ▼                 ▼                 ▼                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐             │
│  │ OpenAI       │  │ Context      │  │ Extension    │             │
│  │ Provider     │  │ System       │  │ System       │             │
│  │ (runtime/)   │  │ (Layer 5)    │  │ (Layer 4)    │             │
│  │ 国产模型统一  │  │ - Sources    │  │ - Skills     │             │
│  │ OpenAI 兼容  │  │ - Registry   │  │ - MCP        │             │
│  │ 协议         │  │ - Epoch      │  │ - Experts    │             │
│  │              │  │ - Snapshot   │  │              │             │
│  │              │  │ - Compaction │  │              │             │
│  │              │  │   + Bridge   │  │              │             │
│  └──────────────┘  └──────────────┘  └──────────────┘             │
│            │                 │                 │                   │
│            └─────────────────┼─────────────────┘                   │
│                              ▼                                       │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │              Security & Governance (Layer 6, production/)     │   │
│  │  - Event Sourcing(哈希链) / Audit Log / Wildcard Permissions  │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                              │                                       │
│                              ▼                                       │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                   Persistence Layer                           │   │
│  │  - sessions/ 元数据 JSON + messages/ JSONL（harness 落盘）    │   │
│  │  - events/ JSONL（哈希链）/ SQLite（production，未接线）      │   │
│  │  - tool_outputs/ 外部化 / transcripts/（runtime 事件溯源）    │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. 模块总览

### 3.1 模块清单

| 模块编号 | 模块名称 | 核心能力 | 参考来源 |
|----------|----------|----------|----------|
| Module 01 | Agent Core v2 | Session Drain、SafeProviderTurnBoundary、Agent Modes、Type-safe Tools（8 个内置）、Wildcard Permission Ruleset、harness 骨架（driver/inbox/session_log/tool_pipeline） | learn-workbuddy s01-s04, **opencode** |
| Module 02 | Desktop Runtime v2 | Desktop GUI（desktop/）、Terminal UI、Sidecar Server（HTTP+JSON-RPC+SSE）、SessionManager、OpenAIProvider、JSONL Transcript | learn-workbuddy s05-s09, **opencode** |
| Module 03 | Memory & Context v2 | System Context Registry（7 个内置源）、Context Epoch、Context Snapshot、Mid-Conversation 更新（Inbox 注入）、CompactionEngine + CompactionBridge、Workspace/User Memory、Output Externalization | learn-workbuddy s10-s15, **opencode** |
| Module 04 | Extension System v2 | Multi-Source Skills、MCP Connectors（JSON-RPC）、Experts（5 个默认）+ Agent Modes | learn-workbuddy s16-s18, **opencode** |
| Module 05 | Production & Multi-Agent v2 | Event Sourcing（哈希链）、Audit Log、SQLite（WAL，8 表）、Automation Scheduler（croniter）、Supervisor + MessageBus | learn-workbuddy s21-s24, wanman, **opencode** |

### 3.2 模块依赖关系

```
                    ┌──────────────────┐
                    │   Terminal UI    │
                    │  (Module 02.1)   │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  Sidecar Server  │
                    │  (Module 02.2)   │
                    └────────┬─────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────────┐
│                      Agent Core (Module 01)                         │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                    Session Drain                              │  │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐             │  │
│  │  │ Prompt     │  │ Context    │  │ Tool       │             │  │
│  │  │ Promotion  │  │ Reconcile  │  │ Settlement │             │  │
│  │  └────────────┘  └────────────┘  └────────────┘             │  │
│  └──────────────────────────────────────────────────────────────┘  │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐                  │
│  │ Agent Mode │  │ Type-safe  │  │ Permission │                  │
│  │            │  │ Tools      │  │ Ruleset    │                  │
│  └────────────┘  └────────────┘  └────────────┘                  │
└───────┬──────────┬──────────┬──────────┬──────────────────────────┘
        │          │          │          │
        ▼          ▼          ▼          ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│  Model   │ │ Context  │ │Extension │ │ Security │
│ Routing  │ │ System   │ │ System   │ │ (Audit)  │
│(M02.4)   │ │(Module03)│ │(Module04)│ │(Module05)│
└──────────┘ └──────────┘ └──────────┘ └──────────┘
        │          │          │          │
        └──────────┴──────────┴──────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │   Persistence    │
                    │  (SQLite/JSONL)  │
                    └──────────────────┘
```

---

## 4. 核心数据流

### 4.1 任务执行全流程（v2 增强）

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Task Execution Flow v2                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  1. 用户输入                                                         │
│     "帮我分析这个 Excel 文件的数据"                                    │
│         │                                                            │
│         ▼                                                            │
│  2. 客户端接收输入（Terminal UI / Desktop SSE / Sidecar HTTP）        │
│     → SessionManager.submit_prompt 入 Inbox next_turn                │
│         │                                                            │
│         ▼                                                            │
│  3. SessionDriver 开启 turn → 每步触发 SessionRunner.run() 漏斗      │
│         │                                                            │
│         ▼                                                            │
│  4. SafeProviderTurnBoundary 准备                                    │
│     ┌─────────────────────────────────────────────────────────┐     │
│     │  a. Prompt Promotion: 从 Inbox next_turn 推进输入到历史  │     │
│     │  b. Reconcile（Phase 4）: 比较 Snapshot，变更文本经      │     │
│     │     Inbox 注入路径在 step 边界拼入 system context        │     │
│     │     （## Context Update 段）                             │     │
│     │  c. 压缩压力检查（Phase 5）: CompactionBridge.maybe_     │     │
│     │     compact() → 摘要写 session.summary → 下一 turn       │     │
│     │     经 SummaryContextSource 呈现                         │     │
│     │  d. Tool Result Settlement: 结算已完成工具结果           │     │
│     │  e. 组装 system context + messages + tools               │     │
│     └─────────────────────────────────────────────────────────┘     │
│         │                                                            │
│         ▼                                                            │
│  5. 模型调用                                                         │
│     （实际：单一 OpenAIProvider，config.model 指定国产模型；          │
│       ModelRouter 三档路由已实现但未接线；支持流式 delta）           │
│         │                                                            │
│         ▼                                                            │
│  6. 模型推理 → 返回工具调用                                          │
│     tool_call: read_file("data.xlsx")                                │
│         │                                                            │
│         ▼                                                            │
│  7. Permission Ruleset 检查权限                                      │
│     - 通配符匹配: "file.read.*"                                      │
│     - ✅ 允许执行                                                    │
│         │                                                            │
│         ▼                                                            │
│  8. Type-safe Tool Execution                                         │
│     - Schema 验证输入                                                │
│     - 执行工具                                                       │
│     - Schema 验证输出                                                │
│     - Output Bounding (大输出外部化)                                  │
│         │                                                            │
│         ▼                                                            │
│  9. 结果回传 → Tool Result Settled                                   │
│         │                                                            │
│         ▼                                                            │
│  10. 自我校验                                                        │
│      - 任务完成 → 返回结果                                           │
│      - 需要继续 → 继续循环                                           │
│         │                                                            │
│         ▼                                                            │
│  11. 结果交付                                                        │
│      - 更新 Workspace Memory                                         │
│      - 记录 Event Store                                              │
│      - 更新 Context Snapshot                                         │
│         │                                                            │
│         ▼                                                            │
│  12. UI 展示结果                                                     │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 4.2 Context Epoch 生命周期

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Context Epoch Lifecycle                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐        │
│  │ Initialize   │────▶│   Active     │────▶│   Reconcile  │        │
│  │              │     │              │     │              │        │
│  │ - Load all   │     │ - Baseline   │     │ - Compare    │        │
│  │   Sources    │     │   immutable  │     │   Snapshot   │        │
│  │ - Generate   │     │ - Snapshot   │     │ - Generate   │        │
│  │   Baseline   │     │   for diff   │     │   Updates    │        │
│  │ - Create     │     │              │     │              │        │
│  │   Snapshot   │     │              │     │              │        │
│  └──────────────┘     └──────────────┘     └──────┬───────┘        │
│                                                    │                 │
│                         ┌──────────────────────────┼──────────┐     │
│                         │                          │          │     │
│                         ▼                          ▼          ▼     │
│                  ┌──────────────┐          ┌──────────────┐        │
│                  │   Updated    │          │  Replacement │        │
│                  │              │          │   Ready      │        │
│                  │ - Generate   │          │              │        │
│                  │   MidConv    │          │ - Full       │        │
│                  │   SystemMsg  │          │   Replace    │        │
│                  │ - Update     │          │ - New Epoch  │        │
│                  │   Snapshot   │          │              │        │
│                  └──────────────┘          └──────────────┘        │
│                                                                      │
│  ┌──────────────┐                                                   │
│  │ Compact      │  ← 触发 Replacement                               │
│  │              │                                                   │
│  │ - Structured │                                                   │
│  │   Summary    │                                                   │
│  │ - New Epoch  │                                                   │
│  └──────────────┘                                                   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

> **实际接线现状（Phase 4/5）**：压缩摘要**不走基线 replace / 新 Epoch**，而是经 `session.summary → SummaryContextSource → reconcile "新可用源" → Inbox 注入路径` 呈现为 `## Previous Session Summary`；`SessionLog.replace(start, end)` 物理替换（SurfaceOp.replace）与新 Epoch 持久化留待后续阶段（详见 Module 03 §4.4）。

---

## 5. 技术选型

### 5.1 核心技术栈

| 层级 | 技术选型 | 说明 |
|------|----------|------|
| **编程语言** | Python 3.11+ | 生态丰富，AI/ML 领域主流 |
| **CLI 框架** | click | run/exec/init/status/serve/desktop 子命令 |
| **终端 UI** | Rich | Terminal UI（ui.py） |
| **桌面 GUI** | pywebview + aiohttp + 原生 HTML/JS | desktop/ 包：原生窗口包装 Web UI |
| **Web 框架** | aiohttp | Sidecar / Desktop 服务器 + SSE |
| **模型 SDK** | openai (AsyncOpenAI) | OpenAI 兼容协议接入全部国产模型 |
| **数据库** | SQLite + WAL（同步 sqlite3） | production/database.py，8 张表 |
| **数据格式** | JSON / JSONL / YAML | sessions 元数据、事件日志、config、技能 frontmatter |
| **Schema 验证** | JSON Schema（工具定义内嵌 input_schema） | 类型安全的工具输入校验 |
| **异步 IO** | asyncio + aiofiles | 会话/事件/外部化存储 |
| **调度** | croniter | Automation Scheduler cron 表达式 |
| **包管理** | uv / pip | 现代 Python 包管理 |
| **测试框架** | pytest | 标准测试框架 |

### 5.2 模型 Provider 支持（实际：OpenAI 兼容协议统一接入）

| 厂商 | 接入方式 | 状态 |
|------|----------|------|
| DeepSeek / 通义千问（阿里云）/ 智谱GLM / 月之暗面 / 豆包（火山引擎）/ 百川 / MiniMax / 阶跃星辰 | OpenAI 兼容协议（`create_provider` 统一返回 `OpenAIProvider`，缺 base_url 时按厂商名映射默认地址） | ✅ 支持 |
| 本地模型（Ollama 等） | OpenAI 兼容协议，本地端点免 API Key | ✅ 支持 |
| Anthropic | Messages API（AnthropicProvider） | ❌ 未实现（"央企国产-only" 约束下不规划） |

> 分层路由 `ModelRouter`（LITE=deepseek-chat / DEFAULT=deepseek-v3 / CRAFT=qwen-max）已实现，尚未接入会话执行链路。

---

## 6. 项目结构（v2.1，按实际代码更新）

```
sdpost-claw/
├── design-docs/                        # 设计文档
│   ├── 00-system-design-overview.md    # 初版总览
│   ├── 00-system-design-overview-v2.md # v2 总览（本文件）
│   └── modules/
│       ├── 01-agent-core-v2.md         # v2 Agent Core
│       ├── 02-desktop-runtime-v2.md    # v2 Desktop Runtime
│       ├── 03-memory-context-v2.md     # v2 Memory & Context
│       ├── 04-extension-system-v2.md   # v2 Extension System
│       └── 05-production-multiagent-v2.md # v2 Production
├── src/                                # 源代码
│   └── sdpost_claw/
│       ├── main.py                     # CLI 入口：run/exec/init/status/serve/desktop
│       ├── config.py                   # Config / ModelEntry / DEFAULT_MODELS
│       │
│       ├── agent/                      # Module 01: Agent Core
│       │   ├── drain.py                # Session / SessionRunner / SafeProviderTurnBoundary /
│       │   │                           #   PromptPromotion / DrainResult / ModelResponse
│       │   ├── tools.py                # ToolDefinition + ToolRegistry + BuiltInTools（8 个内置）
│       │   ├── permissions.py          # PermissionRuleset（通配符，last-match-wins）+ AgentPermissions
│       │   └── modes.py                # AgentMode / Agent / AgentRegistry
│       │
│       ├── harness/                    # Module 01: Harness 执行骨架（v2 新增）
│       │   ├── driver.py               # SessionDriver（turn/step 状态机 + soft-stopping hooks）
│       │   ├── inbox.py                # Inbox（next_turn / next_step 双队列）
│       │   ├── session_log.py          # SessionLog（append-only 事件日志，derive_messages）
│       │   ├── tool_pipeline.py        # 三阶段工具管道（pre/execute/post）
│       │   ├── compaction_bridge.py    # CompactionBridge（压缩协调器，Phase 5）
│       │   └── events.py               # harness 事件类型
│       │
│       ├── runtime/                    # Module 02: Desktop Runtime
│       │   ├── ui.py                   # Terminal UI (Rich)
│       │   ├── server.py               # SidecarServer（HTTP + JSON-RPC /api/rpc + SSE）
│       │   ├── session.py              # SessionLifecycle / SessionStore / SessionManager
│       │   ├── routing.py              # ModelRouter（三档分层，未接线）
│       │   ├── providers.py            # ModelProvider / OpenAIProvider / create_provider
│       │   └── transcript.py           # JSONLTranscript（transcripts/ 事件溯源）
│       │
│       ├── desktop/                    # Module 02: Desktop GUI（v2 新增）
│       │   ├── app.py                  # DesktopApp（pywebview 原生窗口）
│       │   ├── server.py               # DesktopServer（集成应用服务器，全量 API + SSE）
│       │   └── web/                    # 原生 HTML/CSS/JS 前端
│       │
│       ├── context/                    # Module 03: Context System
│       │   ├── source.py               # ContextSource 接口 + Unavailable + 7 个内置源
│       │   ├── registry.py             # SystemContextRegistry + Snapshot/Generation/结果类型
│       │   ├── epoch.py                # ContextEpoch
│       │   ├── snapshot.py / reconcile.py
│       │   ├── midconv.py              # MidConversationSystemMessage + Handler
│       │   ├── compaction.py           # 模板 + CompactionConfig + 无状态 CompactionEngine
│       │   └── sources/                # （占位包，内置源实际在 source.py）
│       │
│       ├── memory/                     # Module 03: Memory
│       │   ├── workspace.py            # WorkspaceMemory + WorkspaceMemoryStore（文件 JSON）
│       │   ├── user.py                 # User Memory
│       │   └── externalize.py          # OutputExternalizer（工具输出外部化）
│       │
│       ├── extensions/                 # Module 04: Extension System
│       │   ├── skills.py               # SkillRegistry / SkillSource（多来源发现）
│       │   ├── mcp.py                  # MCPConnector / transports（JSON-RPC 直连）
│       │   └── experts.py              # Expert + ExpertRegistry（5 个默认专家）
│       │
│       ├── production/                 # Module 05: Production（独立组件，未接线）
│       │   ├── database.py             # Database（同步 sqlite3 + WAL，8 表 schema）
│       │   ├── events.py               # EventType / Event / EventStore（哈希链）
│       │   ├── audit.py                # AuditLog（仅依赖 EventStore）
│       │   ├── scheduler.py            # ScheduledTask / AutomationScheduler（croniter + 回调）
│       │   └── multiagent.py           # Supervisor / MessageBus（优先级队列）
│       │
│       └── common/                     # 公共组件
│           ├── events.py               # 事件辅助
│           ├── storage.py              # Storage Abstraction
│           └── utils.py                # generate_id / truncate_text 等
│
├── tests/                              # 测试
├── examples/                           # 示例
├── docs/                               # 文档
├── pyproject.toml                      # 项目配置
├── README.md                           # 项目说明
└── LICENSE                             # 许可证
```

---

## 7. 核心概念（v2 新增）

### 7.1 opencode 设计概念映射

| opencode 概念 | sdpost-claw 实现 | 说明 |
|--------------|-----------------|------|
| **System Context** | `SystemContextRegistry` | 可组合的上下文源载体 |
| **Context Source** | `ContextSource[A]` | 独立刷新的类型化值 |
| **Context Epoch** | `ContextEpoch` | 上下文不可变时间跨度 |
| **Context Snapshot** | `Snapshot` | 可比较的 JSON 状态 |
| **Baseline System Context** | `Generation.baseline` | 首次渲染的模型可见文本 |
| **Mid-Conversation System Message** | `MidConversationSystemMessage` | 对话中状态变更指令 |
| **Safe Provider-Turn Boundary** | `SafeProviderTurnBoundary` | 安全模型调用边界 |
| **Prompt Promotion** | `PromptPromotion` | 输入推进机制 |
| **Session Drain** | `SessionRunner` | 进程本地执行协调 |
| **Compaction** | `CompactionEngine`（策略）+ `CompactionBridge`（协调器） | 结构化压缩 |
| **Model Tool Output** | `ToolDefinition.max_output_chars` | 输出大小限制 |
| **Agent Modes** | `AgentMode` (build/plan/general) | 多模式 Agent |
| **Permission Ruleset** | `PermissionRuleset` | 通配符权限规则 |
| **Input Queues** | `Inbox` (next_turn / next_step) | 双队列输入管理 |
| **Turn/Step Lifecycle** | `SessionDriver` | turn/step 状态机 + soft-stopping |
| **Append-only Log** | `SessionLog` | 事件日志 → derive_messages 权威数据源 |

### 7.2 Context Source 清单

| Source Key | 类型 | 刷新频率 | 说明 |
|------------|------|----------|------|
| `date/current` | `DateValue` | 每次 Drain | 当前日期时间 |
| `project/instructions` | `InstructionsValue` | 文件变更时 | 项目指令文件 |
| `agent/skills` | `SkillsValue` | 技能变更时 | 可用技能列表 |
| `agent/info` | `AgentValue` | Agent 变更时 | 当前 Agent 信息 |
| `workspace/memory` | `MemoryValue` | 记忆更新时 | 工作区记忆 |
| `user/preferences` | `PreferencesValue` | 偏好变更时 | 用户偏好 |
| `session/summary` | `SummaryValue` | 压缩后 | 会话摘要 |

---

## 8. 数据模型总览

### 8.1 核心实体

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Core Data Entities v2                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────┐       ┌──────────────┐       ┌──────────────┐    │
│  │   Session    │ 1───N │ ContextEpoch │ 1───N │   Snapshot   │    │
│  │              │       │              │       │              │    │
│  │ - id         │       │ - id         │       │ - entries    │    │
│  │ - cwd        │       │ - baseline   │       │ - hash       │    │
│  │ - mode       │       │ - started_at │       └──────────────┘    │
│  │ - status     │       │ - ended_at   │                            │
│  └──────────────┘       └──────────────┘                            │
│         │                       │                                    │
│         │                       │                                    │
│         ▼                       ▼                                    │
│  ┌──────────────┐       ┌──────────────┐                            │
│  │   Message    │       │    Event     │                            │
│  │              │       │              │                            │
│  │ - id         │       │ - type       │                            │
│  │ - session_id │       │ - session_id │                            │
│  │ - role       │       │ - data       │                            │
│  │ - content    │       │ - hash_chain │                            │
│  └──────────────┘       └──────────────┘                            │
│                                                                      │
│  ┌──────────────┐       ┌──────────────┐       ┌──────────────┐    │
│  │  ToolCall    │       │  AuditLog    │       │    Agent     │    │
│  │              │       │              │       │              │    │
│  │ - id         │       │ - action     │       │ - id         │    │
│  │ - input      │       │ - effect     │       │ - mode       │    │
│  │ - output     │       │ - rule       │       │ - permissions│    │
│  │ - structured │       │ - timestamp  │       │ - skills     │    │
│  │ - externalized      │              │       └──────────────┘    │
│  └──────────────┘       └──────────────┘                            │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 9. 三大根本矛盾与解决方案（v2 更新）

| 根本矛盾 | 直接后果 | v2 解决方案 | 对应模块 |
|----------|----------|-------------|----------|
| **上下文有限 vs 信息无限** | 工具输出、历史、记忆和 schema 会挤爆窗口 | System Context Registry、Context Epoch、Structured Compaction、Output Bounding | Module 01, 03 |
| **自主执行 vs 安全可控** | Agent 越有用，越像本地执行系统 | Wildcard Permission Ruleset、Event Sourcing、Audit Hash Chain、Agent Modes | Module 01, 05 |
| **模型成本 vs 任务复杂度** | 全部用最强模型成本太高 | lite/default/craft 路由、Multi-Agent 分工、Session Drain 优化 | Module 02, 05 |

---

## 10. 开发路线图（v2 更新，含实际状态）

### Phase 1: MVP — ✅ 已完成

| 周 | 目标 | 状态 |
|---|------|------|
| W1 | Session Drain + SafeProviderTurnBoundary（agent/drain.py + harness/） | ✅ |
| W2 | System Context Registry + 内置 Sources（context/） | ✅ |
| W3 | Type-safe Tool Registry + Permission Ruleset | ✅ |
| W4 | Terminal UI + Sidecar Server + Desktop GUI | ✅ |

### Phase 2: 核心功能 — ✅ 基本完成

| 周 | 目标 | 状态 |
|---|------|------|
| W5 | Context Epoch + Snapshot（类型完成，持久化触发待接入） | ⚠️ |
| W6 | Mid-Conversation 更新（Inbox 注入路径，Phase 4） | ✅ |
| W7 | Structured Compaction（CompactionBridge，Phase 5） | ✅ |
| W8 | Event Sourcing + Audit Log（production/ 组件完成，未接线） | ⚠️ |

### Phase 3: 扩展能力 — ⚠️ 部分完成

| 周 | 目标 | 状态 |
|---|------|------|
| W9 | Multi-Source Skills | ✅ |
| W10 | Permission-Aware MCP（传输+JSON-RPC 完成；ToolRegistry 接线待完成） | ⚠️ |
| W11 | Agent Modes（build/plan/general）+ Experts | ✅ |
| W12 | Multi-Agent Collaboration（Supervisor/MessageBus 骨架完成） | ⚠️ |

### Phase 4: 生产化 — ⏳ 待推进

| 周 | 目标 | 状态 |
|---|------|------|
| W13 | SQLite + Context Epoch 持久化（schema/Database 完成，未接线） | ⚠️ |
| W14 | Automation Scheduler（骨架完成，装配未接线） | ⚠️ |
| W15 | 集成测试 + 文档 | ⏳ |
| W16 | 性能优化 + 发布 | ⏳ |

### 后续待办汇总

1. Context Epoch 持久化触发（`start_new_epoch` 接入会话生命周期）
2. `SessionLog.replace(start, end)` surface op（压缩后物理收缩历史）
3. ModelRouter 三档路由接入 SessionRunner
4. MCP 工具包装为 ToolDefinition 并注册 ToolRegistry
5. Skill 权限过滤（`get_available_for_agent`）
6. production/ 组件（EventStore / AuditLog / Scheduler / Supervisor）接入桌面主链路
7. Sub-Agent 输入链路（Supervisor prompt → Inbox）

---

## 11. 接口总览

### 11.1 模块间接口矩阵

| 模块 | 依赖 | 被依赖 | 关键接口 |
|------|------|--------|----------|
| Agent Core | Context System, Session, Model | UI, Sidecar | `SessionRunner.run()` |
| Desktop Runtime | Agent Core, Context System | UI, Extensions | `SidecarServer.handle_run()` |
| Context System | Session, Storage | Agent Core, Extensions | `SystemContextRegistry.reconcile()` |
| Extension System | Tool Registry, Context System | Agent Core | `SkillRegistry.list_all()` |
| Production | Agent Core, Session, Event Store | 所有模块 | `Supervisor.execute_task()` |

### 11.2 外部接口

| 接口 | 协议 | 说明 |
|------|------|------|
| HTTP API | HTTP/JSON | Sidecar Server 对外接口 |
| ACP Protocol | JSON-RPC 2.0 | Agent 通信协议 |
| SSE | Server-Sent Events | 实时事件推送 |
| MCP | stdio/SSE/HTTP | 外部工具协议 |

---

## 12. 安全设计

### 12.1 安全层级

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Security Layers v2                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Layer 1: 权限控制 (Wildcard Ruleset)                                │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  Rules:                                                     │    │
│  │  - allow "file.read.*"    (读取操作自动允许)                │    │
│  │  - allow "network.*"      (网络请求自动允许)                │    │
│  │  - deny  "shell.rm -rf"   (危险命令禁止)                    │    │
│  │  - ask   "file.write.*"   (写操作需确认)                    │    │
│  │                                                             │    │
│  │  匹配策略: Last Match Wins (最后匹配生效)                   │    │
│  │  优先级: deny > ask > allow                                 │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  Layer 2: Agent 模式                                                 │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  - build:  完全访问 (默认)                                  │    │
│  │  - plan:   只读权限 (分析/规划)                             │    │
│  │  - general: 子 Agent 模式                                   │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  Layer 3: 审计日志 (Event Sourcing)                                  │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  - 所有操作记录到 JSONL                                      │    │
│  │  - 哈希链保证不可篡改                                        │    │
│  │  - 支持完整性验证                                            │    │
│  │  - 支持会话重放                                              │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  Layer 4: 数据保护                                                   │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  - 本地存储优先                                              │    │
│  │  - 敏感信息加密                                              │    │
│  │  - 用户数据隔离                                              │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 13. 测试策略

### 13.1 测试层级

| 层级 | 范围 | 工具 | 覆盖率目标 |
|------|------|------|------------|
| 单元测试 | 单个模块/函数 | pytest | 80%+ |
| 集成测试 | 模块间协作 | pytest | 70%+ |
| E2E 测试 | 完整用户流程 | pytest + 模拟 | 关键路径 100% |
| 性能测试 | 负载/压力 | pytest-benchmark | 基准测试 |

### 13.2 关键测试场景（v2 新增）

- Context Source 正确加载和刷新
- Context Reconciliation 正确生成更新
- Provider-Turn Boundary 正确推进输入
- Structured Compaction 生成高质量摘要
- Wildcard Permission 正确匹配
- Agent Modes 正确切换权限
- Event Store 哈希链完整性

---

## 14. 部署与分发

### 14.1 分发方式

| 方式 | 说明 |
|------|------|
| PyPI | `pip install sdpost-claw` |
| 源码 | `git clone` + `pip install -e .` |
| 单文件 | PyInstaller 打包 |

### 14.2 系统要求

| 要求 | 说明 |
|------|------|
| Python | 3.11+ |
| OS | macOS / Windows / Linux |
| 内存 | 4GB+ |
| 磁盘 | 500MB+ |
| 网络 | 调用远程模型时需要 |

---

## 15. 总结

sdpost-claw v2 是一个模块化的全场景 AI 办公智能体系统，通过借鉴 opencode 的优秀设计，实现了：

1. **可组合的上下文系统** — Context Source 可注册、可刷新、可协调
2. **清晰的执行边界** — Session Drain + Provider-Turn Boundary
3. **类型安全的工具** — Schema 验证 + Output Bounding
4. **灵活的权限控制** — Wildcard Permission Ruleset
5. **高质量的压缩** — Structured Compaction Template
6. **多模式 Agent** — build/plan/general 模式切换
7. **完整的事件溯源** — Event Sourcing + Audit Hash Chain
8. **自然语言交互** — 用户用一句话描述需求
9. **自主规划执行** — Agent 自动拆解和完成任务
10. **长期记忆** — 跨会话保持上下文
11. **开放扩展** — Skills/MCP/Experts 三层扩展
12. **多 Agent 协作** — 复杂任务并行处理

---

## 附录：opencode 设计借鉴索引

| 借鉴点 | opencode 源码 | sdpost-claw 实现 |
|--------|--------------|-----------------|
| System Context | `system-context/index.ts` | `context/registry.py` |
| Context Source | `system-context/index.ts` Source<A> | `context/source.py` ContextSource[A] |
| Context Epoch | `session/context-epoch.ts` | `context/epoch.py` |
| Compaction | `session/compaction.ts` | `context/compaction.py` + `harness/compaction_bridge.py` |
| Tool Definition | `tool/tool.ts` | `agent/tools.py` ToolDefinition |
| Permission | `permission.ts` | `agent/permissions.py` |
| Skill Discovery | `skill.ts` | `extensions/skills.py` |
| Session Runner | `session/runner/index.ts` | `agent/drain.py` SessionRunner |
| Input Queues | `session/inbox` | `harness/inbox.py` Inbox |
| Agent Log | `session/log` | `harness/session_log.py` SessionLog |

---

*文档版本: v2.1 | 创建日期: 2026-08-27 | 最近更新: 2026-09-03*
*参考项目: learn-workbuddy, wanman, WorkBuddy, opencode*
