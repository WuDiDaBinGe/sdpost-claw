# Module 02: Desktop Runtime v2 — 桌面运行时（opencode 增强版）

> **v2.1 实现对齐说明（2026-09-03）**：本节已按实际代码更新。要点变更：
> - 新增 **Desktop GUI**（`desktop/` 包）：DesktopServer + pywebview 原生窗口 + Web 前端（见 §6）
> - 模型 Provider 实际只有 `OpenAIProvider`（OpenAI 兼容协议接入国产模型，"央企国产-only" 约束），AnthropicProvider 未实现
> - 模型路由三档 LITE/DEFAULT/CRAFT 实际映射 `deepseek-chat` / `deepseek-v3` / `qwen-max`
> - `SessionRunner.run()` 的实际签名接收 `Session` 对象（非 session_id）
> - SessionStore 实际目录为 `sessions/`（元数据 JSON）+ `messages/`（消息 JSONL）；事件日志另由 harness `SessionLog` 持久化

## 1. 设计演进说明

本模块在初版设计基础上，吸收了 opencode 的以下优秀设计：

| 初版设计 | opencode 启发 | 改进 |
|----------|--------------|------|
| 简单 Session 管理 | Session Drain + Prompt Promotion | 更清晰的执行边界和输入推进 |
| 基础 Model Routing | Provider-Turn Boundary | 安全的模型调用边界 |
| 简单 JSONL 存储 | Context Snapshot + Epoch | 上下文版本管理 |

---

## 2. Session Drain 模型（核心改进）

### 2.1 设计理念（借鉴 opencode CONTEXT.md）

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Session Drain Architecture                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│   Session Drain = 进程本地的执行协调，不是持久化实体                   │
│                                                                      │
│   关键概念:                                                          │
│   - 推进符合条件的持久化工作                                          │
│   - 没有持久化的身份或转录边界                                        │
│   - 每次 drain 是一次 Provider Turn                                  │
│                                                                      │
│   ┌──────────────────────────────────────────────────────────────┐  │
│   │                    Session (持久化实体)                        │  │
│   │                                                              │  │
│   │   ┌─────────────────────────────────────────────────────┐   │  │
│   │   │              Pending Input (待处理输入)               │   │  │
│   │   │   - 用户输入等待推进                                  │   │  │
│   │   │   - 在 Safe Provider-Turn Boundary 推进              │   │  │
│   │   └─────────────────────────────────────────────────────┘   │  │
│   │                            ↓                                  │  │
│   │   ┌─────────────────────────────────────────────────────┐   │  │
│   │   │              Session History (会话历史)               │   │  │
│   │   │   - 已推进的用户输入                                  │   │  │
│   │   │   - 已结算的工具结果                                  │   │  │
│   │   │   - Mid-Conversation System Messages                 │   │  │
│   │   └─────────────────────────────────────────────────────┘   │  │
│   │                            ↓                                  │  │
│   │   ┌─────────────────────────────────────────────────────┐   │  │
│   │   │              Context Epoch (上下文纪元)               │   │  │
│   │   │   - 不可变的 Baseline System Context                 │   │  │
│   │   │   - 可比较的 Context Snapshot                        │   │  │
│   │   │   - 在压缩/迁移时结束                                │   │  │
│   │   └─────────────────────────────────────────────────────┘   │  │
│   │                                                              │  │
│   └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Prompt Promotion（输入推进）

```python
class PromptPromotion:
    """
    输入推进 - 借鉴 opencode 的 Prompt Promotion 设计

    将符合条件的待处理输入原子性地推进到 Session History
    """

    async def promote(
        self,
        session: Session,
        boundary: SafeProviderTurnBoundary,
    ) -> list[Prompt]:
        """
        推进符合条件的用户输入

        条件:
        1. 输入已完全接收（非流式中间状态）
        2. 没有未完成的工具调用
        3. 在 Safe Provider-Turn Boundary 内
        """
        pending = await session.get_pending_input()

        # 过滤：只推进完全接收的输入
        eligible = [p for p in pending if p.is_complete]

        if not eligible:
            return []

        # 原子性推进
        async with session.lock():
            # 再次检查条件（双重检查锁定）
            if await session.has_pending_tool_calls():
                return []

            # 推进到 Session History
            for prompt in eligible:
                await session.admit_to_history(prompt)
                await session.remove_from_pending(prompt)

            return eligible
```

