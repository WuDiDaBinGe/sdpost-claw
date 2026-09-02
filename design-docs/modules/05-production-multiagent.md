# Module 05: Production & Multi-Agent — 生产化与多智能体协作

## 1. 模块概述

Production & Multi-Agent 是 sdpost-claw 的生产级能力层，负责持久化存储、自动化调度、安全审计以及多智能体协作。本模块参考 learn-workbuddy 的 s21-s24 章节和 wanman 的多 Agent 矩阵架构。

### 核心理念
> "持久化让状态可查询，自动化让 Agent 自主运行，审计让自主工作可治理，多 Agent 让复杂任务可并行。"

## 2. 子模块架构

```
Production & Multi-Agent
├── 5.1 SQLite Database (数据库持久化)
├── 5.2 Automation Scheduler (自动化调度)
├── 5.3 Audit & Sandbox (审计与沙盒)
└── 5.4 Multi-Agent Collaboration (多智能体协作)
```

---

## 5.1 SQLite Database（数据库持久化）

### 5.1.1 设计目标
- 会话、用量、任务可查询
- WAL 模式支持高并发读写
- 结构化存储便于分析
- 支持数据迁移

### 5.1.2 数据库 Schema

```sql
-- 会话表
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    cwd TEXT NOT NULL,
    title TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP,
    metadata TEXT  -- JSON
);

-- 消息表
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,  -- user / assistant / system / tool
    content TEXT,
    tool_call_id TEXT,
    tool_name TEXT,
    tokens_input INTEGER DEFAULT 0,
    tokens_output INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- 工具调用表
CREATE TABLE IF NOT EXISTS tool_calls (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    message_id TEXT,
    tool_name TEXT NOT NULL,
    arguments TEXT,  -- JSON
    result TEXT,
    error TEXT,
    duration_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

-- 用量统计表
CREATE TABLE IF NOT EXISTS usage_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    date DATE NOT NULL,
    model TEXT NOT NULL,
    tokens_input INTEGER DEFAULT 0,
    tokens_output INTEGER DEFAULT 0,
    tool_calls_count INTEGER DEFAULT 0,
    estimated_cost REAL DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- 任务表（自动化任务）
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    cron_expression TEXT,
    task_type TEXT,  -- once / recurring
    payload TEXT,  -- JSON
    enabled BOOLEAN DEFAULT 1,
    last_run TIMESTAMP,
    next_run TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 审计日志表
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    action TEXT NOT NULL,
    actor TEXT,  -- user / agent / system
    details TEXT,  -- JSON
    hash TEXT,  -- 哈希链
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- 索引
CREATE INDEX idx_messages_session ON messages(session_id, created_at);
CREATE INDEX idx_tool_calls_session ON tool_calls(session_id, created_at);
CREATE INDEX idx_usage_date ON usage_stats(date, model);
CREATE INDEX idx_audit_session ON audit_log(session_id, created_at);
```

### 5.1.3 核心实现

```python
class DatabaseManager:
    """数据库管理器"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = None

    async def initialize(self):
        """初始化数据库"""
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        # 启用 WAL 模式
        self._conn.execute("PRAGMA journal_mode=WAL")
        await self._create_tables()

    async def _create_tables(self):
        """创建表结构"""
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()

    # === 会话操作 ===
    async def save_session(self, session: Session):
        """保存会话"""
        self._conn.execute("""
            INSERT OR REPLACE INTO sessions (id, cwd, title, status, metadata)
            VALUES (?, ?, ?, ?, ?)
        """, (session.id, session.cwd, session.title, session.status.value,
              json.dumps(session.metadata or {})))
        self._conn.commit()

    async def get_session(self, session_id: str) -> SessionRecord:
        """获取会话"""
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return SessionRecord(**dict(row)) if row else None

    async def list_sessions(
        self, limit: int = 50, offset: int = 0
    ) -> list[SessionRecord]:
        """列出会话"""
        rows = self._conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
        return [SessionRecord(**dict(row)) for row in rows]

    # === 用量统计 ===
    async def record_usage(self, usage: UsageRecord):
        """记录用量"""
        self._conn.execute("""
            INSERT INTO usage_stats (session_id, date, model, tokens_input, tokens_output, tool_calls_count, estimated_cost)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (usage.session_id, usage.date, usage.model, usage.tokens_input,
              usage.tokens_output, usage.tool_calls_count, usage.estimated_cost))
        self._conn.commit()

    async def get_daily_usage(self, date: str) -> dict:
        """获取每日用量"""
        row = self._conn.execute("""
            SELECT SUM(tokens_input) as total_input,
                   SUM(tokens_output) as total_output,
                   SUM(tool_calls_count) as total_calls,
                   SUM(estimated_cost) as total_cost
            FROM usage_stats WHERE date = ?
        """, (date,)).fetchone()
        return dict(row) if row else {}
```

