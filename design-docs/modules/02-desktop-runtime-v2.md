# Module 02: Desktop Runtime v2 — 桌面运行时（opencode 增强版）

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

### 2.3 Session Runner

```python
class SessionRunner:
    """
    会话运行器 - 借鉴 opencode 的 SessionRunner

    负责执行一次 Session Drain
    """

    def __init__(
        self,
        session_store: SessionStore,
        system_context: SystemContextRegistry,
        tool_registry: ToolRegistry,
        model_provider: ModelProvider,
        permission_ruleset: PermissionRuleset,
    ):
        self.session_store = session_store
        self.system_context = system_context
        self.tool_registry = tool_registry
        self.model_provider = model_provider
        self.permission_ruleset = permission_ruleset

    async def run(
        self,
        session_id: str,
        force: bool = False,
    ) -> DrainResult:
        """
        执行一次 Session Drain

        Args:
            session_id: 会话 ID
            force: 即使没有符合条件的工作也执行一次模型调用

        Returns:
            DrainResult: 执行结果
        """
        session = await self.session_store.get(session_id)
        if not session:
            raise SessionNotFoundError(session_id)

        # 1. 准备 Safe Provider-Turn Boundary
        boundary = SafeProviderTurnBoundary()
        prepared = await boundary.prepare(session, self.system_context)

        # 2. 检查是否有工作要做
        if not force and not prepared.has_work():
            return DrainResult(status="no_work")

        # 3. 调用模型
        response = await self.model_provider.generate(
            system=prepared.system_context,
            messages=prepared.messages,
            tools=prepared.tools,
        )

        # 4. 处理响应
        if response.has_tool_calls:
            # 执行工具
            results = await self._execute_tools(response.tool_calls, session)
            return DrainResult(
                status="tool_execution",
                tool_calls=response.tool_calls,
                tool_results=results,
            )
        else:
            # 纯文本响应
            return DrainResult(
                status="text_response",
                content=response.text,
            )

    async def _execute_tools(
        self,
        tool_calls: list[ToolCall],
        session: Session,
    ) -> list[ToolResult]:
        """执行工具调用"""
        results = []
        for call in tool_calls:
            # 权限检查
            decision = self.permission_ruleset.evaluate(call.permission)
            if decision.effect == "deny":
                results.append(ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content="Permission denied",
                    is_error=True,
                ))
                continue

            if decision.effect == "ask":
                # 询问用户
                answer = await self._ask_user(call)
                if not answer:
                    results.append(ToolResult(
                        tool_call_id=call.id,
                        name=call.name,
                        content="User declined",
                        is_error=True,
                    ))
                    continue

            # 执行工具
            tool = self.tool_registry.get(call.name)
            if not tool:
                results.append(ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=f"Unknown tool: {call.name}",
                    is_error=True,
                ))
                continue

            result = await tool.execute(call.input, ToolContext(
                session_id=session.id,
                agent=session.agent_id,
                assistant_message_id=call.message_id,
                tool_call_id=call.id,
            ))
            results.append(result)

        return results
```

---

## 3. Session Management v2

### 3.1 会话生命周期

```python
class SessionLifecycle:
    """
    会话生命周期管理 - 借鉴 opencode 的 Session 设计

    状态:
    - ACTIVE: 活跃，可接收输入
    - DRAINING: 正在执行 drain
    - COMPACTING: 正在压缩
    - EPOCH_TRANSITION: 纪元转换中
    - CLOSED: 已关闭
    """

    async def create(
        self,
        cwd: str,
        title: str | None = None,
        agent_mode: str = "build",
    ) -> Session:
        """创建新会话"""
        session = Session(
            id=generate_id(),
            cwd=cwd,
            title=title or "New Session",
            agent_mode=agent_mode,
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(),
            context_epoch=ContextEpoch.initial(),
        )

        # 初始化 Context Epoch
        generation = await self.system_context.initialize()
        session.context_epoch = ContextEpoch(
            id=generate_id(),
            generation=generation,
            started_at=datetime.now(),
        )

        await self.session_store.save(session)
        return session

    async def start_new_epoch(
        self,
        session: Session,
        reason: str,
    ) -> ContextEpoch:
        """
        开始新的 Context Epoch

        触发原因:
        - compaction: 压缩后
        - migration: 会话迁移
        - incompatible: 不兼容的上下文转换
        """
        # 结束当前纪元
        old_epoch = session.context_epoch
        old_epoch.ended_at = datetime.now()
        old_epoch.end_reason = reason

        # 创建新纪元
        generation = await self.system_context.initialize()
        new_epoch = ContextEpoch(
            id=generate_id(),
            generation=generation,
            started_at=datetime.now(),
        )

        session.context_epoch = new_epoch
        await self.session_store.save(session)

        return new_epoch
```

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

## 4. Model Routing v2

### 4.1 Provider-Turn Boundary 集成

