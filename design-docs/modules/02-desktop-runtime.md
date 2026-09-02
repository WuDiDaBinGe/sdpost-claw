# Module 02: Desktop Runtime — 桌面运行时

## 1. 模块概述

Desktop Runtime 是 sdpost-claw 的运行时基础设施，负责进程管理、会话生命周期、模型路由和事件持久化。本模块参考 learn-workbuddy 的 s05-s09 章节，但用 Terminal UI 替代 Electron，降低初期复杂度。

### 核心理念
> "UI 不直接执行世界动作，Agent 不直接绕过权限，所有状态变更必须可恢复、可回放。"

## 2. 子模块架构

```
Desktop Runtime
├── 2.1 Terminal UI (终端界面)
├── 2.2 Sidecar Server (副驾驶服务器)
├── 2.3 Session Management (会话管理)
├── 2.4 Model Routing (模型路由)
└── 2.5 JSONL Transcript (事件转录)
```

---

## 2.1 Terminal UI（终端界面）

### 2.1.1 设计目标
- 简洁高效的终端交互体验
- 支持流式输出展示
- 任务状态实时更新
- 多会话切换

### 2.1.2 技术选型

| 方案 | 优点 | 缺点 | 选择 |
|------|------|------|------|
| Rich (Python) | 简单、功能丰富 | 交互能力有限 | ✅ 初期方案 |
| Textual (Python) | 完整 TUI 框架 | 学习成本 | 🔄 后期升级 |
| Ink (React+Node) | 现代化 | 需 Node 生态 | ❌ |
| Go Bubble Tea | 高性能 | 需 Go 生态 | ❌ |

### 2.1.3 UI 布局设计

```
┌─────────────────────────────────────────────────────────────┐
│ sdpost-claw v0.1.0                              [会话: xxx] │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                    对话区域                          │   │
│  │                                                     │   │
│  │  👤 用户: 帮我分析这个 Excel 文件的数据              │   │
│  │                                                     │   │
│  │  🤖 AI: 我来帮您分析这个 Excel 文件。               │   │
│  │         首先让我读取文件内容...                      │   │
│  │                                                     │   │
│  │  🔧 [tool_call] read_file("data.xlsx")              │   │
│  │  ✅ [tool_result] 读取成功，共 1000 行数据          │   │
│  │                                                     │   │
│  │  🔧 [tool_call] bash("python analyze.py")           │   │
│  │  ✅ [tool_result] 分析完成                          │   │
│  │                                                     │   │
│  │  🤖 AI: 分析完成！以下是结果：                      │   │
│  │         ...                                         │   │
│  │                                                     │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ 📊 任务状态: 运行中 | 迭代: 3/20 | 模型: deepseek   │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  > 请输入你的任务...                                        │
└─────────────────────────────────────────────────────────────┘
```

### 2.1.4 核心组件

```python
class TerminalUI:
    """终端 UI 主控制器"""

    def __init__(self, config: UIConfig):
        self.config = config
        self.console = Console()
        self.layout = self._build_layout()
        self.event_handlers: dict[str, Callable] = {}

    def _build_layout(self) -> Layout:
        """构建界面布局"""
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main", ratio=1),
            Layout(name="status", size=3),
            Layout(name="input", size=3),
        )
        layout["main"].split_row(
            Layout(name="chat", ratio=3),
            Layout(name="sidebar", ratio=1),
        )
        return layout

    async def stream_message(self, session_id: str, content: str):
        """流式输出消息"""
        # 实时更新对话区域
        pass

    async def show_tool_call(self, tool_name: str, arguments: dict):
        """显示工具调用"""
        pass

    async def show_tool_result(self, result: ToolResult):
        """显示工具结果"""
        pass

    async def request_confirmation(
        self, message: str, options: list[str]
    ) -> str:
        """请求用户确认"""
        pass

    def register_handler(self, event: str, handler: Callable):
        """注册事件处理器"""
        self.event_handlers[event] = handler
```

---

## 2.2 Sidecar Server（副驾驶服务器）

### 2.2.1 设计目标
- 将 Agent 执行与 UI 进程隔离
- 支持远程调用（HTTP/ACP 协议）
- 进程生命周期管理
- 崩溃自动恢复

