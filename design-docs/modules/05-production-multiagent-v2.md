# Module 05: Production & Multi-Agent v2 — 生产级与多智能体（opencode 增强版）

## 1. 设计演进说明

本模块在初版设计基础上，吸收了 opencode 的以下优秀设计：

| 初版设计 | opencode 启发 | 改进 |
|----------|--------------|------|
| 简单审计日志 | Event Sourcing + Context Snapshot | 完整事件溯源 |
| 基础调度 | Session Drain 集成 | 更清晰的执行模型 |
| 简单多 Agent | Agent Modes + Permission Ruleset | 更灵活的 Agent 管理 |

---

## 2. Event Sourcing & Audit Log v2

### 2.1 设计理念（借鉴 opencode 的事件溯源）

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Event Sourcing Architecture                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                    Event (事件)                              │   │
│   │                                                             │   │
│   │   - type: 事件类型                                          │   │
│   │   - session_id: 会话 ID                                     │   │
│   │   - timestamp: 时间戳                                       │   │
│   │   - data: 事件数据                                          │   │
│   │   - context_snapshot: 上下文快照（可选）                     │   │
│   │   - hash_chain: 哈希链                                      │   │
│   │                                                             │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                    Event Store (事件存储)                    │   │
│   │                                                             │   │
│   │   - JSONL 追加写入                                          │   │
│   │   - 支持按会话重放                                          │   │
│   │   - 支持按类型过滤                                          │   │
│   │   - 支持时间范围查询                                        │   │
│   │                                                             │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                    Audit Log (审计日志)                      │   │
│   │                                                             │   │
│   │   - 操作审计: 谁做了什么                                     │   │
│   │   - 安全审计: 权限检查记录                                   │   │
│   │   - 数据审计: 上下文变更记录                                 │   │
│   │                                                             │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 事件模型

```python
class EventType(Enum):
    """事件类型"""
    # 会话事件
    SESSION_CREATED = "session.created"
    SESSION_EPOCH_CHANGED = "session.epoch_changed"
    SESSION_CLOSED = "session.closed"

    # 输入事件
    PROMPT_SUBMITTED = "prompt.submitted"
    PROMPT_ADMITTED = "prompt.admitted"

    # 工具事件
    TOOL_CALLED = "tool.called"
    TOOL_RESULT_SETTLED = "tool.result_settled"
    TOOL_OUTPUT_EXTERNALIZED = "tool.output_externalized"

    # 模型事件
    MODEL_REQUEST = "model.request"
    MODEL_RESPONSE = "model.response"
    MODEL_ERROR = "model.error"

    # 上下文事件
    CONTEXT_INITIALIZED = "context.initialized"
    CONTEXT_UPDATED = "context.updated"
    CONTEXT_REPLACED = "context.replaced"
    CONTEXT_UNAVAILABLE = "context.unavailable"

    # 压缩事件
    COMPACTION_TRIGGERED = "compaction.triggered"
    COMPACTION_COMPLETED = "compaction.completed"

    # 权限事件
    PERMISSION_CHECKED = "permission.checked"
    PERMISSION_DENIED = "permission.denied"
    PERMISSION_ASKED = "permission.asked"

    # Agent 事件
    AGENT_CREATED = "agent.created"
    AGENT_MODE_CHANGED = "agent.mode_changed"
    SUB_AGENT_SPAWNED = "agent.sub_spawned"

@dataclass
class Event:
    """事件"""
    type: EventType
    session_id: str
    timestamp: datetime
    data: dict
    context_snapshot: dict | None = None
    hash_chain: str | None = None

    def compute_hash(self, previous_hash: str | None = None) -> str:
        """计算哈希链"""
        content = json.dumps({
            "type": self.type.value,
            "session_id": self.session_id,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
            "previous_hash": previous_hash,
        }, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()

class EventStore:
    """
    事件存储 - 借鉴 opencode 的 JSONL 持久化

    特性:
    - 追加写入
    - 哈希链完整性
    - 支持重放
    """

    def __init__(self, base_path: Path):
        self.base_path = base_path
        self._last_hashes: dict[str, str] = {}

    async def append(self, event: Event):
        """追加事件"""
        # 计算哈希链
        session_hash = self._last_hashes.get(event.session_id)
        event.hash_chain = event.compute_hash(session_hash)
        self._last_hashes[event.session_id] = event.hash_chain

        # 写入 JSONL
        path = self.base_path / f"{event.session_id}.jsonl"
        async with aiofiles.open(path, "a") as f:
            await f.write(json.dumps({
                "type": event.type.value,
                "session_id": event.session_id,
                "timestamp": event.timestamp.isoformat(),
                "data": event.data,
                "context_snapshot": event.context_snapshot,
                "hash_chain": event.hash_chain,
            }) + "\n")

    async def replay(self, session_id: str) -> list[Event]:
        """重放会话事件"""
        path = self.base_path / f"{session_id}.jsonl"
        if not path.exists():
            return []

        events = []
        async with aiofiles.open(path, "r") as f:
            async for line in f:
                data = json.loads(line)
                events.append(Event(
                    type=EventType(data["type"]),
                    session_id=data["session_id"],
                    timestamp=datetime.fromisoformat(data["timestamp"]),
                    data=data["data"],
                    context_snapshot=data.get("context_snapshot"),
                    hash_chain=data.get("hash_chain"),
                ))
        return events

    async def verify_integrity(self, session_id: str) -> bool:
        """验证哈希链完整性"""
        events = await self.replay(session_id)
        previous_hash = None
        for event in events:
            expected_hash = event.compute_hash(previous_hash)
            if expected_hash != event.hash_chain:
                return False
            previous_hash = event.hash_chain
        return True
```

