# sdpost-claw 系统设计总文档

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
| **多模型** | 支持 DeepSeek / OpenAI / Anthropic 等多 Provider |
| **本地优先** | 数据存储在本地，保护用户隐私 |

---

## 2. 系统架构

### 2.1 六层架构

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: 用户界面 (User Interface)                           │
│  - Terminal UI / 流式输出 / 任务状态                         │
│  - 目标: 功能丰富但不压垮用户                                │
├─────────────────────────────────────────────────────────────┤
│ Layer 2: Agent 推理 (Agent Reasoning)                        │
│  - Agent Loop / 任务规划 / 自我校验                          │
│  - 目标: 自主决策但可被编排                                  │
├─────────────────────────────────────────────────────────────┤
│ Layer 3: 工具执行 (Tool Execution)                           │
│  - Tool Registry / 内置工具 / Shell 执行                     │
│  - 目标: 能力强大但有安全边界                                │
├─────────────────────────────────────────────────────────────┤
│ Layer 4: 扩展系统 (Extension System)                         │
│  - Skills / MCP Connectors / Experts                         │
│  - 目标: 开放生态但可治理                                    │
├─────────────────────────────────────────────────────────────┤
│ Layer 5: 记忆系统 (Memory System)                            │
│  - Workspace Memory / User Memory / Context Compact          │
│  - 目标: 长期记忆但控制隐私和成本                            │
├─────────────────────────────────────────────────────────────┤
│ Layer 6: 安全治理 (Security & Governance)                    │
│  - Permission Hooks / Audit Log / Sandbox                    │
│  - 目标: 本地执行但可审批、可审计、可回滚                    │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 系统总图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         sdpost-claw System                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                     Terminal UI (Layer 1)                     │   │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐             │   │
│  │  │ 对话区域    │  │ 任务状态   │  │ 侧边栏     │             │   │
│  │  └────────────┘  └────────────┘  └────────────┘             │   │
│  └──────────────────────────┬───────────────────────────────────┘   │
│                              │                                       │
│                              ▼                                       │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                  Sidecar Server (Layer 1)                     │   │
│  │  - HTTP API / ACP Protocol / SSE 实时推送                     │   │
│  └──────────────────────────┬───────────────────────────────────┘   │
│                              │                                       │
│                              ▼                                       │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                   Agent Core (Layer 2)                        │   │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐             │   │
│  │  │ Agent Loop │  │ Tool       │  │ Permission │             │   │
│  │  │            │  │ Dispatch   │  │ Hooks      │             │   │
│  │  └────────────┘  └────────────┘  └────────────┘             │   │
│  │  ┌────────────┐  ┌────────────┐                              │   │
│  │  │ Deferred   │  │ Model      │                              │   │
│  │  │ Loading    │  │ Routing    │                              │   │
│  │  └────────────┘  └────────────┘                              │   │
│  └──────────────────────────┬───────────────────────────────────┘   │
│                              │                                       │
│            ┌─────────────────┼─────────────────┐                   │
│            │                 │                 │                   │
│            ▼                 ▼                 ▼                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐             │
│  │ Tool Registry│  │ Memory       │  │ Extension    │             │
│  │ (Layer 3)    │  │ System       │  │ System       │             │
│  │              │  │ (Layer 5)    │  │ (Layer 4)    │             │
│  │ - File Ops   │  │              │  │              │             │
│  │ - Shell      │  │ - Workspace  │  │ - Skills     │             │
│  │ - Network    │  │ - User       │  │ - MCP        │             │
│  │ - Sub-Agent  │  │ - Context    │  │ - Experts    │             │
│  └──────────────┘  └──────────────┘  └──────────────┘             │
│            │                 │                 │                   │
│            └─────────────────┼─────────────────┘                   │
│                              │                                       │
│                              ▼                                       │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │              Security & Governance (Layer 6)                  │   │
│  │  - Permission Hooks / Audit Log / Sandbox Policy              │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                              │                                       │
│                              ▼                                       │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                   Persistence Layer                           │   │
│  │  - SQLite / JSONL Transcripts / Memory Files                  │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. 模块总览

### 3.1 模块清单