---

## 5.2 Automation Scheduler（自动化调度）

### 5.2.1 设计目标
- 定时和周期性任务
- 任务队列管理
- 失败重试机制
- 任务状态追踪

### 5.2.2 调度策略

```python
class TaskType(Enum):
    """任务类型"""
    ONCE = "once"           # 一次性任务
    RECURRING = "recurring" # 周期性任务
    CRON = "cron"           # Cron 表达式

class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED =cancelled"

class AutomationScheduler:
    """自动化调度器"""

    def __init__(self, config: SchedulerConfig, db: DatabaseManager):
        self.config = config
        self.db = db
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._running: dict[str, asyncio.Task] = {}
        self._cron_jobs: dict[str, CronJob] = {}

    async def start(self):
        """启动调度器"""
        # 加载已保存的任务
        await self._load_scheduled_tasks()
        # 启动调度循环
        asyncio.create_task(self._scheduler_loop())

    async def schedule(
        self,
        name: str,
        task_type: TaskType,
        payload: dict,
        cron_expression: str = None,
        run_at: datetime = None,
    ) -> str:
        """创建定时任务"""
        task_id = str(uuid.uuid4())

        # 计算下次执行时间
        if task_type == TaskType.CRON and cron_expression:
            next_run = self._calc_next_cron_run(cron_expression)
        elif task_type == TaskType.ONCE and run_at:
            next_run = run_at
        else:
            next_run = datetime.now()

        # 保存到数据库
        self.db._conn.execute("""
            INSERT INTO scheduled_tasks (id, name, cron_expression, task_type, payload, next_run)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (task_id, name, cron_expression, task_type.value,
              json.dumps(payload), next_run))
        self.db._conn.commit()

        return task_id

    async def cancel(self, task_id: str):
        """取消任务"""
        # 取消运行中的任务
        running_task = self._running.pop(task_id, None)
        if running_task:
            running_task.cancel()

        # 更新数据库状态
        self.db._conn.execute(
            "UPDATE scheduled_tasks SET enabled = 0 WHERE id = ?",
            (task_id,)
        )
        self.db._conn.commit()

    async def _scheduler_loop(self):
        """调度主循环"""
        while True:
            try:
                # 获取待执行的任务
                now = datetime.now()
                pending = self.db._conn.execute("""
                    SELECT * FROM scheduled_tasks
                    WHERE enabled = 1 AND next_run <= ?
                    ORDER BY next_run ASC LIMIT 10
                """, (now,)).fetchall()

                for row in pending:
                    task_id = row["id"]
                    if task_id not in self._running:
                        self._running[task_id] = asyncio.create_task(
                            self._execute_scheduled_task(dict(row))
                        )

                await asyncio.sleep(30)  # 每 30 秒检查一次

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"调度器错误: {e}")
                await asyncio.sleep(60)

    async def _execute_scheduled_task(self, task: dict):
        """执行定时任务"""
        task_id = task["id"]
        try:
            # 更新状态为运行中
            self.db._conn.execute(
                "UPDATE scheduled_tasks SET last_run = ? WHERE id = ?",
                (datetime.now(), task_id)
            )
            self.db._conn.commit()

            # 执行任务
            payload = json.loads(task["payload"])
            result = await self._run_task(payload)

            # 计算下次执行时间
            if task["task_type"] == TaskType.RECURRING.value:
                next_run = self._calc_next_cron_run(task["cron_expression"])
                self.db._conn.execute(
                    "UPDATE scheduled_tasks SET next_run = ? WHERE id = ?",
                    (next_run, task_id)
                )
            else:
                self.db._conn.execute(
                    "UPDATE scheduled_tasks SET enabled = 0 WHERE id = ?",
                    (task_id,)
                )
            self.db._conn.commit()

        except Exception as e:
            logger.error(f"任务执行失败 {task_id}: {e}")
        finally:
            self._running.pop(task_id, None)
```

### 5.2.3 预置自动化任务