### 2.3 审计日志

```python
class AuditLog:
    """
    审计日志 - 增强版

    记录所有安全相关操作
    """

    def __init__(self, db: Database, event_store: EventStore):
        self.db = db
        self.event_store = event_store

    async def log_permission_check(
        self,
        session_id: str,
        action: str,
        decision: PermissionDecision,
        context: dict | None = None,
    ):
        """记录权限检查"""
        event = Event(
            type=EventType.PERMISSION_CHECKED,
            session_id=session_id,
            timestamp=datetime.now(),
            data={
                "action": action,
                "effect": decision.effect,
                "rule": decision.rule.action if decision.rule else None,
                "context": context,
            },
        )
        await self.event_store.append(event)

        # 同时写入数据库
        await self.db.execute(
            "INSERT INTO audit_log (session_id, action, effect, rule, context, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, action, decision.effect, decision.rule.action if decision.rule else None, json.dumps(context) if context else None, event.timestamp),
        )

    async def log_context_change(
        self,
        session_id: str,
        change_type: str,
        source_key: str,
        details: dict | None = None,
    ):
        """记录上下文变更"""
        event = Event(
            type=EventType.CONTEXT_UPDATED if change_type == "update" else EventType.CONTEXT_REPLACED,
            session_id=session_id,
            timestamp=datetime.now(),
            data={
                "change_type": change_type,
                "source_key": source_key,
                "details": details,
            },
        )
        await self.event_store.append(event)

    async def get_audit_trail(
        self,
        session_id: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[dict]:
        """获取审计轨迹"""
        query = "SELECT * FROM audit_log WHERE session_id = ?"
        params: list = [session_id]

        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time)
        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time)

        query += " ORDER BY timestamp"
        return await self.db.fetch_all(query, params)
```

---

## 3. Automation Scheduler v2

### 3.1 设计理念

```python
class AutomationScheduler:
    """
    自动化调度器 - 增强版

    集成 Session Drain 模型:
    - 调度任务触发 Session Drain
    - 支持 cron 表达式
    - 支持事件触发
    """

    def __init__(
        self,
        session_runner: SessionRunner,
        event_store: EventStore,
    ):
        self.session_runner = session_runner
        self.event_store = event_store
        self._tasks: dict[str, ScheduledTask] = {}
        self._running = False

    async def start(self):
        """启动调度器"""
        self._running = True
        while self._running:
            now = datetime.now()
            for task in self._tasks.values():
                if task.should_run(now):
                    await self._execute_task(task)
            await asyncio.sleep(60)  # 每分钟检查一次

    async def stop(self):
        """停止调度器"""
        self._running = False

    async def add_task(self, task: ScheduledTask):
        """添加调度任务"""
        self._tasks[task.id] = task

    async def remove_task(self, task_id: str):
        """移除调度任务"""
        self._tasks.pop(task_id, None)

    async def _execute_task(self, task: ScheduledTask):
        """执行调度任务"""
        # 创建新会话或复用现有会话
        session = await self.session_store.get(task.session_id)
        if not session:
            session = await self.session_lifecycle.create(
                cwd=task.cwd,
                title=f"Scheduled: {task.name}",
            )

        # 提交任务提示
        await session.submit_prompt(task.prompt)

        # 触发 Session Drain
        result = await self.session_runner.run(session.id, force=True)

        # 记录执行
        await self.event_store.append(Event(
            type=EventType.SESSION_CREATED,
            session_id=session.id,
            timestamp=datetime.now(),
            data={
                "task_id": task.id,
                "task_name": task.name,
                "result_status": result.status,
            },
        ))

@dataclass
class ScheduledTask:
    """调度任务"""
    id: str
    name: str
    session_id: str
    cwd: str
    prompt: str
    cron: str  # cron 表达式
    enabled: bool = True
    last_run: datetime | None = None
    next_run: datetime | None = None

    def should_run(self, now: datetime) -> bool:
        """检查是否应该执行"""
        if not self.enabled:
            return False
        if self.next_run is None:
            return True
        return now >= self.next_run
```