| 模块编号 | 模块名称 | 核心能力 | 参考来源 |
|----------|----------|----------|----------|
| Module 01 | Agent Core | Agent Loop、Tool Dispatch、Deferred Loading、Permission Hooks | learn-workbuddy s01-s04 |
| Module 02 | Desktop Runtime | Terminal UI、Sidecar Server、Session Management、Model Routing、JSONL Transcript | learn-workbuddy s05-s09 |
| Module 03 | Memory & Context | Workspace Memory、User Memory、Output Externalization、Context Compact、Prompt Assembly | learn-workbuddy s10-s15 |
| Module 04 | Extension System | Skills System、MCP Connectors、Experts System | learn-workbuddy s16-s18, WorkBuddy SkillHub |
| Module 05 | Production & Multi-Agent | SQLite Database、Automation Scheduler、Audit & Sandbox、Multi-Agent Collaboration | learn-workbuddy s21-s24, wanman |

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
┌────────────────────────────────────────────────────────────┐
│                      Agent Core (Module 01)                 │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐      │
│  │  Loop   │  │ Dispatch│  │ Deferred│  │Permission│      │
│  └─────────┘  └─────────┘  └─────────┘  └─────────┘      │
└───────┬──────────┬──────────┬──────────┬───────────────────┘
        │          │          │          │
        ▼          ▼          ▼          ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│  Model   │ │  Memory  │ │Extension │ │ Security │
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

### 4.1 任务执行全流程

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Task Execution Flow                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  1. 用户输入                                                         │
│     "帮我分析这个 Excel 文件的数据"                                    │
│         │                                                            │
│         ▼                                                            │
│  2. Terminal UI 接收输入                                             │
│         │                                                            │
│         ▼                                                            │
│  3. Sidecar Server 创建/恢复会话                                     │
│         │                                                            │
│         ▼                                                            │
│  4. Agent Loop 启动                                                  │
│         │                                                            │
│         ▼                                                            │
│  5. Prompt Assembler 组装上下文                                      │
│     ┌─────────────────────────────────────────────────────────┐     │
│     │ - System Prompt                                        │     │
│     │ - User Memory (用户偏好)                                │     │
│     │ - Workspace Memory (项目记忆)                           │     │
│     │ - Tool Specs (可用工具)                                 │     │
│     │ - 对话历史                                              │     │
│     └─────────────────────────────────────────────────────────┘     │
│         │                                                            │
│         ▼                                                            │
│  6. Model Router 选择模型                                            │
│     (根据任务复杂度选择 lite/default/craft)                           │
│         │                                                            │
│         ▼                                                            │
│  7. 模型推理 → 返回工具调用                                          │
│     tool_call: read_file("data.xlsx")                                │
│         │                                                            │
│         ▼                                                            │
│  8. Permission Hooks 检查权限                                        │
│     ✅ 允许执行                                                      │
│         │                                                            │
│         ▼                                                            │
│  9. Tool Dispatch 分发执行                                           │
│         │                                                            │
│         ▼                                                            │
│  10. Tool Execution                                                  │
│      - 读取文件内容                                                  │
│      - 大输出 → Output Externalization                               │
│         │                                                            │
│         ▼                                                            │
│  11. 结果回传 Agent Loop                                             │
│         │                                                            │
│         ▼                                                            │
│  12. 自我校验                                                        │
│      - 任务完成 → 返回结果                                           │
│      - 需要继续 → 继续循环                                           │
│         │                                                            │
│         ▼                                                            │
│  13. 结果交付                                                        │
│      - 生成分析报告                                                  │
│      - 更新 Workspace Memory                                         │
│      - 记录 Audit Log                                                │
│         │                                                            │
│         ▼                                                            │
│  14. UI 展示结果                                                     │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 4.2 多 Agent 协作流程

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Multi-Agent Collaboration                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  用户目标: "生成一份季度业务分析报告"                                  │
│         │                                                            │
│         ▼                                                            │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                     Supervisor 分解任务                        │   │
│  │  1. 收集各部门数据                                             │   │
│  │  2. 分析数据趋势                                               │   │
│  │  3. 生成可视化图表                                             │   │
│  │  4. 撰写分析报告                                               │   │
│  └──────────────────────────┬───────────────────────────────────┘   │
│                              │                                       │
│            ┌─────────────────┼─────────────────┐                   │
│            │                 │                 │                   │
│            ▼                 ▼                 ▼                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐             │
│  │ 数据分析师    │  │ 数据分析师    │  │ 文档撰写师    │             │
│  │ (收集数据)    │  │ (分析趋势)    │  │ (撰写报告)    │             │
│  │              │  │              │  │              │             │
│  │ 独立工作区    │  │ 独立工作区    │  │ 独立工作区    │             │
│  └──────────────┘  └──────────────┘  └──────────────┘             │
│            │                 │                 │                   │
│            └─────────────────┼─────────────────┘                   │
│                              │                                       │
│                              ▼                                       │
│                    ┌──────────────────┐                             │
│                    │  Shared Context  │                             │
│                    │  (共享中间结果)   │                             │
│                    └──────────────────┘                             │
│                              │                                       │
│                              ▼                                       │
│                    ┌──────────────────┐                             │
│                    │  可视化专家       │                             │
│                    │  (生成图表)       │                             │
│                    └──────────────────┘                             │
│                              │                                       │
│                              ▼                                       │
│                    ┌──────────────────┐                             │
│                    │  结果汇总交付     │                             │
│                    └──────────────────┘                             │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 5. 技术选型