```python
class ModelRouter:
    """
    模型路由器 - 增强版，集成 Provider-Turn Boundary

    在每次模型调用前确保:
    1. 上下文已协调
    2. 输入已推进
    3. 工具结果已结算
    """

    def __init__(self):
        self._providers: dict[str, ModelProvider] = {}
        self._tiers: dict[str, str] = {
            "LITE": "gpt-4o-mini",
            "DEFAULT": "gpt-4o",
            "CRAFT": "claude-sonnet-4-20250514",
        }

    def register(self, name: str, provider: ModelProvider):
        """注册模型提供者"""
        self._providers[name] = provider

    async def generate(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolDefinition],
        tier: str = "DEFAULT",
    ) -> ModelResponse:
        """
        生成模型响应

        Args:
            system: 系统上下文（已协调）
            messages: 消息历史（已推进）
            tools: 可用工具
            tier: 模型层级
        """
        model_name = self._tiers.get(tier, tier)
        provider = self._providers.get(model_name)
        if not provider:
            raise ProviderNotFoundError(model_name)

        return await provider.generate(
            system=system,
            messages=[m.to_dict() for m in messages],
            tools=[t.to_dict() for t in tools],
        )
```

### 4.2 模型提供者接口

```python
class ModelProvider(ABC):
    """模型提供者抽象"""

    @abstractmethod
    async def generate(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ModelResponse:
        """生成响应"""
        pass

    @abstractmethod
    async def count_tokens(self, text: str) -> int:
        """计算 token 数"""
        pass

class OpenAIProvider(ModelProvider):
    """OpenAI 兼容提供者"""

    def __init__(self, api_key: str, base_url: str | None = None):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def generate(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ModelResponse:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}] + messages,
            tools=tools,
        )
        return ModelResponse.from_openai(response)

class AnthropicProvider(ModelProvider):
    """Anthropic 提供者"""

    def __init__(self, api_key: str):
        self.client = AsyncAnthropic(api_key=api_key)

    async def generate(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ModelResponse:
        response = await self.client.messages.create(
            model=self.model,
            system=system,
            messages=messages,
            tools=tools,
        )
        return ModelResponse.from_anthropic(response)
```

---

## 5. JSONL Transcript v2

### 5.1 事件溯源设计

```python
class EventType(Enum):
    """事件类型"""
    SESSION_CREATED = "session.created"
    SESSION_EPOCH_CHANGED = "session.epoch_changed"
    PROMPT_ADMITTED = "prompt.admitted"
    TOOL_CALLED = "tool.called"
    TOOL_RESULT_SETTLED = "tool.result_settled"
    MODEL_RESPONSE = "model.response"
    CONTEXT_UPDATED = "context.updated"
    COMPACTION = "compaction.occurred"

@dataclass
class Event:
    """事件"""
    type: EventType
    session_id: str
    timestamp: datetime
    data: dict

class JSONLTranscript:
    """
    JSONL 转录 - 增强版事件溯源

    记录所有状态变更事件，支持:
    - 会话重放
    - 审计追踪
    - 调试分析
    """

    def __init__(self, store: SessionStore):
        self.store = store

    async def record(self, event: Event):
        """记录事件"""
        await self.store.append_message(event.session_id, Message(
            role="event",
            content=json.dumps({
                "type": event.type.value,
                "timestamp": event.timestamp.isoformat(),
                "data": event.data,
            }),
        ))

    async def replay(self, session_id: str) -> list[Event]:
        """重放会话"""
        messages = await self.store.get_messages(session_id)
        events = []
        for msg in messages:
            if msg.role == "event":
                data = json.loads(msg.content)
                events.append(Event(
                    type=EventType(data["type"]),
                    session_id=session_id,
                    timestamp=datetime.fromisoformat(data["timestamp"]),
                    data=data["data"],
                ))
        return events
```

---

## 6. 数据模型

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

@dataclass
class DrainResult:
    """Drain 执行结果"""
    status: str  # "no_work" | "tool_execution" | "text_response"
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
```

---

## 7. 与其他模块的接口

| 接口 | 方向 | 说明 |
|------|------|------|
| `SystemContextRegistry` | 依赖 | 上下文管理 |
| `ToolRegistry` | 依赖 | 工具执行 |
| `ModelProvider` | 依赖 | 模型调用 |
| `PermissionRuleset` | 依赖 | 权限检查 |
| `SessionStore` | 依赖 | 会话持久化 |
| `EventBus` | 输出 | 事件发布 |

---

## 8. 实现计划

| 阶段 | 内容 | 产出 |
|------|------|------|
| Phase 1 | Session Drain 基础 | 单次 drain 执行 |
| Phase 2 | Prompt Promotion | 输入推进机制 |
| Phase 3 | Context Epoch 集成 | 纪元管理 |
| Phase 4 | JSONL 事件溯源 | 完整事件记录 |
| Phase 5 | Model Routing 增强 | 多模型支持 |

---

*文档版本: v2.0 | 创建日期: 2026-08-27 | 基于 opencode 架构优化*