```python
class BuiltInTasks:
    """内置自动化任务"""

    @staticmethod
    def daily_report(db: DatabaseManager) -> dict:
        """每日报告"""
        today = datetime.now().strftime("%Y-%m-%d")
        usage = db.get_daily_usage(today)
        return {
            "date": today,
            "total_tokens": usage.get("total_input", 0) + usage.get("total_output", 0),
            "total_cost": usage.get("total_cost", 0),
            "total_tool_calls": usage.get("total_calls", 0),
        }

    @staticmethod
    def weekly_summary(db: DatabaseManager) -> dict:
        """每周摘要"""
        # 汇总本周数据
        pass

    @staticmethod
    def cleanup_old_sessions(db: DatabaseManager, days: int = 30):
        """清理旧会话"""
        cutoff = datetime.now() - timedelta(days=days)
        db._conn.execute(
            "DELETE FROM sessions WHERE closed_at < ?",
            (cutoff,)
        )
        db._conn.commit()
```

---

## 5.3 Audit & Sandbox（审计与沙盒）

### 5.3.1 设计目标
- 每步操作留痕，不可篡改
- 命令安全分级
- 沙盒边界控制
- 哈希链保证完整性

### 5.3.2 审计日志实现

```python
class AuditLog:
    """审计日志 — 哈希链保证不可篡改"""

    def __init__(self, db: DatabaseManager):
        self.db = db
        self._last_hash = self._load_last_hash()

    def _load_last_hash(self) -> str:
        """加载最后一条记录的哈希"""
        row = self.db._conn.execute(
            "SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["hash"] if row else "GENESIS"

    async def record(
        self,
        action: str,
        actor: str,
        details: dict,
        session_id: str = None,
    ):
        """记录审计事件"""
        # 计算哈希链
        data = json.dumps({
            "action": action,
            "actor": actor,
            "details": details,
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
            "prev_hash": self._last_hash,
        }, sort_keys=True)

        current_hash = hashlib.sha256(data.encode()).hexdigest()

        self.db._conn.execute("""
            INSERT INTO audit_log (session_id, action, actor, details, hash)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, action, actor, json.dumps(details), current_hash))
        self.db._conn.commit()

        self._last_hash = current_hash

    async def verify_chain(self) -> bool:
        """验证哈希链完整性"""
        rows = self.db._conn.execute(
            "SELECT * FROM audit_log ORDER BY id ASC"
        ).fetchall()

        prev_hash = "GENESIS"
        for row in rows:
            data = json.dumps({
                "action": row["action"],
                "actor": row["actor"],
                "details": json.loads(row["details"]),
                "session_id": row["session_id"],
                "prev_hash": prev_hash,
            }, sort_keys=True)

            expected_hash = hashlib.sha256(data.encode()).hexdigest()
            if expected_hash != row["hash"]:
                return False
            prev_hash = row["hash"]

        return True
```

### 5.3.3 沙盒策略

```python
class SandboxPolicy:
    """沙盒策略"""

    def __init__(self, config: SandboxConfig):
        self.config = config
        self._command_rules = self._build_command_rules()

    def _build_command_rules(self) -> list[CommandRule]:
        """构建命令规则"""
        return [
            CommandRule(
                pattern=r"^rm\s+-rf\s+/",
                level=PermissionLevel.DENY,
                description="禁止删除根目录",
            ),
            CommandRule(
                pattern=r"^(mkfs|dd)\s+",
                level=PermissionLevel.DENY,
                description="禁止磁盘操作",
            ),
            CommandRule(
                pattern=r"^curl.*\|.*bash",
                level=PermissionLevel.CONFIRM,
                description="下载执行需确认",
            ),
            CommandRule(
                pattern=r"^sudo\s+",
                level=PermissionLevel.CONFIRM,
                description="sudo 操作需确认",
            ),
            CommandRule(
                pattern=r"^git\s+push",
                level=PermissionLevel.NOTIFY,
                description="git push 后通知",
            ),
        ]

    def evaluate(self, command: str) -> SandboxDecision:
        """评估命令"""
        for rule in self._command_rules:
            if re.match(rule.pattern, command):
                return SandboxDecision(
                    allowed=rule.level != PermissionLevel.DENY,
                    level=rule.level,
                    reason=rule.description,
                )
        return SandboxDecision(allowed=True, level=PermissionLevel.AUTO)

@dataclass
class CommandRule:
    """命令规则"""
    pattern: str
    level: PermissionLevel
    description: str

@dataclass
class SandboxDecision:
    """沙盒决策"""
    allowed: bool
    level: PermissionLevel
    reason: str = ""
```

