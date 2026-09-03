# Module 05: Production & Multi-Agent v2 — 生产级与多智能体（opencode 增强版）

> **v2.1 实现对齐说明（2026-09-03）**：本节已按实际代码更新。要点变更：
> - `EventStore`（production/events.py）与设计基本一致：JSONL 追加写 + SHA256 哈希链 + 重放/过滤/完整性校验
> - `AuditLog`（production/audit.py）实际只依赖 `EventStore`（无双写 SQLite）；权限检查按 effect 分派为 PERMISSION_DENIED / PERMISSION_ASKED / PERMISSION_CHECKED 三种事件
> - `AutomationScheduler`（production/scheduler.py）实际**不直接依赖** SessionRunner——通过 `on_task_due()` 回调解耦；cron 解析用 `croniter`
> - `Supervisor`（production/multiagent.py）实际直接构造 `Agent`（不走 AgentRegistry），`_run_agent` 使用实际签名 `session_runner.run(session=session, system_context=..., force=True)`
> - `Database`（production/database.py）为**同步** sqlite3 + WAL（非异步 API）；schema 含 8 张表（新增 workspace_memory / user_preferences）
> - **接线现状**：production/ 包为独立组件集合，尚未接入桌面端主链路（config.audit_enabled 预留开关）

## 1. 设计演进说明

本模块在初版设计基础上，吸收了 opencode 的以下优秀设计：

| 初版设计 | opencode 启发 | 改进 |
|----------|--------------|------|
| 简单审计日志 | Event Sourcing + Hash Chain | 完整事件溯源 |
| 基础调度 | Session Drain 集成 | 更清晰的执行模型 |
| 简单多 Agent | Agent Modes + Permission Ruleset | 更灵活的 Agent 管理 |

---

## 2. Event Sourcing & Audit Log v2（实际实现于 production/）

### 2.1 EventStore（production/events.py）

```
Event(type, session_id, timestamp, data, context_snapshot?, hash_chain?)
        ↓ append（逐事件哈希链：SHA256(type+session_id+timestamp+data+previous_hash)）
events/<session_id>.jsonl（追加写）
        ↓ replay / get_events_by_type / verify_integrity / clear
```

```python
class EventType(Enum):
    # 会话事件
    SESSION_CREATED / SESSION_RESUMED / SESSION_CLOSED / SESSION_EPOCH_CHANGED
    # 输入事件
    PROMPT_SUBMITTED / PROMPT_ADMITTED
    # 工具事件
    TOOL_CALLED / TOOL_RESULT_SETTLED / TOOL_OUTPUT_EXTERNALIZED
    # 模型事件
    MODEL_REQUEST / MODEL_RESPONSE / MODEL_ERROR
    # 上下文事件
    CONTEXT_INITIALIZED / CONTEXT_UPDATED / CONTEXT_REPLACED / CONTEXT_UNAVAILABLE
    # 压缩事件
    COMPACTION_TRIGGERED / COMPACTION_COMPLETED
    # 权限事件
    PERMISSION_CHECKED / PERMISSION_DENIED / PERMISSION_ASKED
    # Agent 事件
    AGENT_CREATED / AGENT_MODE_CHANGED / SUB_AGENT_SPAWNED


class EventStore:
    """JSONL 事件存储（哈希链完整性）"""

    def __init__(self, base_path: Path): ...   # 写 base_path/events/
    async def append(self, event: Event) -> None:
        # _last_hashes[session_id] 维护链尾，compute_hash(prev) 后落盘
    async def replay(self, session_id) -> list[Event]: ...
    async def get_events_by_type(self, session_id, event_type) -> list[Event]: ...
    async def verify_integrity(self, session_id) -> bool:
        # 逐事件重算哈希并与 hash_chain 比对
    async def clear(self, session_id) -> None: ...
```

### 2.2 AuditLog（production/audit.py）