### 2.2.2 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                    Sidecar Architecture                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐     HTTP/JSON-RPC     ┌──────────────┐   │
│  │  Terminal UI │ ◄──────────────────► │  Sidecar     │   │
│  │  (前端进程)   │                      │  Server      │   │
│  └──────────────┘                      │  (后端进程)   │   │
│                                        └──────┬───────┘   │
│                                               │           │
│                                               ▼           │
│                                        ┌──────────────┐   │
│                                        │  Agent       │   │
│                                        │  Runtime     │   │
│                                        │  (子进程)     │   │
│                                        └──────────────┘   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 2.2.3 接口设计

```python
class SidecarServer:
    """副驾驶服务器 — 提供 HTTP/ACP 接口"""

    def __init__(self, config: ServerConfig):
        self.config = config
        self.app = web.Application()
        self.agent_runtime = AgentRuntime(config)
        self.session_manager = SessionManager(config)
        self._setup_routes()

    def _setup_routes(self):
        """设置路由"""
        self.app.router.add_get("/api/v1/health", self.handle_health)
        self.app.router.add_get("/api/v1/sessions", self.handle_list_sessions)
        self.app.router.add_post("/api/v1/sessions", self.handle_create_session)
        self.app.router.add_post("/api/v1/runs", self.handle_run)
        self.app.router.add_get("/api/v1/sessions/{id}/history", self.handle_get_history)
        self.app.router.add_get("/api/v1/acp/events", self.handle_sse)  # Server-Sent Events
        self.app.router.add_post("/api/v1/acp", self.handle_acp_jsonrpc)

    async def handle_run(self, request: web.Request) -> web.Response:
        """处理任务执行请求"""
        body = await request.json()
        session_id = body.get("session_id")
        prompt = body.get("prompt", "")
        cwd = body.get("cwd", ".")

        # 创建或恢复会话
        session = await self.session_manager.get_or_create(session_id, cwd)

        # 异步执行任务
        task = asyncio.create_task(
            self.agent_runtime.execute(session, prompt)
        )

        return web.json_response({
            "data": {
                "task_id": str(task.get_name()),
                "session_id": session.id,
                "status": "running",
            }
        })

    async def handle_sse(self, request: web.Request) -> web.Response:
        """Server-Sent Events 实时推送"""
        response = web.StreamResponse()
        response.headers["Content-Type"] = "text/event-stream"
        await response.prepare(request)

        queue = asyncio.Queue()
        self.agent_runtime.subscribe(queue)

        try:
            while True:
                event = await queue.get()
                await response.write(
                    f"event: {event.type}\ndata: {json.dumps(event.data)}\n\n".encode()
                )
        except asyncio.CancelledError:
            self.agent_runtime.unsubscribe(queue)

        return response
```

### 2.2.4 ACP 协议（Agent Communication Protocol）

```python
class ACPProtocol:
    """ACP 协议实现 — JSON-RPC 2.0 风格"""

    METHODS = {
        "initialize": "初始化连接",
        "session/new": "创建新会话",
        "session/load": "加载已有会话",
        "session/prompt": "发送任务提示",
        "session/stop": "停止当前任务",
        "session/list": "列出所有会话",
    }

    async def handle_request(self, request: dict) -> dict:
        """处理 ACP 请求"""
        method = request.get("method")
        params = request.get("params", {})
        request_id = request.get("id")

        handler = getattr(self, f"handle_{method.replace('/', '_')}", None)
        if not handler:
            return self._error(request_id, -32601, f"方法不存在: {method}")

        try:
            result = await handler(params)
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as e:
            return self._error(request_id, -32602, str(e))
```

---

## 2.3 Session Management（会话管理）

### 2.3.1 设计目标
- 逻辑会话可恢复
- 运行时必须重建
- 会话隔离与安全
- 会话持久化

### 2.3.2 会话生命周期

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│ Created │───►│ Active  │───►│ Paused  │───►│ Closed  │
└─────────┘    └─────────┘    └─────────┘    └─────────┘
                    │              │
                    │              │
                    ▼              ▼
              ┌─────────┐    ┌─────────┐
              │ Running │    │Resumable│
              └─────────┘    └─────────┘