---

## 5.4 Multi-Agent Collaboration（多智能体协作）

### 5.4.1 设计目标
- 多 Agent 并行处理复杂任务
- Agent 间消息通信
- 任务分配与结果汇总
- 参考 wanman 的 Supervisor 架构

### 5.4.2 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                    Multi-Agent Architecture                   │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                  Supervisor (编排器)                   │   │
│  │  - 任务分解与分配                                      │   │
│  │  - Agent 生命周期管理                                  │   │
│  │  - 消息路由                                            │   │
│  │  - 结果汇总                                            │   │
│  └──────────────────────────┬───────────────────────────┘   │
│                              │                               │
│            ┌─────────────────┼─────────────────┐            │
│            │                 │                 │            │
│            ▼                 ▼                 ▼            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │  Agent A     │  │  Agent B     │  │  Agent C     │     │
│  │  (数据分析)   │  │  (文档撰写)   │  │  (可视化)    │     │
│  │              │  │              │  │              │     │
│  │  独立工作区   │  │  独立工作区   │  │  独立工作区   │     │
│  │  独立模型    │  │  独立模型     │  │  独立模型     │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
│            │                 │                 │            │
│            └─────────────────┼─────────────────┘            │
│                              │                               │
│                              ▼                               │
│                    ┌──────────────────┐                      │
│                    │  Shared Context  │                      │
│                    │  (共享上下文)     │                      │
│                    └──────────────────┘                      │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 5.4.3 核心实现

```python
class Supervisor:
    """多 Agent 编排器"""

    def __init__(self, config: SupervisorConfig):
        self.config = config
        self._agents: dict[str, AgentWorker] = {}
        self._message_bus = MessageBus()
        self._task_pool = TaskPool()
        self._shared_context = SharedContext()

    async def register_agent(
        self, name: str, agent_config: AgentConfig, role: str
    ):
        """注册 Agent"""
        worker = AgentWorker(
            name=name,
            config=agent_config,
            role=role,
            message_bus=self._message_bus,
            shared_context=self._shared_context,
        )
        self._agents[name] = worker

    async def execute_goal(self, goal: str) -> GoalResult:
        """执行目标"""
        # 1. 分解目标
        subtasks = await self._decompose_goal(goal)

        # 2. 分配任务
        assignments = await self._assign_tasks(subtasks)

        # 3. 并行执行
        results = await asyncio.gather(*[
            self._execute_with_agent(agent_name, task)
            for agent_name, task in assignments.items()
        ])

        # 4. 汇总结果
        return await self._synthesize_results(results)

    async def _decompose_goal(self, goal: str) -> list[SubTask]:
        """分解目标为子任务"""
        # 使用 LLM 分解
        prompt = f"""将以下目标分解为可并行的子任务：

目标: {goal}

输出格式: JSON 数组，每个子任务包含:
- id: 任务ID
- description: 任务描述
- required_role: 所需角色
- dependencies: 依赖的任务ID列表
"""
        # 调用模型分解...
        return subtasks

    async def _assign_tasks(
        self, subtasks: list[SubTask]
    ) -> dict[str, SubTask]:
        """分配任务给 Agent"""
        assignments = {}
        for subtask in subtasks:
            # 找到合适的 Agent
            agent_name = self._find_best_agent(subtask.required_role)
            assignments[agent_name] = subtask
        return assignments

class AgentWorker:
    """Agent 工作器"""

    def __init__(
        self,
        name: str,
        config: AgentConfig,
        role: str,
        message_bus: MessageBus,
        shared_context: SharedContext,
    ):
        self.name = name
        self.config = config
        self.role = role
        self.message_bus = message_bus
        self.shared_context = shared_context
        self._agent_loop = AgentLoop(config)

    async def run(self, task: SubTask) -> SubTaskResult:
        """执行任务"""
        # 1. 接收任务
        await self._emit_status(task.id, TaskStatus.RUNNING)

        # 2. 执行
        try:
            result = await self._agent_loop.run(
                user_input=task.description,
                session=await self._create_session(task),
            )
            await self._emit_status(task.id, TaskStatus.COMPLETED)
            return SubTaskResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED,
                result=result,
            )
        except Exception as e:
            await self._emit_status(task.id, TaskStatus.FAILED)
            return SubTaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(e),
            )

    async def send_message(self, to: str, content: str):
        """发送消息给其他 Agent"""
        await self.message_bus.send(
            from_agent=self.name,
            to_agent=to,
            content=content,
        )

class MessageBus:
    """消息总线"""

    def __init__(self):
        self._queues: dict[str, asyncio.Queue] = {}
        self._history: list[AgentMessage] = []

    async def send(self, from_agent: str, to_agent: str, content: str):
        """发送消息"""
        message = AgentMessage(
            id=str(uuid.uuid4()),
            from_agent=from_agent,
            to_agent=to_agent,
            content=content,
            timestamp=datetime.now(),
        )
        self._history.append(message)

        queue = self._queues.get(to_agent)
        if queue:
            await queue.put(message)

    async def receive(self, agent_name: str, timeout: float = None) -> AgentMessage:
        """接收消息"""
        queue = self._queues.setdefault(agent_name, asyncio.Queue())
        return await asyncio.wait_for(queue.get(), timeout=timeout)

@dataclass
class AgentMessage:
    """Agent 消息"""
    id: str
    from_agent: str
    to_agent: str
    content: str
    timestamp: datetime
    priority: str = "normal"  # high / normal / low
```