### 5.1 核心技术栈

| 层级 | 技术选型 | 说明 |
|------|----------|------|
| **编程语言** | Python 3.11+ | 生态丰富，AI/ML 领域主流 |
| **终端 UI** | Rich (初期) → Textual (后期) | 渐进式升级 |
| **Web 框架** | aiohttp | 异步 HTTP 服务器 |
| **数据库** | SQLite + WAL | 轻量级本地持久化 |
| **数据格式** | JSON / JSONL / YAML | 结构化存储 |
| **包管理** | uv / pip | 现代 Python 包管理 |
| **测试框架** | pytest | 标准测试框架 |

### 5.2 模型 Provider 支持

| Provider | API 格式 | 状态 |
|----------|----------|------|
| DeepSeek | Anthropic-compatible | ✅ 优先支持 |
| OpenAI | Responses API / Chat Completions | ✅ 支持 |
| Anthropic | Messages API | ✅ 支持 |
| 本地模型 (Ollama) | OpenAI-compatible | 🔄 后期支持 |
| 国产模型 (通义/文心) | 各自格式 | 🔄 后期支持 |

---

## 6. 项目结构

```
sdpost-claw/
├── design-docs/                    # 设计文档
│   ├── 00-system-design-overview.md
│   └── modules/
│       ├── 01-agent-core.md
│       ├── 02-desktop-runtime.md
│       ├── 03-memory-context.md
│       ├── 04-extension-system.md
│       └── 05-production-multiagent.md
├── src/                            # 源代码
│   ├── sdpost_claw/
│   │   ├── __init__.py
│   │   ├── main.py                 # 入口
│   │   ├── config.py               # 配置管理
│   │   │
│   │   ├── agent/                  # Module 01: Agent Core
│   │   │   ├── __init__.py
│   │   │   ├── loop.py             # Agent Loop
│   │   │   ├── tools.py            # Tool Registry
│   │   │   ├── deferred.py         # Deferred Loading
│   │   │   └── permissions.py      # Permission Hooks
│   │   │
│   │   ├── runtime/                # Module 02: Desktop Runtime
│   │   │   ├── __init__.py
│   │   │   ├── ui.py               # Terminal UI
│   │   │   ├── server.py           # Sidecar Server
│   │   │   ├── session.py          # Session Management
│   │   │   ├── routing.py          # Model Routing
│   │   │   ├── providers.py        # Model Providers
│   │   │   └── transcript.py       # JSONL Transcript
│   │   │
│   │   ├── memory/                 # Module 03: Memory & Context
│   │   │   ├── __init__.py
│   │   │   ├── workspace.py        # Workspace Memory
│   │   │   ├── user.py             # User Memory
│   │   │   ├── externalize.py      # Output Externalization
│   │   │   ├── compact.py          # Context Compact
│   │   │   └── prompt.py           # Prompt Assembly
│   │   │
│   │   ├── extensions/             # Module 04: Extension System
│   │   │   ├── __init__.py
│   │   │   ├── skills.py           # Skills System
│   │   │   ├── mcp.py              # MCP Connectors
│   │   │   └── experts.py          # Experts System
│   │   │
│   │   ├── production/             # Module 05: Production
│   │   │   ├── __init__.py
│   │   │   ├── database.py         # SQLite Database
│   │   │   ├── scheduler.py        # Automation Scheduler
│   │   │   ├── audit.py            # Audit & Sandbox
│   │   │   └── multiagent.py       # Multi-Agent Collaboration
│   │   │
│   │   └── common/                 # 公共组件
│   │       ├── __init__.py
│   │       ├── events.py           # Event Bus
│   │       ├── storage.py          # Storage Abstraction
│   │       └── utils.py            # 工具函数
│   │
│   └── skills/                     # 内置 Skills
│       ├── data-analysis/
│       ├── ppt-creation/
│       ├── code-review/
│       └── writing/
│
├── tests/                          # 测试
│   ├── unit/
│   ├── integration/
│   └── e2e/
│
├── examples/                       # 示例
│   ├── basic-usage/
│   ├── multi-agent/
│   └── custom-skills/
│
├── docs/                           # 文档
│   ├── user-guide/
│   ├── developer-guide/
│   └── api-reference/
│
├── pyproject.toml                  # 项目配置
├── README.md                       # 项目说明
└── LICENSE                         # 许可证
```