### 2.3 Session Runner（实际实现见 Module 01 §2.4）

`SessionRunner` 的权威实现在 [agent/drain.py](file:///f:/MyCoding/sdpost-claw/src/sdpost_claw/agent/drain.py)，完整描述见 Module 01 §2.4。本模块关注其与 Runtime 层的集成：

```python
class SessionRunner:
    """执行一次 Session Drain（实际签名）"""

    def __init__(self, tool_registry, permission_ruleset,
                 model_provider=None, event_bus=None): ...
    # 构造后注入协调器（Phase 4/5，保持 run 签名不变）：
    #   system_context / compaction_bridge / summary_source

    async def run(
        self,
        session: Session,        # Session 对象（非 session_id）
        system_context: str,
        force: bool = False,
        on_delta: Any = None,    # 流式回调 (kind, chunk)：kind ∈ "text" | "reasoning"
    ) -> DrainResult:
        """每步内部完成：reconcile（Phase 4）→ 边界 prepare →
        压缩压力检查（Phase 5）→ 模型调用（流式/一次性）→
        工具执行（三阶段管道）或文本响应落历史"""
```

所有客户端入口（终端 `run/exec`、Sidecar `serve`、桌面 `desktop`）都经由同一个 `SessionRunner.run()` 漏斗，reconcile 与压缩对所有入口一致生效。`DrainResult.status`：`no_work | tool_execution | text_response | error`。

---

## 3. Session Management v2

### 3.1 会话生命周期（实际实现于 runtime/session.py）

实际实现为三个协作类（[runtime/session.py](file:///f:/MyCoding/sdpost-claw/src/sdpost_claw/runtime/session.py)）：

```python
class SessionLifecycle:
    """会话生命周期状态机"""
    # create(cwd, title, agent_mode) / get / update / close / list_all / delete
    # 会话以 dict 形式持久化为 sessions/<id>.json，重启后可 resume

class SessionStore:
    """文件存储（见 §3.2）"""

class SessionManager:
    """运行时操作桥梁（SessionStore + SessionLifecycle 之上）"""
    # create_session(cwd, title, agent_mode) -> Session（agent/drain.py 实体）
    # get_session(session_id) -> Session | None（恢复 history）
    # submit_prompt(session_id, text) -> Prompt（入 Inbox next_turn）
    # add_assistant_message / add_assistant_tool_calls / add_tool_message
    # persist_log(session)  # 将 SessionLog 增量落盘到事件日志 JSONL
```

> **Context Epoch 集成现状**：`ContextEpoch` 类型已实现（context/epoch.py），但会话创建路径尚未持久化 epoch 记录；压缩摘要改经 Inbox 注入路径呈现（见 Module 03 §4），`start_new_epoch` 的持久化触发留待后续阶段。

### 3.2 会话存储

```python
class SessionStore:
    """
    会话存储 - 借鉴 opencode 的持久化设计

    存储结构:
    - sessions/: 会话元数据
    - messages/: 消息历史 (JSONL)
    - snapshots/: 上下文快照
    - tool_outputs/: 工具输出（外部化）
    """

    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.sessions_path = base_path / "sessions"
        self.messages_path = base_path / "messages"
        self.snapshots_path = base_path / "snapshots"
        self.tool_outputs_path = base_path / "tool_outputs"

    async def save(self, session: Session):
        """保存会话元数据"""
        path = self.sessions_path / f"{session.id}.json"
        path.write_text(json.dumps(session.to_dict(), indent=2))

    async def append_message(self, session_id: str, message: Message):
        """追加消息到 JSONL"""
        path = self.messages_path / f"{session_id}.jsonl"
        async with aiofiles.open(path, "a") as f:
            await f.write(json.dumps(message.to_dict()) + "\n")

    async def save_snapshot(self, session_id: str, snapshot: Snapshot):
        """保存上下文快照"""
        path = self.snapshots_path / f"{session_id}.json"
        path.write_text(json.dumps(snapshot.entries, indent=2))

    async def save_tool_output(
        self,
        session_id: str,
        tool_call_id: str,
        output: str,
    ) -> Path:
        """保存工具输出（外部化）"""
        path = self.tool_outputs_path / session_id / f"{tool_call_id}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output, encoding="utf-8")
        return path
```

---

## 4. Model Routing v2（实际实现于 runtime/routing.py + runtime/providers.py）

### 4.1 Provider 接口与国产模型适配

实际只有 `OpenAIProvider` 一种实现：所有国产大模型（DeepSeek、通义千问、智谱GLM、月之暗面、豆包、百川、MiniMax、阶跃星辰）均兼容 OpenAI 协议，统一走此实现（"央企国产-only" 约束）。`AnthropicProvider` 未实现。

```python
class ModelProvider(ABC):
    """模型提供者抽象（实际签名）"""

    @abstractmethod
    async def generate(self, system: str, messages: list[dict],
                       tools: list[dict] | None = None) -> ModelResponse: ...

    async def generate_stream(self, system: str, messages: list[dict],
                              tools: list[dict] | None = None,
                              on_delta: Any = None) -> ModelResponse:
        """流式生成。on_delta(kind, chunk)：kind ∈ "text" | "reasoning"。
        默认退化为一次性 generate + 单个 delta。"""

    @abstractmethod
    async def count_tokens(self, text: str) -> int: ...


class OpenAIProvider(ModelProvider):
    """OpenAI 兼容协议 Provider —— 适配所有国产大模型 API"""

    async def generate_stream(self, system, messages, tools=None, on_delta=None):
        # 解析 delta.content（text）与 delta.reasoning_content
        # （DeepSeek-R1/GLM thinking 风格）增量输出
        # stream_options={"include_usage": True} 收集 usage
        # 流式被拒时降级为一次性 generate()
        ...


def create_provider(provider_name, api_key, model=None, base_url=None) -> ModelProvider:
    """工厂函数：统一返回 OpenAIProvider。

    - 缺少 base_url 时从厂商名映射默认地址
      （deepseek → https://api.deepseek.com、阿里云 → dashscope compatible-mode 等）
    - 未指定模型名时按厂商推荐默认值（deepseek→deepseek-chat、阿里云→qwen-max、智谱→glm-4-plus…）
    - api_key 为空时走环境变量 OPENAI_API_KEY（本地端点如 Ollama 免 Key）
    """
```

### 4.2 分层路由 ModelRouter

```python
class ModelRouter:
    """分层模型选择（runtime/routing.py，实际映射）"""

    def __init__(self):
        self._tiers: dict[str, str] = {"LITE": "deepseek", "DEFAULT": "deepseek", "CRAFT": "阿里云"}
        self._tier_models: dict[str, str] = {
            "LITE": "deepseek-chat",
            "DEFAULT": "deepseek-v3",
            "CRAFT": "qwen-max",
        }

    def set_tier(self, tier, provider_name, model=None): ...
    async def generate(self, system, messages, tools=None, tier="DEFAULT"): ...
    def select_tier_for_task(self, task_complexity: str = "normal") -> str:
        # simple → LITE / normal → DEFAULT / complex → CRAFT
```

> **现状**：桌面端主链路（DesktopServer）当前直接使用 `create_provider` 创建的单 Provider（config.model 指定），`ModelRouter` 的三档路由能力已实现但尚未接入会话执行链路。

---

## 5. JSONL Transcript v2（实际实现于 runtime/transcript.py + harness/session_log.py）

### 5.1 事件类型（实际 EventType 枚举）

```python
class EventType(Enum):
    SESSION_CREATED = "session.created"
    SESSION_RESUMED = "session.resumed"
    SESSION_CLOSED = "session.closed"
    PROMPT_SUBMITTED = "prompt.submitted"
    MODEL_REQUEST = "model.request"
    MODEL_RESPONSE = "model.response"
    TOOL_CALLED = "tool.called"
    TOOL_RESULT = "tool.result"
    CONTEXT_UPDATED = "context.updated"
    COMPACTION = "compaction.occurred"
    ERROR = "error.occurred"
```

### 5.2 JSONLTranscript（事件溯源）

```python
class JSONLTranscript:
    """按会话写 transcripts/<session_id>.jsonl，支持 replay / 按类型过滤 / 清除"""

    def __init__(self, base_path: Path): ...
    async def record(self, event: TranscriptEvent) -> None: ...
    async def record_simple(self, event_type, session_id, data=None) -> None: ...
    async def replay(self, session_id: str) -> list[TranscriptEvent]: ...
    async def get_events_by_type(self, session_id, event_type) -> list[TranscriptEvent]: ...
```

### 5.3 双层事件日志现状

| 层 | 位置 | 角色 |
|----|------|------|
| harness `SessionLog` | harness/session_log.py | 会话内存中的 append-only 事件日志，是 `derive_messages()` 的权威数据源；`SessionManager.persist_log(session)` 将其增量落盘到 `messages/<session_id>.jsonl` |
| runtime `JSONLTranscript` | runtime/transcript.py | 独立的事件溯源记录器（写 `transcripts/`），供审计/调试，尚未在桌面主链路默认启用 |

---

## 6. Desktop GUI（实际实现于 desktop/ 包）

### 6.1 组件构成

| 组件 | 文件 | 说明 |
|------|------|------|
| `DesktopApp` | desktop/app.py | pywebview 原生窗口包装器；`start_with_server()` 在后台线程起 SidecarServer 后打开指向 `http://127.0.0.1:8765/` 的原生窗口 |
| `DesktopServer` | desktop/server.py | 集成应用服务器：aiohttp + 静态 Web UI 托管 + 全量 REST/SSE API |
| Web 前端 | desktop/web/ | 原生 HTML/CSS/JS 聊天界面（SSE 消费端） |

### 6.2 组装流程（DesktopServer.setup()）

```python
# 1. 注册 Context Sources（Date / ProjectInstructions / AgentInfo / SummaryContextSource）
# 2. 注册内置工具 BuiltInTools.register_all(tool_registry, cwd)
# 3. 注册技能源（bundled skills 目录 + config.skill_dirs）
# 4. create_provider(...) 创建模型 Provider（缺 base_url/api_key 时从 DEFAULT_MODELS 条目回填）
# 5. SessionRunner(tool_registry, _ruleset_for_mode(default_mode), model_provider)
# 6. CompactionEngine(CompactionConfig()) + CompactionBridge(engine, provider)
# 7. 注入协调器（Phase 4/5）：
#    session_runner.system_context    = self.system_context
#    session_runner.compaction_bridge = self._compaction_bridge
#    session_runner.summary_source    = self._summary_source
```

### 6.3 HTTP API 一览

| 路由 | 方法 | 说明 |
|------|------|------|
| `/` `/static/{path}` | GET | Web UI 与静态资源 |
| `/api/health` | GET | 健康检查（model_configured / version） |
| `/api/sessions` | GET / POST | 会话列表 / 创建 |
| `/api/sessions/{id}` | GET / DELETE | 会话详情（含 history）/ 删除 |
| `/api/sessions/{id}/prompt` | POST | 提交 Prompt（后台起 `_process_prompt` 任务） |
| `/api/sessions/{id}/stream` | GET | SSE 事件流（带 per-session 缓冲重放，支持晚连接） |
| `/api/fs/browse` | GET | 本地目录浏览（工作区选择器，Windows 盘符根） |
| `/api/skills` `/api/experts` `/api/connectors` `/api/spaces` `/api/automations` `/api/library` | GET | 侧边栏 / 扩展数据（automations、library 为占位） |
| `/api/config` | GET / POST | 配置读写（model / theme / compaction / skill_dirs 等；保存后刷新 SessionRunner.model_provider） |
| `/api/models[...]` | GET / POST / PUT | 模型条目管理（增删改查、批量删除默认模型用 disabled override 隐藏） |
| `/api/models/test` | POST | 直连 `/chat/completions` 测试连通性（本地端点免 Key） |

### 6.4 处理循环与 SSE 事件

`_process_prompt()`：`submit_prompt` → 后台任务中最多 20 次迭代调用 `session_runner.run(force=True, on_delta=...)`，按 `DrainResult.status` 分派（`tool_execution` 持续迭代并持久化 assistant tool_calls / tool 消息；`text_response` / `no_work` / `error` 结束）。收尾发布 `turn_stats`（duration/iterations/tool_calls/reasoning_chars）与 `done` 事件，并经 `_maybe_generate_title()` 自动生成会话标题（模型小调用，回退截取用户输入）。

SSE 事件类型：`user | delta(kind: text/reasoning) | message | tool | turn_stats | title | done | connected`。

---

## 7. 数据模型

```python
@dataclass
class Session:
    """会话"""
    id: str
    cwd: str
    title: str
    agent_mode: str
    status: SessionStatus
    created_at: datetime
    updated_at: datetime = field(default_factory=datetime.now)
    context_epoch: ContextEpoch | None = None

@dataclass
class ContextEpoch:
    """上下文纪元"""
    id: str
    generation: Generation
    started_at: datetime
    ended_at: datetime | None = None
    end_reason: str | None = None

@dataclass
class Prompt:
    """用户输入"""
    id: str
    text: str
    is_complete: bool = True
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class ToolCall:
    """工具调用"""
    id: str
    name: str
    input: dict
    message_id: str
    permission: str | None = None

@dataclass
class ToolResult:
    """工具结果"""
    tool_call_id: str
    name: str
    content: str
    structured_output: Any | None = None
    externalized_path: Path | None = None
    is_truncated: bool = False
    is_error: bool = False

@dataclass
class ModelResponse:
    """模型响应"""
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    has_tool_calls: bool = False
    usage: dict | None = None
    reasoning: str = ""   # DeepSeek-R1 / GLM thinking 风格的推理内容

@dataclass
class DrainResult:
    """Drain 执行结果"""
    status: str  # "no_work" | "tool_execution" | "text_response" | "error"
    content: str | None = None
    error: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
```

---

## 8. 与其他模块的接口

| 接口 | 方向 | 说明 |
|------|------|------|
| `SystemContextRegistry` | 依赖 | 上下文管理 |
| `ToolRegistry` | 依赖 | 工具执行 |
| `ModelProvider` | 依赖 | 模型调用 |
| `PermissionRuleset` | 依赖 | 权限检查 |
| `SessionStore` | 依赖 | 会话持久化 |
| `EventBus` | 输出 | 事件发布 |

---

## 9. 实现计划

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 | Session Drain 基础 | ✅ 已完成（agent/drain.py + harness/driver.py） |
| Phase 2 | Prompt Promotion + Inbox 双队列 | ✅ 已完成（agent/drain.py + harness/inbox.py） |
| Phase 3 | Context Epoch 集成 | ⚠️ 类型已实现，epoch 持久化触发待接入 |
| Phase 4 | reconcile + Inbox 注入路径 | ✅ 已完成（SessionRunner 内嵌，Phase 4） |
| Phase 5 | Compaction Bridge + Summary 注入 | ✅ 已完成（Phase 5，surface op replace 留后续） |
| Phase 6 | Desktop GUI | ✅ 已完成（desktop/ 包） |

---

*文档版本: v2.1 | 创建日期: 2026-08-27 | 最近更新: 2026-09-03 | 基于 opencode 架构优化*