### 5.4.4 预置 Agent 角色

```python
class AgentRoles:
    """预置 Agent 角色配置"""

    @staticmethod
    def data_analyst() -> AgentConfig:
        """数据分析师"""
        return AgentConfig(
            name="data-analyst",
            role="数据分析专家",
            system_prompt="你是数据分析专家，擅长从数据中发现洞察。",
            tools=["read_file", "write_file", "bash"],
            model_tier=ModelTier.DEFAULT,
        )

    @staticmethod
    def writer() -> AgentConfig:
        """文档撰写师"""
        return AgentConfig(
            name="writer",
            role="技术写作专家",
            system_prompt="你是技术写作专家，擅长撰写清晰的技术文档。",
            tools=["read_file", "write_file", "search_files"],
            model_tier=ModelTier.DEFAULT,
        )

    @staticmethod
    def visualizer() -> AgentConfig:
        """可视化专家"""
        return AgentConfig(
            name="visualizer",
            role="数据可视化专家",
            system_prompt="你是数据可视化专家，擅长创建直观的图表。",
            tools=["read_file", "write_file", "bash"],
            model_tier=ModelTier.LITE,
        )

    @staticmethod
    def coordinator() -> AgentConfig:
        """协调者"""
        return AgentConfig(
            name="coordinator",
            role="项目协调者",
            system_prompt="你负责协调各 Agent 的工作，确保任务顺利完成。",
            tools=["spawn_agent", "send_message"],
            model_tier=ModelTier.LITE,
        )
```

---

## 6. 数据模型

```python
@dataclass
class SessionRecord:
    """会话记录"""
    id: str
    cwd: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime
    closed_at: datetime = None
    metadata: dict = None

@dataclass
class UsageRecord:
    """用量记录"""
    session_id: str
    date: str
    model: str
    tokens_input: int
    tokens_output: int
    tool_calls_count: int
    estimated_cost: float

@dataclass
class ScheduledTask:
    """定时任务"""
    id: str
    name: str
    task_type: TaskType
    cron_expression: str
    payload: dict
    enabled: bool
    last_run: datetime
    next_run: datetime

@dataclass
class SubTask:
    """子任务"""
    id: str
    description: str
    required_role: str
    dependencies: list[str]

@dataclass
class SubTaskResult:
    """子任务结果"""
    task_id: str
    status: TaskStatus
    result: Any = None
    error: str = None
```

---

## 7. 与其他模块的接口

| 接口 | 方向 | 说明 |
|------|------|------|
| `AgentLoop` | 调用 | 执行 Agent 任务 |
| `SessionManager` | 依赖 | 会话持久化 |
| `PermissionGuard` | 依赖 | 安全审计 |
| `ToolRegistry` | 依赖 | 工具调用审计 |
| `EventBus` | 输出 | 事件发布 |

---

## 8. 实现计划

| 阶段 | 内容 | 产出 |
|------|------|------|
| Phase 1 | SQLite 数据库 | 数据可持久化存储 |
| Phase 2 | 自动化调度 | 定时任务可运行 |
| Phase 3 | 审计与沙盒 | 操作可审计可追溯 |
| Phase 4 | 多 Agent 协作 | 复杂任务可并行处理 |

---

*文档版本: v1.0 | 创建日期: 2026-08-27*