---

## 7. 数据模型总览

### 7.1 核心实体

```
┌─────────────────────────────────────────────────────────────┐
│                      Core Data Entities                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐       ┌──────────────┐                   │
│  │   Session    │ 1───N │   Message    │                   │
│  │              │       │              │                   │
│  │ - id         │       │ - id         │                   │
│  │ - cwd        │       │ - session_id │                   │
│  │ - title      │       │ - role       │                   │
│  │ - status     │       │ - content    │                   │
│  │ - created_at │       │ - tokens     │                   │
│  └──────────────┘       └──────────────┘                   │
│         │                       │                           │
│         │                       │                           │
│         ▼                       ▼                           │
│  ┌──────────────┐       ┌──────────────┐                   │
│  │  ToolCall    │       │  AuditLog    │                   │
│  │              │       │              │                   │
│  │ - id         │       │ - id         │                   │
│  │ - session_id │       │ - session_id │                   │
│  │ - tool_name  │       │ - action     │                   │
│  │ - arguments  │       │ - actor      │                   │
│  │ - result     │       │ - hash       │                   │
│  └──────────────┘       └──────────────┘                   │
│                                                              │
│  ┌──────────────┐       ┌──────────────┐                   │
│  │   Memory     │       │    Skill     │                   │
│  │              │       │              │                   │
│  │ - type       │       │ - name       │                   │
│  │ - content    │       │ - version    │                   │
│  │ - scope      │       │ - manifest   │                   │
│  │ - created_at │       │ - path       │                   │
│  └──────────────┘       └──────────────┘                   │
│                                                              │
│  ┌──────────────┐       ┌──────────────┐                   │
│  │   Expert     │       │ ScheduledTask│                   │
│  │              │       │              │                   │
│  │ - name       │       │ - id         │                   │
│  │ - persona    │       │ - name       │                   │
│  │ - tools      │       │ - cron       │                   │
│  │ - skills     │       │ - payload    │                   │
│  └──────────────┘       └──────────────┘                   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 7.2 存储路径

```
~/.sdpost/                           # 用户级数据
├── config.yaml                      # 全局配置
├── memory/                          # 用户记忆
│   ├── user_profile.json
│   ├── preferences.json
│   └── habits.md
├── skills/                          # 用户安装的技能
├── experts/                         # 自定义专家
└── database.db                     # SQLite 数据库