---

## 4. Multi-Agent Collaboration v2

### 4.1 设计理念（借鉴 opencode 的 Agent 模式）

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Multi-Agent Architecture                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                    Supervisor (监督者)                       │   │
│   │                                                             │   │
│   │   - 任务分解                                                │   │
│   │   - Agent 分配                                              │   │
│   │   - 结果汇总                                                │   │
│   │                                                             │   │
│   │   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐       │   │
│   │   │ Agent BUILD │  │ Agent PLAN  │  │ Agent GEN   │       │   │
│   │   │ (完全访问)  │  │ (只读)      │  │ (子任务)    │       │   │
│   │   └─────────────┘  └─────────────┘  └─────────────┘       │   │
│   │                                                             │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                    Message Bus (消息总线)                    │   │
│   │                                                             │   │
│   │   - steer: 中断消息（高优先级）                              │   │
│   │   - followUp: 普通消息                                      │   │
│   │   - result: 结果消息                                        │   │
│   │                                                             │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 4.2 多 Agent 实现

```python
class Supervisor:
    """
    监督者 - 借鉴 opencode 的 Agent 模式

    负责:
    - 任务分解和分配
    - 子 Agent 管理
    - 结果汇总
    """

    def __init__(
        self,
        agent_registry: AgentRegistry,
        session_runner: SessionRunner,
        message_bus: MessageBus,
    ):
        self.agent_registry = agent_registry
        self.session_runner = session_runner
        self.message_bus = message_bus

    async def execute_task(
        self,
        task: str,
        context: dict | None = None,
    ) -> TaskResult:
        """
        执行复杂任务

        1. 使用 PLAN Agent 分析任务
        2. 分解为子任务
        3. 分配 BUILD Agent 执行
        4. 汇总结果
        """
        # 1. 创建 PLAN Agent 分析任务
        plan_agent = self.agent_registry.create_sub_agent(
            parent=self._get_current_agent(),
            name="planner",
            mode=AgentMode.PLAN,
        )

        plan_result = await self._run_agent(
            agent=plan_agent,
            prompt=f"Analyze the following task and break it down into subtasks:\n\n{task}",
        )

        # 2. 解析子任务
        subtasks = self._parse_subtasks(plan_result)

        # 3. 并行执行子任务
        results = await asyncio.gather(*[
            self._execute_subtask(subtask) for subt in subtasks
        ])

        # 4. 汇总结果
        return self._aggregate_results(results)

    async def _execute_subtask(self, subtask: SubTask) -> SubTaskResult:
        """执行子任务"""
        # 创建 BUILD Agent
        build_agent = self.agent_registry.create_sub_agent(
            parent=self._get_current_agent(),
            name=f"worker-{subtask.id}",
            mode=AgentMode.BUILD,
        )

        result = await self._run_agent(
            agent=build_agent,
            prompt=subtask.prompt,
        )

        return SubTaskResult(
            subtask_id=subtask.id,
            result=result,
        )

    async def _run_agent(
        self,
        agent: Agent,
        prompt: str,
    ) -> str:
        """运行 Agent"""
        # 创建会话
        session = await self.session_lifecycle.create(
            cwd=os.getcwd(),
            title=f"Agent: {agent.name}",
            agent_mode=agent.mode.value,
        )

        # 提交提示
        await session.submit_prompt(prompt)

        # 执行 Drain
        result = await self.session_runner.run(session.id, force=True)

        return result.content or ""

class MessageBus:
    """
    消息总线 - 借鉴 wanman 的消息优先级

    消息优先级:
    - steer: 中断消息（高优先级）
    - followUp: 普通消息
    - result: 结果消息
    """

    def __init__(self):
        self._queues: dict[str, asyncio.PriorityQueue] = {}

    async def send(
        self,
        agent_id: str,
        message: Message,
        priority: int = 0,
    ):
        """发送消息"""
        if agent_id not in self._queues:
            self._queues[agent_id] = asyncio.PriorityQueue()

        await self._queues[agent_id].put((priority, message))

    async def recv(self, agent_id: str) -> Message:
        """接收消息"""
        if agent_id not in self._queues:
            self._queues[agent_id] = asyncio.PriorityQueue()

        _, message = await self._queues[agent_id].get()
        return message

    async def steer(self, agent_id: str, message: Message):
        """发送中断消息（最高优先级）"""
        await self.send(agent_id, message, priority=0)

    async def follow_up(self, agent_id: str, message: Message):
        """发送普通消息"""
        await self.send(agent_id, message, priority=10)

    async def send_result(self, agent_id: str, message: Message):
        """发送结果消息"""
        await self.send(agent_id, message, priority=5)
```

