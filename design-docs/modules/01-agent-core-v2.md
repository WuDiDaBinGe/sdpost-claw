# Module 01: Agent Core v2 — 智能体核心引擎（opencode 增强版）

> **v2.1 实现对齐说明（2026-09-03）**：本节已按实际代码更新。要点变更：
> - Provider-Turn Boundary 未单独成文件，`SafeProviderTurnBoundary` 实现于 [drain.py](file:///f:/MyCoding/sdpost-claw/src/sdpost_claw/agent/drain.py)
> - 执行循环由 `harness/` 的 `SessionDriver`（turn/step 状态机）驱动，工具执行走 `harness/tool_pipeline.py` 三阶段管道
> - 工具输入 Schema 采用 JSON Schema dict（非 Pydantic），实际内置工具为 8 个（无 websearch/task/skill）
> - 权限评估策略为"最高优先级匹配，同优先级最后匹配生效"

## 1. 设计演进说明

本模块在初版设计基础上，吸收了 opencode 的以下优秀设计：

| 初版设计 | opencode 启发 | 改进 |
|----------|--------------|------|
| 简单 Agent Loop | Session Drain + Provider-Turn Boundary | 更清晰的执行边界和状态管理 |
| 基础 Tool Dispatch | Schema-validated Tool Definition | 类型安全的工具输入/输出验证 |
| 简单权限检查 | Wildcard Permission Ruleset | 灵活的模式匹配权限 |
| 基础压缩 | Structured Compaction Template | 高质量的结构化摘要 |

实现阶段又吸收了 **deepseek-harness (dsh)** 的执行骨架设计（turn/step 状态机、Inbox、append-only SessionLog、三阶段 Tool Pipeline），见 §2.3。

---

## 2. 核心架构

### 2.1 执行模型：Session Drain + Provider Turn

```
┌─────────────────────────────────────────────────────────────────────┐
│                     opencode-inspired Execution Model                │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│   ┌──────────────────────────────────────────────────────────────┐  │
│   │                   Session Drain (会话排放)                     │  │
│   │   - 进程本地的执行协调，不是持久化实体                          │  │
│   │   - 推进符合条件的持久化工作                                    │  │
│   │   - 没有持久化的身份或转录边界                                  │  │
│   │                                                              │  │
│   │   ┌────────────────────────────────────────────────────┐    │  │
│   │   │            Provider Turn (模型调用轮次)              │    │  │
│   │   │                                                    │    │  │
│   │   │  1. 准备 Baseline System Context                   │    │  │
│   │   │  2. 推进已接受的用户输入                            │    │  │
│   │   │  3. 结算已完成的工具结果                            │    │  │
│   │   │  4. 在 Safe Provider-Turn Boundary                  │    │  │
│   │   │     纳入上下文变更                                  │    │  │
│   │   │  5. 调用模型 Provider                               │    │  │
│   │   │  6. 处理响应 / 执行工具                             │    │  │
│   │   │                                                    │    │  │
│   │   └────────────────────────────────────────────────────┘    │  │
│   │                                                              │  │
│   └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Safe Provider-Turn Boundary（安全模型调用边界 — 实际实现）

实现于 [drain.py](file:///f:/MyCoding/sdpost-claw/src/sdpost_claw/agent/drain.py) `SafeProviderTurnBoundary`。在每次模型调用前：

1. **Prompt Promotion**：`PromptPromotion.promote()` 从 `session.inbox` 领取（drain）`next_turn` 队列中的用户输入，写入 `session.history` 并镜像写入 append-only 事件日志（`user_message` 事件）
2. **Tool Result Settlement**：将 `session.pending_tool_results` 结算进 history（`role=tool`）并写入 `tool_result` 表面事件
3. **Non-waking Inject**：调用 `session.drain_injections()` 领取 `next_step` 队列的上下文注入，记录为 ignorable `context_injection` 事件，并把文本以 `## Context Update` 补丁折叠进本次模型可见的 system context（不产生额外 user turn）
4. **组装**：返回 `PreparedTurn`（system_context / messages / tools / admitted / settled），`has_work()` 判断是否有工作可做

```python
class SafeProviderTurnBoundary:
    """安全模型调用边界 - 实现于 agent/drain.py"""

    def __init__(self, session: "Session"):
        self.session = session

    async def prepare(
        self,
        system_context: str,
        tools: list[ToolDefinition],
    ) -> PreparedTurn:
        # 1. 推进符合条件的输入（从 Inbox next_turn 领取）
        promotion = PromptPromotion(self.session)
        admitted = await promotion.promote()

        # 2. 结算已完成的工具结果
        settled = await self._settle_tool_results()

        # 3. 在 step 边界领取上下文注入（non-waking inject），
        #    折叠为 "## Context Update" 补丁
        injections = self.session.drain_injections()
        if injections:
            patch = "\n\n## Context Update\n" + "\n\n".join(
                inj.text for inj in injections
            )
            system_context = system_context + patch

        # 4. 组装 messages / tools
        return PreparedTurn(
            system_context=system_context,
            messages=list(self.session.history),
            tools=[t.to_dict() for t in tools],
            admitted=admitted,
            settled=settled,
        )
```

### 2.3 Harness 执行骨架（v2.1 新增，借鉴 deepseek-harness）

实现层新增 `harness/` 包，为 Session Drain 提供执行骨架。`SessionRunner.run()` 仍是唯一的每步执行器（签名不变），harness 只负责生命周期与策略：

| 组件 | 文件 | 职责 |
|------|------|------|
| `SessionDriver` | [harness/driver.py](file:///f:/MyCoding/sdpost-claw/src/sdpost_claw/harness/driver.py) | turn/step 状态机。**turn** = 0..n 个 step，以 `turn_start`/`turn_end`（带结构化 reason：completed/blocked/aborted/error/max_tokens/interrupted/max_steps）为界；**step** = 一次模型请求 + 其工具调用。`pre_turn`/`pre_step` 钩子是软停止检查点（deny-only，首个 Abort 生效），取代硬编码 max_iterations 作为主控（旧上限仅作安全兜底） |
| `Inbox` | [harness/inbox.py](file:///f:/MyCoding/sdpost-claw/src/sdpost_claw/harness/inbox.py) | 双队列输入：`next_turn`（用户 prompt，开新 turn）+ `next_step`（`Inject`：context_update/skill/file_change 等，在下一个 step 边界合并且**不唤醒**驱动）。`claim_next_turn`/`claim_next_step` 为领取式（drain），保证每条恰好合并一次 |
| `SessionLog` | [harness/session_log.py](file:///f:/MyCoding/sdpost-claw/src/sdpost_claw/harness/session_log.py) | append-only 事件日志，`append()` 是唯一写入口（快照数据 + 单调 seq）。`derive_messages()` 从表面事件（`user_message`/`assistant_message`/`tool_result`，见 `SURFACE_TYPES`）投影出 OpenAI 格式消息历史——"模型可见即已记录"。支持增量持久化 JSONL（水位线 `_persisted_seq`） |
| 事件词表 | [harness/events.py](file:///f:/MyCoding/sdpost-claw/src/sdpost_claw/harness/events.py) | `turn_start/end`、`step_start/end`、`user_message`、`assistant_message`、`tool_call`、`tool_result`、`request_header`、`session_end`、`compaction_occurred`、`context_injection`；`SessionEvent` 带 `seq`/`time`/`ignorable`（未知 ignorable 事件可被读者跳过） |
| 三阶段工具管道 | [harness/tool_pipeline.py](file:///f:/MyCoding/sdpost-claw/src/sdpost_claw/harness/tool_pipeline.py) | `run()`：① pre-execute（PermissionRuleset 决策 + deny-only 单调 guards，ask 在自动化模式自动拒绝）→ ② execute（around 钩子接缝，预留 timeout/retry/audit）→ ③ post-execute（PostGuard 可 accept/block/replace）+ 工具可选 `finalize_content` 同步内容重写。每次调用 emit 一条 ignorable `tool_call` 观察事件（含结构化 pre-decision，作为审计记录） |
| `CompactionBridge` | [harness/compaction_bridge.py](file:///f:/MyCoding/sdpost-claw/src/sdpost_claw/harness/compaction_bridge.py) | 无状态 `CompactionEngine`（策略）与驱动循环之间的有状态协调器，见 §6 |

应用层 agent loop（[main.py](file:///f:/MyCoding/sdpost-claw/src/sdpost_claw/main.py) `Application._process_input`）驱动顺序：
`turn_start` → [`step_start`（pre_step 钩子软停止）→ `SessionRunner.run()` → `step_end`]×n → `turn_end`（finally 中恰好一次）→ `SessionManager.persist_log()` 落盘事件日志。

### 2.4 SessionRunner（实际实现）

```python
class SessionRunner:
    """执行一次 Session Drain - agent/drain.py"""

    def __init__(self, tool_registry, permission_ruleset,
                 model_provider=None, event_bus=None):
        ...  # Phase 4/5 协调器在构造后注入：
             # system_context / compaction_bridge / summary_source

    async def run(self, session, system_context, force=False, on_delta=None) -> DrainResult:
        # 1. 每步 reconcile 上下文快照（Phase 4）：Updated 结果路由进
        #    session.inbox（context_update 注入），本步边界即折叠进
        #    system context —— 无 baseline 补丁、无额外 user turn。
        #    快照在首个 step 惰性初始化，sidecar/desktop 同样生效。
        # 2. SafeProviderTurnBoundary.prepare()
        # 3. force=False 且无工作 → DrainResult(status="no_work")
        # 4. 压缩压力检查（Phase 5）：CompactionBridge.maybe_compact()，
        #    命中后 summary_source.update_summary(session.summary)
        # 5. 调用模型（on_delta 回调支持流式 text/reasoning）
        # 6. has_tool_calls → _execute_tools()（走三阶段管道）→
        #    status="tool_execution"；否则 assistant 文本入 history +
        #    assistant_message 事件 → status="text_response"
```

`DrainResult.status`：`no_work | tool_execution | text_response | error`（error 携带 `error` 字段）。

工具权限推断（`_infer_permission`）：`read/glob/grep→file.read`、`write→file.write`、`edit→file.edit`、`bash→shell.execute`、`webfetch→network.request`、`question→user.interact`，未知工具回退 `tool.<name>`。

---

## 3. System Context 系统（核心改进）

### 3.1 设计理念（借鉴 opencode CONTEXT.md）

```
┌─────────────────────────────────────────────────────────────────────┐
│                    System Context Architecture                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│   System Context = 零个或多个 Context Source 的不透明载体             │
│                                                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │ Context Source (上下文源)                                    │   │
│   │                                                             │   │
│   │ - key: 稳定的命名空间标识符 (如 "date/current",             │   │
│   │        "project/instructions", "agent/skills")              │   │
│   │ - codec: Schema 编解码器，用于验证和序列化                   │   │
│   │ - load: 加载当前值的效果（可能返回 Unavailable）             │   │
│   │ - baseline: 首次渲染为模型可见文本                           │   │
│   │ - update: 变更时生成更新文本                                 │   │
│   │ - removed: 可选的移除文本生成器                              │   │
│   │                                                             │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │ Context Snapshot (上下文快照)                                │   │
│   │                                                             │   │
│   │ - 可覆盖的模型隐藏 JSON 状态                                 │   │
│   │ - 用于比较每个 Context Source 上次提交的值                   │   │
│   │ - 原子性更新，与 Mid-Conversation System Message 同步       │   │
│   │                                                             │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │ Context Epoch (上下文纪元)                                   │   │
│   │                                                             │   │
│   │ - 一个初始渲染的 System Context 保持不变的时间跨度           │   │
│   │ - 在压缩、会话移动或不兼容的上下文转换时结束                 │   │
│   │ - 每个 Epoch 有不可变的 Baseline System Context              │   │
│   │                                                             │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 Context Source 实现

```python
class ContextSource(ABC, Generic[A]):
    """
    上下文源 - 借鉴 opencode 的 Source<A> 设计

    每个 Context Source 是独立可刷新的类型化值
    """

    @property
    @abstractmethod
    def key(self) -> str:
        """稳定的命名空间标识符，如 'date/current'"""
        pass

    @abstractmethod
    async def load(self) -> A | Unavailable:
        """
        加载当前值
        返回 Unavailable 表示临时无法观察，保留上次状态
        """
        pass

    @abstractmethod
    def baseline(self, value: A) -> str:
        """首次渲染为模型可见文本"""
        pass

    @abstractmethod
    def update(self, previous: A, current: A) -> str:
        """变更时生成更新文本"""
        pass

    def removed(self, previous: A) -> str | None:
        """可选的移除文本生成器"""
        return None

class DateContextSource(ContextSource[DateValue]):
    """日期上下文源 - 示例"""

    key = "date/current"

    async def load(self) -> DateValue | Unavailable:
        now = datetime.now()
        return DateValue(
            date=now.strftime("%Y-%m-%d"),
            timezone=str(now.astimezone().tzinfo),
        )

    def baseline(self, value: DateValue) -> str:
        return f"Current date: {value.date} ({value.timezone})"

    def update(self, previous: DateValue, current: DateValue) -> str:
        return f"Date changed from {previous.date} to {current.date}"

class ProjectInstructionsContextSource(ContextSource[InstructionsValue]):
    """项目指令上下文源 - 发现 AGENTS.md / CLAUDE.md 等"""

    key = "project/instructions"

    def __init__(self, project_path: Path):
        self.project_path = project_path

    async def load(self) -> InstructionsValue | Unavailable:
        instructions = []
        # 发现全局和项目级的 AGENTS.md
        for pattern in ["AGENTS.md", ".claude/CLAUDE.md", ".cursorrules"]:
            for file in self.project_path.rglob(pattern):
                if file.exists():
                    instructions.append(Instruction(
                        source=str(file),
                        content=file.read_text(encoding="utf-8"),
                    ))

        if not instructions:
            return Unavailable("No instruction files found")

        return InstructionsValue(instructions=instructions)

    def baseline(self, value: InstructionsValue) -> str:
        parts = ["## Project Instructions\n"]
        for inst in value.instructions:
            parts.append(f"### From: {inst.source}\n{inst.content}\n")
        return "\n".join(parts)

    def update(self, previous: InstructionsValue, current: InstructionsValue) -> str:
        return f"Project instructions updated. {len(current.instructions)} instruction files active."

class AgentSkillsContextSource(ContextSource[SkillsValue]):
    """Agent 可用技能上下文源 - 实现于 context/source.py"""

    def __init__(self, skills: list[SkillInfo] | None = None):
        # 实际实现直接持有技能列表（setup 时从 SkillRegistry.discover
        # 一次性灌入）；按 Agent 权限的动态刷新为后续计划
        self._skills = skills or []

    @property
    def key(self) -> str:
        return "agent/skills"

    async def load(self) -> SkillsValue | Unavailable:
        if not self._skills:
            return Unavailable("No skills available")
        return SkillsValue(skills=self._skills)

    def baseline(self, value: SkillsValue) -> str:
        parts = ["## Available Skills"]
        for skill in value.skills:
            slash = f"/" if skill.slash else ""
            parts.append(f"- **{slash}{skill.name}**: {skill.description or 'No description'}")
        return "\n".join(parts)
```

### 3.3 System Context Registry

```python
class SystemContextRegistry:
    """
    系统上下文注册表 - 借鉴 opencode 的 System Context Registry

    管理有序、有作用域的上下文贡献者
    """

    def __init__(self):
        self._sources: dict[str, ContextSource] = {}
        self._order: list[str] = []

    def register(self, source: ContextSource):
        """注册上下文源"""
        if source.key in self._sources:
            raise DuplicateKeyError(f"重复的上下文源 key: {source.key}")
        self._sources[source.key] = source
        self._order.append(source.key)

    def unregister(self, key: str):
        """注销上下文源"""
        self._sources.pop(key, None)
        if key in self._order:
            self._order.remove(key)

    async def initialize(self) -> Generation:
        """
        初始化 System Context（实际实现）

        生成基线和快照。**Unavailable 的源被跳过**（不贡献基线也不进快照），
        而不是抛 InitializationBlocked —— 否则任何可选源（如项目指令文件）
        缺失都会让整个上下文系统回退。InitializationBlocked 异常类仍保留
        但当前初始化路径不使用。
        """
        baseline_parts = []
        snapshot = Snapshot()

        for key in self._order:
            source = self._sources[key]
            value = await source.load()

            if isinstance(value, Unavailable):
                continue  # 跳过，不贡献基线

            baseline_parts.append(source.baseline(value))
            snapshot.entries[key] = SourceSnapshot(value=value)

        return Generation(
            baseline="\n\n".join(baseline_parts),
            snapshot=snapshot,
        )

    async def reconcile(
        self,
        snapshot: Snapshot,
    ) -> ReconcileResult:
        """
        协调上下文 - 比较当前值与快照（实际实现）

        实际实现只返回两种结果：
        - Unchanged: 无变化
        - Updated: 有更新（新注册源贡献 baseline 文本；已变更源贡献
          update 文本），携带合并后的新快照

        ReplacementReady / ReplacementBlocked 结果类型已定义但当前
        reconcile 不返回（replace() 路径使用独立的 ReplacementResult
        体系，见 Module 03）。
        """
        updates = []
        new_snapshot = {}
        has_changes = False

        for key in self._order:
            source = self._sources[key]
            value = await source.load()

            if isinstance(value, Unavailable):
                # 不可用，保留上次状态
                new_snapshot[key] = snapshot.entries.get(key)
                continue

            old_entry = snapshot.entries.get(key)

            if old_entry is None:
                # 新注册的源
                updates.append(source.baseline(value))
                new_snapshot[key] = SourceSnapshot(value=value)
                has_changes = True
            elif old_entry.value != value:
                # 值变更
                updates.append(source.update(old_entry.value, value))
                new_snapshot[key] = SourceSnapshot(value=value)
                has_changes = True
            else:
                new_snapshot[key] = old_entry

        if not has_changes:
            return ReconcileResult(tag="Unchanged")

        return ReconcileResult(
            tag="Updated",
            text="\n".join(updates),
            snapshot=Snapshot(entries=new_snapshot),
        )
```

---

## 4. Tool System v2（类型安全工具）

### 4.1 设计理念（借鉴 opencode — 实际实现）

实际实现（[agent/tools.py](file:///f:/MyCoding/sdpost-claw/src/sdpost_claw/agent/tools.py)）与初版设计的差异：

- 输入 Schema 使用 **JSON Schema dict**（OpenAI function calling 格式直接可用），而非 Pydantic model
- **没有独立的输出 Schema 验证**；输出经 `str()` 后做大小截断（`truncate_text` 保留头尾）
- 新增 `finalize_content` 回调：同步、仅内容的重写，在管道所有归一化（截断）完成后执行一次（dsh `finalizeContent`）
- `to_dict()` 直接产出 OpenAI 兼容的 tool dict

```python
@dataclass
class ToolDefinition:
    """工具定义 - 类型安全工具（实际实现）"""

    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema
    execute_fn: Callable[[dict, ToolContext], Awaitable[Any]]
    permission: str | None = None
    max_output_chars: int = 2000
    # 可选的同步内容重写，由工具管道在截断之后执行（默认 None，向后兼容）
    finalize_content: Callable[[str], str] | None = None

    async def execute(self, input_data: dict, context: ToolContext) -> ToolResult:
        """执行工具（计时 + 异常统一封装为 is_error 的 ToolResult）"""
        output = await self.execute_fn(input_data, context)
        model_output = self._format_for_model(output)  # 大小限制 + 截断
        return ToolResult(...)

    def to_dict(self) -> dict:
        """OpenAI 兼容工具 dict"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }
```

`ToolResult` 字段：`tool_call_id / name / content / structured_output / externalized_path / is_truncated / is_error / duration_ms`。`ToolContext` 字段：`session_id / agent_id / assistant_message_id / tool_call_id / cwd / max_output_chars`。

### 4.2 内置工具集（实际实现：8 个工具）

实际实现于 [agent/tools.py](file:///f:/MyCoding/sdpost-claw/src/sdpost_claw/agent/tools.py) `BuiltInTools.register_all(registry, cwd)`：

```python
class BuiltInTools:
    """内置工具集 - 实际注册的 8 个工具"""

    @staticmethod
    def register_all(registry: ToolRegistry, cwd: str = "") -> None:
        # === 文件操作 ===
        registry.register(ToolDefinition(
            name="read",      # 读取文件（offset/limit 分页，带行号）
            permission="file.read",
        ))
        registry.register(ToolDefinition(
            name="write",     # 写文件（自动创建父目录）
            permission="file.write",
        ))
        registry.register(ToolDefinition(
            name="edit",      # 精确字符串替换（仅第一处）
            permission="file.edit",
        ))
        registry.register(ToolDefinition(
            name="glob",      # glob 模式找文件
            permission="file.read",
        ))
        registry.register(ToolDefinition(
            name="grep",      # regex 搜内容（结果限 100 条）
            permission="file.read",
        ))

        # === Shell 执行 ===
        registry.register(ToolDefinition(
            name="bash",      # shell 命令（默认 timeout 60s）
            permission="shell.execute",
            max_output_chars=4000,
        ))

        # === 网络请求 ===
        registry.register(ToolDefinition(
            name="webfetch",  # 抓取网页（默认 max_chars 5000）
            permission="network.request",
            max_output_chars=8000,
        ))

        # === 用户交互 ===
        registry.register(ToolDefinition(
            name="question",  # 向用户提问（由 UI 层特殊处理）
            permission="user.interact",
        ))
```

> **未实现的规划工具**：`websearch`（网络搜索）、`task`（子 Agent 派生）、`skill`（技能执行）仍在设计中，代码尚未注册。相关权限动作（`network.search`、`agent.spawn`、`skill.execute`）已预留于权限体系。

---

## 5. Permission System v2（通配符规则）

### 5.1 设计理念（借鉴 opencode）

```python
class PermissionRule:
    """
    权限规则 - 借鉴 opencode 的 Wildcard Ruleset

    支持通配符匹配:
    - "file.*" 匹配所有文件操作
    - "file.read.*" 匹配所有读取操作
    - "*" 匹配所有
    """

    def __init__(
        self,
        action: str,      # 如 "file.read", "shell.execute", "*"
        effect: str,      # "allow" | "deny"
        priority: int = 0,  # 数字越大优先级越高
    ):
        self.action = action
        self.effect = effect
        self.priority = priority
        self._pattern = action.replace("*", ".*")

    def matches(self, action: str) -> bool:
        """检查动作是否匹配此规则"""
        return bool(re.match(f"^{self._pattern}$", action))

class PermissionRuleset:
    """权限规则集"""

    def __init__(self):
        self._rules: list[PermissionRule] = []

    def allow(self, action: str, priority: int = 0):
        """添加允许规则"""
        self._rules.append(PermissionRule(action, "allow", priority))

    def deny(self, action: str, priority: int = 0):
        """添加拒绝规则"""
        self._rules.append(PermissionRule(action, "deny", priority))

    def evaluate(self, action: str) -> PermissionDecision:
        """
        评估权限（实际实现于 agent/permissions.py）

        策略：**最高优先级的匹配规则生效；同优先级时最后匹配的规则生效**
        （opencode findLast 策略 + 优先级扩展）。
        """
        matching = [r for r in self._rules if r.matches(action)]
        if not matching:
            return PermissionDecision(effect="ask", rule=None)  # 无规则 → ask

        # 按 (priority, 规则位置) 降序排序 → 最高优先级、位置最靠后者胜出
        matching.sort(key=lambda r: (r.priority, self._rules.index(r)), reverse=True)
        winner = matching[0]

        return PermissionDecision(effect=winner.effect, rule=winner)

    def is_allowed(self, action: str) -> bool: ...
    def is_denied(self, action: str) -> bool: ...
    def remove_rule(self, action, effect=None) -> None: ...  # 规则移除
```

通配符实现细节：`PermissionRule.__post_init__` 对 action 做 `re.escape` 后再把 `\*` 还原为 `.*`，因此 `file.*`、`shell.*`、`*` 等模式中的 `.` 等元字符不会被误当通配符。

> **自动化模式的 ask 处理**：三阶段工具管道（harness/tool_pipeline.py）中，`ask` 决策在无人工介入的自动化执行路径上**自动拒绝**（`permission requires confirmation (auto-denied)`），与既有行为保持一致。

class AgentPermissions:
    """Agent 权限预设 - 实际实现（build/plan/general）"""

    @staticmethod
    def build() -> PermissionRuleset:
        """build agent - 完全访问权限"""
        ruleset = PermissionRuleset()
        ruleset.allow("*")
        return ruleset

    @staticmethod
    def plan() -> PermissionRuleset:
        """plan agent - 只读权限"""
        ruleset = PermissionRuleset()
        ruleset.allow("file.read", priority=5)
        ruleset.allow("file.list", priority=5)
        ruleset.allow("network.*", priority=5)
        ruleset.deny("file.write", priority=10)
        ruleset.deny("file.edit", priority=10)
        ruleset.deny("shell.*", priority=10)
        ruleset.deny("agent.spawn", priority=10)
        return ruleset

    @staticmethod
    def general() -> PermissionRuleset:
        """general agent - 标准访问，拒绝危险 shell 操作"""
        ruleset = PermissionRuleset()
        ruleset.allow("*", priority=5)
        ruleset.deny("shell.rm", priority=10)
        return ruleset

    @staticmethod
    def custom(allow: list[str], deny: list[str]) -> PermissionRuleset:
        """自定义权限"""
        ruleset = PermissionRuleset()
        for action in deny:
            ruleset.deny(action, priority=10)  # 拒绝优先级更高
        for action in allow:
            ruleset.allow(action, priority=5)
        return ruleset
```

模式切换实际生效路径：终端 REPL 的 `mode <build|plan|general>` 命令会**重建 PermissionRuleset 并热替换** `SessionRunner.permission_ruleset`（见 main.py `_ruleset_for_mode`），保证模式切换即时生效。

---

## 6. Compaction v2（结构化压缩）

### 6.1 设计理念（借鉴 opencode 的 SUMMARY_TEMPLATE）

```python
COMPACTION_TEMPLATE = """Output exactly the Markdown structure shown inside <template> and keep the section order unchanged. Do not include the <template> tags in your response.
<template>
## Objective
- [one or two brief sentences describing what the user is trying to accomplish]

## Important Details
- [constraints/preferences, decisions and why, important facts/assumptions, exact context needed to continue, or "(none)"]

## Work State
### Completed
- [finished work, verified facts, or changes made; otherwise "(none)"]

### Active
- [current work, partial changes, or investigation state; otherwise "(none)"]

### Blocked
- [blockers, failing commands, or unknowns; otherwise "(none)"]

## Next Move
1. [immediate concrete action, or "(none)"]
2. [next action if known, or "(none)"]

## Relevant Files
- [file or directory path: why it matters, or "(none)"]
</template>

Rules:
- Keep every section, even when empty.
- Use terse bullets, not prose paragraphs.
- Preserve exact file paths, symbols, commands, error strings, URLs, and identifiers when known.
- Do not mention the summary process or that context was compacted."""

COMPACTION_UPDATE_INSTRUCTIONS = """The <prior-summary> summarizes everything that happened before the <conversation>. Construct a new summary that combines both. The <prior-summary> is discarded after this: anything you do not carry into the new summary is lost.

When combining:
- Carry forward objectives, constraints, user directives, decisions, and parallel workstreams from the <prior-summary> even when the <conversation> does not mention them. Drop only what is finished and no longer needed.
- The <conversation> is more recent than the <prior-summary>. Where they conflict, the conversation wins: state the corrected fact and drop the old claim.
- Add new progress, decisions, constraints, and context from the conversation.
- Move completed work from "Active" to "Completed".
- If a blocker has been resolved, update the summary to reflect that while keeping any details still needed to continue the work.
- Update "Objective" and "Next Move" to reflect the current work state."""

### 6.2 实际实现：无状态 CompactionEngine + 有状态 CompactionBridge

实际实现将"策略"与"协调"分离（[context/compaction.py](file:///f:/MyCoding/sdpost-claw/src/sdpost_claw/context/compaction.py) + [harness/compaction_bridge.py](file:///f:/MyCoding/sdpost-claw/src/sdpost_claw/harness/compaction_bridge.py)）：

```python
class CompactionEngine:
    """压缩引擎 - 无状态策略（context/compaction.py）"""

    def __init__(self, config: CompactionConfig):
        self.config = config
        self.buffer_tokens = config.buffer_tokens  # 默认 20000
        self.keep_tokens = config.keep_tokens      # 默认 8000

    def should_compact(self, total_tokens: int) -> bool:
        """total_tokens > max_tokens - buffer_tokens 时触发"""
        if not self.config.enabled:
            return False
        return total_tokens > (self.config.max_tokens - self.buffer_tokens)

    def build_compaction_prompt(
        self, messages: list[dict], prior_summary: str | None = None,
    ) -> tuple[str, str]:
        """返回 (system_prompt, user_content)。
        有 prior_summary 时走增量模板（TEMPLATE + UPDATE_INSTRUCTIONS，
        <prior-summary> + <conversation> 包裹）；否则走首次压缩模板。"""

    def count_tokens(self, text: str) -> int:
        """近似 token 估算：len(text) // 3"""
```

```python
class CompactionBridge:
    """有状态协调器 - 连接 CompactionEngine 与驱动循环（harness/compaction_bridge.py）"""

    def __init__(self, engine, provider=None):
        self.engine = engine
        self.provider = provider            # 复用现有单一 provider（央企国产-only）
        self._last_compaction_tokens = 0    # 重触发保护状态

    async def maybe_compact(self, session) -> bool:
        """每步压力检查（在 SessionRunner.run 内调用，位于边界 prepare 之后、
        模型调用之前）：
        1. 从 SessionLog.derive_messages() 取历史并估算 token 压力
        2. 超阈值且通过重触发保护 → build_compaction_prompt
        3. 经现有单一 provider 生成摘要 → session.summary
        4. emit ignorable 的 compaction_occurred 事件（不入衍生消息历史）
        任何失败都返回 False —— 压缩绝不能打断当前 turn。
        """
```

**摘要如何到达模型**：`session.summary` 更新后，调用方同步 `SummaryContextSource.update_summary()`。该源在 `initialize` 时因摘要为空而 `Unavailable`（不在快照中），下一 turn 的 `reconcile` 将其视为**新注册源**返回 `Updated`（携带 `## Previous Session Summary`），经 Phase 4 的 inbox 注入路径在 step 边界折叠进 system context —— **无需 baseline replace**。物理替换衍生历史中段的 `SessionLog.replace(start, end)` 表面操作（dsh `SurfaceOp.replace`）留待后续阶段。

**重触发保护**：因为摘要注入不缩小衍生历史，裸阈值检查会每步重触发；Bridge 记住上次压缩时的 token 水位，只有对话增长超过一个 buffer 后才允许再次压缩。策略（CompactionEngine）保持无状态。

---

## 7. 数据模型

```python
@dataclass
class Generation:
    """上下文生成 - 借鉴 opencode"""
    baseline: str
    snapshot: Snapshot

@dataclass
class Snapshot:
    """上下文快照"""
    entries: dict[str, SourceSnapshot]

@dataclass
class SourceSnapshot:
    """单个源的快照"""
    value: Any
    removed: str | None = None

@dataclass
class MidConversationSystemMessage:
    """对话中系统消息 - 借鉴 opencode"""
    text: str
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class PreparedTurn:
    """准备好的模型调用（实际实现）"""
    system_context: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    context_update: str | None = None  # 保留字段；实际注入已在 prepare 内折叠
    admitted: list[Prompt] = field(default_factory=list)
    settled: list[ToolResult] = field(default_factory=list)

    def has_work(self) -> bool:
        return bool(self.admitted or self.settled)

@dataclass
class DrainResult:
    """Drain 执行结果（实际实现）"""
    status: str = "no_work"  # no_work | tool_execution | text_response | error
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    error: str | None = None
```

**Session 实体**（实际实现于 drain.py，持久化结构见 Module 02 的 `SessionStore`）：

```python
class Session:
    """会话持久化实体 - agent/drain.py"""
    id / cwd / title / agent_mode / status / created_at / updated_at
    history: list[dict]                    # 会话历史（Phase 1 双写保留）
    log: SessionLog                        # append-only 事件日志（未来唯一事实源）
    inbox: Inbox                           # next_turn prompts + next_step 注入
    pending_tool_results: list[ToolResult] # 待结算工具结果
    context_snapshot: dict                 # 上下文快照
    baseline_system_context: str
    token_count: int
    summary: str                           # Phase 5 压缩摘要（由 SummaryContextSource 呈现）

    def submit_prompt(text) -> Prompt      # 入 inbox.next_turn 队列
    def drain_injections() -> list[Inject] # step 边界领取注入（记录 context_injection 事件）
    def has_pending_tool_calls() -> bool
    def add_tool_result(result) -> None
    def _emit(type, data, ignorable)       # 双写辅助：镜像写事件日志
```

---

## 8. 与其他模块的接口

| 接口 | 方向 | 说明 |
|------|------|------|
| `ModelProvider` | 依赖 | 模型调用抽象层（runtime/providers.py，仅 OpenAI 兼容实现） |
| `SessionStore` / `SessionManager` | 依赖 | 会话持久化（runtime/session.py） |
| `PermissionRuleset` | 依赖 | 权限检查（agent/permissions.py） |
| `SystemContextRegistry` | 依赖 | 每步 reconcile（Phase 4，注入协调器） |
| `CompactionBridge` / `SummaryContextSource` | 依赖 | 每步压缩压力检查（Phase 5，注入协调器） |
| `SessionDriver` | 协作 | turn/step 生命周期（harness/driver.py，由应用层驱动） |
| `AuditLog` | 输出 | 审计日志记录（production/audit.py） |
| `EventBus` | 输出 | 进程内事件发布（common/events.py） |
| `JSONLTranscript` | 输出 | JSONL 事件溯源（runtime/transcript.py，应用层写入） |

---

## 9. 实现状态（v2.1 更新）

| 能力 | 状态 | 实现位置 |
|------|------|----------|
| Session Drain + Provider-Turn Boundary | ✅ 已实现 | agent/drain.py |
| Prompt Promotion（Inbox next_turn） | ✅ 已实现 | agent/drain.py + harness/inbox.py |
| Harness turn/step 状态机 + 软停止钩子 | ✅ 已实现 | harness/driver.py |
| Append-only SessionLog + derive_messages | ✅ 已实现 | harness/session_log.py、harness/events.py |
| 三阶段工具管道（pre/execute/post + finalize） | ✅ 已实现 | harness/tool_pipeline.py |
| Type-safe Tool Registry（JSON Schema） | ✅ 已实现 | agent/tools.py |
| 内置工具 8 个 | ✅ 已实现 | agent/tools.py BuiltInTools |
| Wildcard Permission Ruleset（优先级 + last-match） | ✅ 已实现 | agent/permissions.py |
| Agent Modes（build/plan/general）默认权限 | ✅ 已实现 | agent/modes.py、agent/permissions.py |
| 无状态 CompactionEngine + CompactionBridge | ✅ 已实现 | context/compaction.py、harness/compaction_bridge.py |
| Non-waking context 注入（Phase 4） | ✅ 已实现 | harness/inbox.py + agent/drain.py |
| websearch / task / skill 工具 | ⏳ 规划中 | 未注册 |
| SessionLog SurfaceOp.replace（物理压缩历史） | ⏳ 规划中 | 摘要目前经注入路径呈现 |
| ask 决策的人工确认交互 | ⏳ 规划中 | 自动化路径目前自动拒绝 |

---

*文档版本: v2.1 | 创建日期: 2026-08-27 | 更新日期: 2026-09-03 | 基于 opencode + deepseek-harness 架构，已与实现代码对齐*