<workspace>/.sdpost/                 # 工作区级数据
├── memory/                          # 工作区记忆
│   ├── workspace.json
│   ├── daily/
│   ├── decisions/
│   └── facts/
├── transcripts/                     # 会话转录
│   └── <session_id>.jsonl
├── tool-results/                    # 工具结果外部化
│   └── <session_id>/
└── skills/                          # 工作区技能
```

---

## 8. 三大根本矛盾与解决方案

| 根本矛盾 | 直接后果 | 解决方案 | 对应模块 |
|----------|----------|----------|----------|
| **上下文有限 vs 信息无限** | 工具输出、历史、记忆和 schema 会挤爆窗口 | 延迟加载、输出外部化、JSONL、压缩、记忆筛选 | Module 01.3, Module 03 |
| **自主执行 vs 安全可控** | Agent 越有用，越像本地执行系统 | 权限 hooks、沙盒边界、审计 hash chain | Module 01.4, Module 05.3 |
| **模型成本 vs 任务复杂度** | 全部用最强模型成本太高 | lite/default/craft 路由、多 Agent 分工 | Module 02.4, Module 05.4 |

---

## 9. 开发路线图

### Phase 1: MVP (4 周)

| 周 | 目标 | 产出 |
|---|------|------|
| W1 | Agent Loop + 基础工具 | 可运行的单轮对话 |
| W2 | Terminal UI + Sidecar Server | 可交互的终端界面 |
| W3 | Session Management + JSONL | 会话可恢复 |
| W4 | Model Routing (DeepSeek) | 支持真实模型调用 |

### Phase 2: 核心功能 (4 周)

| 周 | 目标 | 产出 |
|---|------|------|
| W5 | Workspace Memory + User Memory | 记忆系统可用 |
| W6 | Output Externalization + Context Compact | 长会话支持 |
| W7 | Prompt Assembly | 动态上下文 |
| W8 | Permission Hooks + Audit Log | 安全治理 |

### Phase 3: 扩展能力 (4 周)

| 周 | 目标 | 产出 |
|---|------|------|
| W9 | Skills System | 技能可加载 |
| W10 | MCP Connectors | 外部工具可接入 |
| W11 | Experts System | 专家模式可切换 |
| W12 | Multi-Agent Collaboration | 多 Agent 可协作 |

### Phase 4: 生产化 (4 周)

| 周 | 目标 | 产出 |
|---|------|------|
| W13 | SQLite Database | 数据可查询 |
| W14 | Automation Scheduler | 定时任务可运行 |
| W15 | 集成测试 + 文档 | 完整可用版本 |
| W16 | 性能优化 + 发布 | v1.0 发布 |

---

## 10. 接口总览

### 10.1 模块间接口矩阵

| 模块 | 依赖 | 被依赖 | 关键接口 |
|------|------|--------|----------|
| Agent Core | Memory, Session, Model | UI, Sidecar | `AgentLoop.run()` |
| Desktop Runtime | Agent Core, Memory | UI, Extensions | `SidecarServer.handle_run()` |
| Memory & Context | Session, Storage | Agent Core, Extensions | `PromptAssembler.assemble()` |
| Extension System | Tool Registry, Memory | Agent Core | `SkillRegistry.search()` |
| Production | Agent Core, Session | 所有模块 | `Supervisor.execute_goal()` |

### 10.2 外部接口

| 接口 | 协议 | 说明 |
|------|------|------|
| HTTP API | HTTP/JSON | Sidecar Server 对外接口 |
| ACP Protocol | JSON-RPC 2.0 | Agent 通信协议 |
| SSE | Server-Sent Events | 实时事件推送 |
| MCP | stdio/SSE/HTTP | 外部工具协议 |

---

## 11. 安全设计

### 11.1 安全层级

```
┌─────────────────────────────────────────────────────────────┐
│                      Security Layers                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Layer 1: 权限控制                                           │
│  - AUTO: 只读操作自动执行                                    │
│  - NOTIFY: 网络请求等执行后通知                              │
│  - CONFIRM: 写操作等需用户确认                               │
│  - DENY: 危险操作禁止执行                                    │
│                                                              │
│  Layer 2: 沙盒边界                                           │
│  - 命令白名单/黑名单                                         │
│  - 路径访问控制                                              │
│  - 网络请求限制                                              │
│                                                              │
│  Layer 3: 审计日志                                           │
│  - 所有操作记录                                              │
│  - 哈希链保证不可篡改                                        │
│  - 支持完整性验证                                            │
│                                                              │
│  Layer 4: 数据保护                                           │
│  - 本地存储优先                                              │
│  - 敏感信息加密                                              │
│  - 用户数据隔离                                              │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 12. 测试策略

### 12.1 测试层级

| 层级 | 范围 | 工具 | 覆盖率目标 |
|------|------|------|------------|
| 单元测试 | 单个模块/函数 | pytest | 80%+ |
| 集成测试 | 模块间协作 | pytest | 70%+ |
| E2E 测试 | 完整用户流程 | pytest + 模拟 | 关键路径 100% |
| 性能测试 | 负载/压力 | pytest-benchmark | 基准测试 |

### 12.2 关键测试场景

- Agent Loop 正确执行工具调用链
- 权限系统正确阻止危险操作
- 记忆系统正确持久化和恢复
- 多 Agent 协作正确完成任务
- 审计日志不可篡改

---

## 13. 部署与分发

### 13.1 分发方式

| 方式 | 说明 |
|------|------|
| PyPI | `pip install sdpost-claw` |
| 源码 | `git clone` + `pip install -e .` |
| 单文件 | PyInstaller 打包 |

### 13.2 系统要求

| 要求 | 说明 |
|------|------|
| Python | 3.11+ |
| OS | macOS / Windows / Linux |
| 内存 | 4GB+ |
| 磁盘 | 500MB+ |
| 网络 | 调用远程模型时需要 |

---

## 14. 总结

sdpost-claw 是一个模块化的全场景 AI 办公智能体系统，通过六层架构和五大模块实现了：

1. **自然语言交互** — 用户用一句话描述需求
2. **自主规划执行** — Agent 自动拆解和完成任务
3. **多模态处理** — 支持文档、代码、数据、设计等多种任务
4. **长期记忆** — 跨会话保持上下文
5. **安全可控** — 分级权限和审计日志
6. **开放扩展** — Skills/MCP/Experts 三层扩展
7. **多 Agent 协作** — 复杂任务并行处理

---

*文档版本: v1.0 | 创建日期: 2026-08-27*
*参考项目: learn-workbuddy, wanman, WorkBuddy, opencode*