```

### 2.3.3 核心实现

```python
class SessionManager:
    """会话管理器"""

    def __init__(self, config: SessionConfig):
        self.config = config
        self.storage = SessionStorage(config)
        self.active_sessions: dict[str, Session] = {}

    async def create_session(
        self, cwd: str, title: str = "Untitled"
    ) -> Session:
        """创建新会话"""
        session = Session(
            id=str(uuid.uuid4()),
            cwd=cwd,
            title=title,
            created_at=datetime.now(),
            status=SessionStatus.CREATED,
        )
        await self.storage.save_session(session)
        self.active_sessions[session.id] = session
        return session

    async def get_or_create(
        self, session_id: str = None, cwd: str = "."
    ) -> Session:
        """获取或创建会话"""
        if session_id and session_id in self.active_sessions:
            return self.active_sessions[session_id]

        if session_id:
            # 尝试从存储恢复
            session = await self.storage.load_session(session_id)
            if session:
                self.active_sessions[session_id] = session
                return session

        return await self.create_session(cwd)

    async def pause_session(self, session_id: str):
        """暂停会话"""
        session = self.active_sessions.get(session_id)
        if session:
            session.status = SessionStatus.PAUSED
            await self.storage.save_session(session)

    async def resume_session(self, session_id: str) -> Session:
        """恢复会话"""
        session = await self.storage.load_session(session_id)
        if session:
            session.status = SessionStatus.ACTIVE
            self.active_sessions[session_id] = session
        return session

    async def close_session(self, session_id: str):
        """关闭会话"""
        session = self.active_sessions.pop(session_id, None)
        if session:
            session.status = SessionStatus.CLOSED
            session.closed_at = datetime.now()
            await self.storage.save_session(session)

@dataclass
class Session:
    """会话数据模型"""
    id: str
    cwd: str
    title: str
    created_at: datetime
    status: SessionStatus
    transcript_path: str = None
    metadata: dict = None
    closed_at: datetime = None
```

---

## 2.4 Model Routing（模型路由）

### 2.4.1 设计目标
- 多 Provider 支持（DeepSeek / OpenAI / Anthropic / 本地模型）
- 基于任务复杂度的智能路由
- 成本优化
- 故障转移

### 2.4.2 路由策略

```python
class ModelTier(Enum):
    """模型等级"""
    LITE = "lite"       # 轻量任务：分类、摘要、路由决策
    DEFAULT = "default" # 标准任务：对话、简单工具调用
    CRAFT = "craft"     # 专业任务：复杂推理、代码生成、创意工作

class ModelRouter:
    """模型路由器"""

    def __init__(self, config: RouterConfig):
        self.config = config
        self.providers: dict[str, ModelProvider] = {}
        self.tier_mapping: dict[ModelTier, str] = {}
        self.cost_tracker = CostTracker()

    def register_provider(
        self, name: str, provider: ModelProvider, tier: ModelTier
    ):
        """注册模型提供者"""
        self.providers[name] = provider
        self.tier_mapping[tier] = name

    async def route(self, task: TaskContext) -> ModelProvider:
        """根据任务选择模型"""
        tier = self._classify_task(task)
        provider_name = self.tier_mapping.get(tier, self.tier_mapping[ModelTier.DEFAULT])

        # 检查 Provider 健康状态
        if not await self.providers[provider_name].is_healthy():
            provider_name = self._failover(provider_name)

        return self.providers[provider_name]

    def _classify_task(self, task: TaskContext) -> ModelTier:
        """任务分类"""
        # 基于任务特征分类
        if task.type in ("classify", "summarize", "route"):
            return ModelTier.LITE
        elif task.type in ("code_generate", "complex_reasoning", "creative"):
            return ModelTier.CRAFT
        return ModelTier.DEFAULT

    def _failover(self, failed_provider: str) -> str:
        """故障转移"""
        for name, provider in self.providers.items():
            if name != failed_provider and provider.is_healthy():
                return name
        raise RuntimeError("所有模型 Provider 均不可用")

class ModelProvider(ABC):
    """模型提供者抽象基类"""

    @abstractmethod
    async def generate(
        self,
        system: str,
        messages: list[dict],
        tools: list[ToolSpec],
    ) -> ModelTurn:
        pass

    @abstractmethod
    async def is_healthy(self) -> bool:
        pass

    @abstractmethod
    def format_tool_results(self, results: list[ToolResult]) -> list[dict]:
        pass