```python
class AuditLog:
    """审计日志：权限 / 上下文 / 工具 / 模型 / 错误（仅依赖 EventStore）"""

    def __init__(self, event_store: EventStore): ...

    async def log_permission_check(self, session_id, action, decision: PermissionDecision, context=None):
        # effect == "deny" → PERMISSION_DENIED
        # effect == "ask"  → PERMISSION_ASKED
        # 其余             → PERMISSION_CHECKED
        # data: {action, effect, rule: decision.rule.action, context}

    async def log_context_change(self, session_id, change_type, source_key, details=None): ...
        # update → CONTEXT_UPDATED，否则 CONTEXT_REPLACED
    async def log_tool_call(self, session_id, tool_name, tool_input, duration_ms=0): ...
    async def log_tool_result(self, session_id, tool_name, is_error=False, is_truncated=False): ...
    async def log_model_request(self, session_id, model, message_count, tool_count): ...
    async def log_model_response(self, session_id, model, usage=None): ...
    async def log_error(self, session_id, error_type, message): ...

    async def get_audit_trail(self, session_id, event_type=None) -> list[Event]:
        # 按类型过滤或全量重放
```

> 与初版差异：初版设计为 EventStore + SQLite `audit_log` 表双写；实际只走事件流。SQLite `audit_log` 表与 `Database.save_audit_log()` 已实现，作为后续接线选项。

---

## 3. Automation Scheduler v2（实际实现于 production/scheduler.py）

### 3.1 ScheduledTask（croniter 驱动）

```python
@dataclass
class ScheduledTask:
    id / name / session_id / cwd / prompt / cron
    enabled: bool = True
    last_run / next_run: datetime | None

    def should_run(self, now) -> bool: ...
    def compute_next_run(self, base=None) -> datetime | None: ...
    def to_dict(self) -> dict: ...
```

### 3.2 AutomationScheduler（回调解耦）

```python
class AutomationScheduler:
    """cron 调度循环（asyncio.Task，每 60s 检查一次到期任务）"""

    def on_task_due(self, callback) -> None:
        """注册到期回调 —— 实际执行入口。

        与初版差异：调度器不直接持有 SessionRunner / SessionStore；
        由调用方注册回调（如：创建/复用会话 → submit_prompt →
        session_runner.run(session=session, force=True) → 审计事件），
        回调异常被吞掉不影响循环。"""

    def start / stop                      # asyncio.create_task(self._run_loop())
    def add_task / remove_task / get_task / list_tasks
    def enable_task / disable_task
```

> 现状：桌面端 `/api/automations` 返回空占位列表；调度器尚未在应用装配中启动。

---

## 4. Multi-Agent Collaboration v2（实际实现于 production/multiagent.py）

### 4.1 MessageBus（优先级消息总线）

```python
class MessageBus:
    """asyncio.PriorityQueue 按 agent_id 分队列。

    优先级约定：steer=0（中断，最高） / send_result=5 / follow_up=10（普通）"""

    async def send(self, agent_id, message: dict, priority=10): ...
    async def recv(self, agent_id) -> dict: ...
    async def steer(self, agent_id, message): ...
    async def follow_up(self, agent_id, message): ...
    async def send_result(self, agent_id, message): ...
```

### 4.2 Supervisor（监督者）

```python
@dataclass
class SubTask:
    id: str
    prompt: str
    dependencies: list[str]
    result: str | None = None

@dataclass
class SubTaskResult:
    subtask_id: str
    result: str

@dataclass
class TaskResult:
    subtask_results: list[SubTaskResult]
    summary: str


class Supervisor:
    def __init__(self, agent_registry, session_runner, message_bus): ...

    async def execute_task(self, task: str, context=None) -> TaskResult:
        # 1. PLAN Agent 分析分解（"Analyze this task and break it into subtasks…
        #    Format each subtask on a new line starting with '- '"）
        # 2. _parse_subtasks：按 "- "/"* "/裸行解析；解析不出则整体作为一个子任务
        # 3. asyncio.gather 并行执行 BUILD 子 Agent
        # 4. _aggregate_results：拼装 "## Task Execution Results" Markdown

    async def _run_agent(self, agent: Agent, prompt: str,
                         system_context: str = "", session: Session | None = None) -> str:
        # 实际签名：直接构造 Session（cwd=".", title=f"Agent: {agent.name}",
        # agent_mode=agent.mode.value），调用
        # session_runner.run(session=session, system_context=..., force=True)
        # 注意：prompt 未实际进入 Inbox——子 Agent 输入链路待完善
```