---

## 5. SQLite Database v2

### 5.1 数据库 Schema（增强版）

```sql
-- 会话表
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    cwd TEXT NOT NULL,
    title TEXT,
    agent_mode TEXT DEFAULT 'build',
    status TEXT DEFAULT 'active',
    context_epoch_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 上下文纪元表（新增）
CREATE TABLE context_epochs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    baseline TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    end_reason TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- 消息表
CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    metadata TEXT,
    tokens INTEGER DEFAULT 0,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- 工具调用表
CREATE TABLE tool_calls (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    input TEXT,
    output TEXT,
    structured_output TEXT,
    externalized_path TEXT,
    is_truncated BOOLEAN DEFAULT FALSE,
    is_error BOOLEAN DEFAULT FALSE,
    duration_ms INTEGER,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- 审计日志表
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    action TEXT NOT NULL,
    effect TEXT NOT NULL,
    rule TEXT,
    context TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- 调度任务表
CREATE TABLE scheduled_tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    session_id TEXT,
    cwd TEXT NOT NULL,
    prompt TEXT NOT NULL,
    cron TEXT NOT NULL,
    enabled BOOLEAN DEFAULT TRUE,
    last_run TIMESTAMP,
    next_run TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 使用统计表
CREATE TABLE usage_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost REAL DEFAULT 0,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- 索引
CREATE INDEX idx_messages_session ON messages(session_id, timestamp);
CREATE INDEX idx_tool_calls_session ON tool_calls(session_id, timestamp);
CREATE INDEX idx_audit_session ON audit_log(session_id, timestamp);
CREATE INDEX idx_context_epochs_session ON context_epochs(session_id, started_at);
```

---

## 6. 数据模型

```python
@dataclass
class ContextEpochRecord:
    """上下文纪元记录"""
    id: str
    session_id: str
    baseline: str
    snapshot: str
    started_at: datetime
    ended_at: datetime | None
    end_reason: str | None

@dataclass
class ScheduledTask:
    """调度任务"""
    id: str
    name: str
    session_id: str
    cwd: str
    prompt: str
    cron: str
    enabled: bool = True
    last_run: datetime | None = None
    next_run: datetime | None = None

@dataclass
class SubTask:
    """子任务"""
    id: str
    prompt: str
    dependencies: list[str] = field(default_factory=list)

@dataclass
class SubTaskResult:
    """子任务结果"""
    subtask_id: str
    result: str

@dataclass
class TaskResult:
    """任务结果"""
    subtask_results: list[SubTaskResult]
    summary: str
```

---

## 7. 与其他模块的接口

| 接口 | 方向 | 说明 |
|------|------|------|
| `EventStore` | 核心 | 事件持久化 |
| `AuditLog` | 核心 | 审计记录 |
| `SessionRunner` | 依赖 | 执行 Drain |
| `AgentRegistry` | 依赖 | Agent 管理 |
| `MessageBus` | 核心 | 消息传递 |

---

## 8. 实现计划

| 阶段 | 内容 | 产出 |
|------|------|------|
| Phase 1 | Event Store | 事件持久化 |
| Phase 2 | Audit Log | 审计记录 |
| Phase 3 | Context Epoch 持久化 | 纪元存储 |
| Phase 4 | Automation Scheduler | 任务调度 |
| Phase 5 | Multi-Agent | 多 Agent 协作 |

---

*文档版本: v2.0 | 创建日期: 2026-08-27 | 基于 opencode 架构优化*