```

### 2.4.3 Provider 适配器

```python
class DeepSeekProvider(ModelProvider):
    """DeepSeek 适配器 — Anthropic-compatible API"""

    def __init__(self, api_key: str, model: str = "deepseek-chat"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://api.deepseek.com"

    async def generate(self, system, messages, tools) -> ModelTurn:
        # 转换为 Anthropic 格式
        response = await self._call_api(
            messages=self._convert_messages(messages),
            system=system,
            tools=self._convert_tools(tools),
        )
        return self._parse_response(response)

class OpenAIProvider(ModelProvider):
    """OpenAI 适配器"""

    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self.api_key = api_key
        self.model = model

class AnthropicProvider(ModelProvider):
    """Anthropic 适配器"""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self.api_key = api_key
        self.model = model
```

---

## 2.5 JSONL Transcript（事件转录）

### 2.5.1 设计目标
- 追加写入，崩溃可恢复
- 完整记录会话过程
- 支持回放与审计
- 结构化事件流

### 2.5.2 事件类型定义

```python
class EventType(Enum):
    """事件类型"""
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SYSTEM_EVENT = "system_event"
    ERROR = "error"
    STATE_CHANGE = "state_change"

@dataclass
class TranscriptEvent:
    """转录事件"""
    timestamp: datetime
    session_id: str
    type: EventType
    data: dict
    iteration: int = 0

class JSONLTranscript:
    """JSONL 事件转录器"""

    def __init__(self, base_path: Path):
        self.base_path = base_path

    def transcript_path(self, session_id: str) -> Path:
        """获取会话转录文件路径"""
        return self.base_path / "transcripts" / f"{session_id}.jsonl"

    async def append(self, event: TranscriptEvent):
        """追加事件"""
        path = self.transcript_path(event.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": event.timestamp.isoformat(),
                "session_id": event.session_id,
                "type": event.type.value,
                "iteration": event.iteration,
                "data": event.data,
            }, ensure_ascii=False) + "\n")

    async def read_transcript(self, session_id: str) -> list[TranscriptEvent]:
        """读取完整转录"""
        path = self.transcript_path(session_id)
        if not path.exists():
            return []

        events = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                    events.append(TranscriptEvent(
                        timestamp=datetime.fromisoformat(data["timestamp"]),
                        session_id=data["session_id"],
                        type=EventType(data["type"]),
                        data=data["data"],
                        iteration=data.get("iteration", 0),
                    ))
                except (json.JSONDecodeError, KeyError):
                    continue
        return events

    async def replay(self, session_id: str, handler: Callable):
        """回放会话"""
        events = await self.read_transcript(session_id)
        for event in events:
            await handler(event)
```

---

## 3. 数据模型

```python
@dataclass
class ServerConfig:
    """服务器配置"""
    host: str = "127.0.0.1"
    port: int = 8765
    max_concurrent_tasks: int = 10
    task_timeout: int = 300  # 秒

@dataclass
class SessionConfig:
    """会话配置"""
    max_sessions: int = 100
    session_timeout: int = 3600  # 秒
    auto_save_interval: int = 30  # 秒

@dataclass
class RouterConfig:
    """路由配置"""
    default_tier: ModelTier = ModelTier.DEFAULT
    enable_failover: bool = True
    cost_budget_daily: float = 100.0  # 元/天
```

---

## 4. 与其他模块的接口

| 接口 | 方向 | 说明 |
|------|------|------|
| `AgentLoop` | 调用 | 调用 Agent 执行任务 |
| `MemoryManager` | 依赖 | 会话上下文恢复 |
| `Storage` | 依赖 | 持久化存储 |
| `EventBus` | 输出 | 事件发布 |

---

## 5. 实现计划

| 阶段 | 内容 | 产出 |
|------|------|------|
| Phase 1 | Terminal UI 基础框架 | 可交互的终端界面 |
| Phase 2 | Sidecar Server + ACP 协议 | HTTP API 可用 |
| Phase 3 | Session Management | 会话可创建/恢复/关闭 |
| Phase 4 | Model Routing | 多 Provider 支持 |
| Phase 5 | JSONL Transcript | 事件可回放 |

---

*文档版本: v1.0 | 创建日期: 2026-08-27*