> 与初版差异：实际直接构造 `Agent(name=..., mode=..., permissions=PermissionRuleset())` 而非经 AgentRegistry.create_sub_agent；子 Agent 的 prompt 通过新会话承载的接线待完成。

---

## 5. SQLite Database v2（实际实现于 production/database.py）

### 5.1 Database 类（同步 sqlite3 + WAL）

```python
class Database:
    """同步 API（sqlite3 + row_factory=Row）：
    - PRAGMA journal_mode=WAL、foreign_keys=ON
    - connect()/close()/execute()/executemany()/commit()/fetch_one()/fetch_all()

    领域操作：
    - Session: save_session / get_session / list_sessions / delete_session
    - Message: save_message / get_messages
    - ToolCall: save_tool_call（含 structured_output / externalized_path / duration_ms）
    - Audit:   save_audit_log / get_audit_trail
    - Usage:   save_usage / get_usage_summary
    - Task:    save_scheduled_task / get_scheduled_tasks / delete_scheduled_task
    - Memory:  get_workspace_memory / save_workspace_memory
    - Prefs:   get_preferences / save_preferences
    """
```

### 5.2 数据库 Schema（8 张表 + 5 个索引，实际 SCHEMA）

```sql
sessions        (id PK, cwd, title, agent_mode='build', status='active',
                 context_epoch_id, created_at, updated_at)
context_epochs  (id PK, session_id FK, baseline, snapshot, started_at, ended_at, end_reason)
messages        (id PK, session_id FK, role, content, metadata, tokens, timestamp)
tool_calls      (id PK, session_id FK, message_id, tool_name, input, output,
                 structured_output, externalized_path, is_truncated, is_error,
                 duration_ms, timestamp)
audit_log       (id AUTOINCREMENT, session_id FK, action, effect, rule, context, timestamp)
scheduled_tasks (id PK, name, session_id, cwd, prompt, cron, enabled, last_run, next_run, created_at)
usage_stats     (id AUTOINCREMENT, session_id FK, model, input_tokens, output_tokens, cost, timestamp)
workspace_memory(workspace_id PK, daily_summary, recent_decisions='[]', key_facts='[]', updated_at)
user_preferences(id=1 CHECK, language='zh-CN', communication_style='professional',
                 expertise_level='intermediate', updated_at)

-- 索引
idx_messages_session / idx_tool_calls_session / idx_audit_session
idx_context_epochs_session / idx_usage_session
```

> 与初版差异：新增 `workspace_memory` / `user_preferences` 两表（生产环境记忆/偏好的持久化选项；桌面端日常路径用 memory/ 包的文件存储，见 Module 03 §5.2）。

---

## 6. 与其他模块的接口

| 接口 | 方向 | 说明 |
|------|------|------|
| `EventStore` | 核心 | 事件持久化（events/ JSONL + 哈希链） |
| `AuditLog` | 核心 | 审计记录（仅依赖 EventStore） |
| `SessionRunner` | 依赖 | 执行 Drain（Supervisor._run_agent） |
| `AgentRegistry` | 依赖 | Agent 管理（Supervisor 当前直接构造 Agent） |
| `MessageBus` | 核心 | 优先级消息传递 |
| `Database` | 依赖 | 生产 SQLite 持久化（同步 API） |

---

## 7. 实现计划

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 | Event Store（哈希链） | ✅ 已完成（production/events.py） |
| Phase 2 | Audit Log | ✅ 已完成（production/audit.py，事件流单写） |
| Phase 3 | SQLite Database + Schema | ✅ 已完成（production/database.py，8 表 + WAL） |
| Phase 4 | Automation Scheduler | ✅ 骨架完成（croniter + 回调解耦；应用装配未接线） |
| Phase 5 | Multi-Agent（Supervisor + MessageBus） | ✅ 骨架完成（子 Agent 输入链路待完善） |
| Phase 6 | 生产组件接入桌面主链路 | ⏳ 待接入（audit_enabled 等开关已预留） |

---

*文档版本: v2.1 | 创建日期: 2026-08-27 | 最近更新: 2026-09-03 | 基于 opencode 架构优化*
