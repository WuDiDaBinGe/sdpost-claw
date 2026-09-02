# Module 01: Agent Core v2 — 智能体核心引擎（opencode 增强版）

## 1. 设计演进说明

本模块在初版设计基础上，吸收了 opencode 的以下优秀设计：

| 初版设计 | opencode 启发 | 改进 |
|----------|--------------|------|
| 简单 Agent Loop | Session Drain + Provider-Turn Boundary | 更清晰的执行边界和状态管理 |
| 基础 Tool Dispatch | Schema-validated Tool Definition | 类型安全的工具输入/输出验证 |
| 简单权限检查 | Wildcard Permission Ruleset | 灵活的模式匹配权限 |
| 基础压缩 | Structured Compaction Template | 高质量的结构化摘要 |

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

### 2.2 Safe Provider-Turn Boundary（安全模型调用边界）

```python
class SafeProviderTurnBoundary:
    """
    安全模型调用边界 - 借鉴 opencode 设计

    在每次模型调用之前，确保：
    1. 所有符合条件的用户输入已推进到 Session History
    2. 所有已完成的工具结果已结算
    3. 上下文变更可以安全地按时间顺序纳入
    4. 不会在模型调用过程中异步推送上下文变更
    """

    async def prepare(
        self,
        session: Session,
        system_context: SystemContext,
    ) -> PreparedTurn:
        """准备一次安全的模型调用"""

        # 1. 推进符合条件的用户输入
        admitted = await self._promote_pending_input(session)

        # 2. 结算已完成的工具结果
        settled = await self._settle_tool_results(session)

        # 3. 在安全边界纳入上下文变更
        context_update = await self._reconcile_context(session, system_context)

        # 4. 组装最终的模型调用请求
        return PreparedTurn(
            system_context=system_context.baseline,
            messages=await self._assemble_history(session),
            tools=await self._get_available_tools(session),
            context_update=context_update,  # Mid-Conversation System Message
            admitted=admitted,
            settled=settled,
        )

    async def _reconcile_context(
        self,
        session: Session,
        system_context: SystemContext,
    ) -> MidConversationSystemMessage | None:
        """
        协调上下文变更 - 借鉴 opencode 的 Context Reconciliation

        返回以下之一：
        - Unchanged: 上下文无变化
        - Updated: 有更新，生成 Mid-Conversation System Message
        - ReplacementReady: 需要替换基线（如压缩后）
        - ReplacementBlocked: 替换被阻塞（有不可用上下文）
        """
        snapshot = await session.get_context_snapshot()
        result = await system_context.reconcile(snapshot)

        if result.tag == "Unchanged":
            return None
        elif result.tag == "Updated":
            return MidConversationSystemMessage(text=result.text)
        elif result.tag == "ReplacementReady":
            await session.replace_baseline(result.generation)
            return None
        elif result.tag == "ReplacementBlocked":
            # 有不可用的上下文源，阻塞本次调用
            raise ContextUnavailableError(result.unavailable_keys)
```

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
    """Agent 可用技能上下文源"""

    key = "agent/skills"

    def __init__(self, agent: Agent, skill_registry: SkillRegistry):
        self.agent = agent
        self.registry = skill_registry

    async def load(self) -> SkillsValue | Unavailable:
        # 只列出该 Agent 有权使用的技能名称和描述
        available = self.registry.get_available_for_agent(self.agent.id)
        if not available:
            return Unavailable("No skills available")
        return SkillsValue(skills=available)

    def baseline(self, value: SkillsValue) -> str:
        parts = ["## Available Skills\n"]
        for skill in value.skills:
            parts.append(f"- **{skill.name}**: {skill.description}")
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
        初始化 System Context
        生成基线和快照
        """
        baseline_parts = []
        snapshot = {}

        for key in self._order:
            source = self._sources[key]
            value = await source.load()

            if isinstance(value, Unavailable):
                raise InitializationBlocked(unavailable_keys=[key])

            baseline_parts.append(source.baseline(value))
            snapshot[key] = SourceSnapshot(
                value=value,
                removed=None,
            )

        return Generation(
            baseline="\n\n".join(baseline_parts),
            snapshot=Snapshot(entries=snapshot),
        )

    async def reconcile(
        self,
        snapshot: Snapshot,
    ) -> ReconcileResult:
        """
        协调上下文 - 比较当前值与快照

        返回:
        - Unchanged: 无变化
        - Updated: 有更新
        - ReplacementReady: 需要替换基线
        - ReplacementBlocked: 替换被阻塞
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

### 4.1 设计理念（借鉴 opencode）

```python
class ToolDefinition(Generic[Input, Output]):
    """
    工具定义 - 借鉴 opencode 的 type-safe tool design

    特性:
    - 输入/输出 Schema 验证
    - 结构化输出与模型可见输出分离
    - 权限集成
    - 输出大小限制
    """

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: Type[Input],  # Pydantic model
        output_schema: Type[Output],
        structured_schema: Type[StructuredOutput] | None = None,
        execute: Callable[[Input, ToolContext], Awaitable[Output]] = None,
        permission: str | None = None,
        max_output_chars: int = 2000,
    ):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.output_schema = output_schema
        self.structured_schema = structured_schema or output_schema
        self.execute_fn = execute
        self.permission = permission
        self.max_output_chars = max_output_chars

    async def execute(self, input_data: dict, context: ToolContext) -> ToolResult:
        """执行工具"""
        # 1. 验证输入
        validated = self.input_schema(**input_data)

        # 2. 执行
        output = await self.execute_fn(validated, context)

        # 3. 验证输出
        validated_output = self.output_schema(**output) if isinstance(output, dict) else output

        # 4. 生成模型可见输出（可能截断）
        model_output = self._format_for_model(validated_output)

        # 5. 生成结构化输出（完整）
        structured = None
        if self.structured_schema != self.output_schema:
            structured = self._to_structured(validated_output)

        return ToolResult(
            tool_call_id=context.tool_call_id,
            name=self.name,
            content=model_output.text,
            structured_output=structured,
            externalized_path=model_output.externalized_path,
            is_truncated=model_output.is_truncated,
        )

    def _format_for_model(self, output: Output) -> ModelOutput:
        """格式化为模型可见输出，带大小限制"""
        text = str(output)
        if len(text) <= self.max_output_chars:
            return ModelOutput(text=text, is_truncated=False)

        # 截断：保留头尾
        head_chars = self.max_output_chars // 2 - 100
        tail_chars = self.max_output_chars // 2 - 100
        truncated = text[:head_chars] + "\n... (truncated) ...\n" + text[-tail_chars:]

        # 完整输出外部化
        externalized_path = await self._save_full_output(text)

        return ModelOutput(
            text=truncated,
            externalized_path=externalized_path,
            is_truncated=True,
        )
```

### 4.2 内置工具集（借鉴 opencode 的工具分类）

```python
class BuiltInTools:
    """内置工具集 - 参考 opencode 的工具分类"""

    @staticmethod
    def register_all(registry: ToolRegistry):
        # === 文件操作 ===
        registry.register(ToolDefinition(
            name="read",
            description="Read file contents",
            input_schema=ReadInput,
            output_schema=ReadOutput,
            permission="file.read",
        ))

        registry.register(ToolDefinition(
            name="write",
            description="Write file contents",
            input_schema=WriteInput,
            output_schema=WriteOutput,
            permission="file.write",
        ))

        registry.register(ToolDefinition(
            name="edit",
            description="Edit file with exact string replacement",
            input_schema=EditInput,
            output_schema=EditOutput,
            permission="file.edit",
        ))

        registry.register(ToolDefinition(
            name="glob",
            description="Find files by glob pattern",
            input_schema=GlobInput,
            output_schema=GlobOutput,
            permission="file.read",
        ))

        registry.register(ToolDefinition(
            name="grep",
            description="Search file contents with regex",
            input_schema=GrepInput,
            output_schema=GrepOutput,
            permission="file.read",
        ))

        # === Shell 执行 ===
        registry.register(ToolDefinition(
            name="bash",
            description="Execute shell command",
            input_schema=BashInput,
            output_schema=BashOutput,
            permission="shell.execute",
            max_output_chars=4000,
        ))

        # === 网络请求 ===
        registry.register(ToolDefinition(
            name="webfetch",
            description="Fetch web content",
            input_schema=WebfetchInput,
            output_schema=WebfetchOutput,
            permission="network.request",
        ))

        registry.register(ToolDefinition(
            name="websearch",
            description="Search the web",
            input_schema=WebsearchInput,
            output_schema=WebsearchOutput,
            permission="network.search",
        ))

        # === 子 Agent ===
        registry.register(ToolDefinition(
            name="task",
            description="Spawn a sub-agent for complex tasks",
            input_schema=TaskInput,
            output_schema=TaskOutput,
            permission="agent.spawn",
        ))

        # === 用户交互 ===
        registry.register(ToolDefinition(
            name="question",
            description="Ask the user a question",
            input_schema=QuestionInput,
            output_schema=QuestionOutput,
            permission="user.interact",
        ))

        # === 技能系统 ===
        registry.register(ToolDefinition(
            name="skill",
            description="Execute a skill",
            input_schema=SkillInput,
            output_schema=SkillOutput,
            permission="skill.execute",
        ))
```

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
        评估权限 - 最后匹配的规则生效（Last Match Wins）
        借鉴 opencode 的 findLast 策略
        """
        matching = [r for r in self._rules if r.matches(action)]
        if not matching:
            return PermissionDecision(
                effect="ask",  # 无规则时询问用户
                rule=None,
            )

        # 按优先级排序，返回最高优先级的规则
        matching.sort(key=lambda r: r.priority, reverse=True)
        winner = matching[0]

        return PermissionDecision(
            effect=winner.effect,
            rule=winner,
        )

class AgentPermissions:
    """Agent 权限配置 - 借鉴 opencode 的 build/plan 模式"""

    @staticmethod
    def build() -> PermissionRuleset:
        """build agent - 完全访问权限"""
        ruleset = PermissionRuleset()
        ruleset.allow("*")  # 允许所有
        return ruleset

    @staticmethod
    def plan() -> PermissionRuleset:
        """plan agent - 只读权限"""
        ruleset = PermissionRuleset()
        ruleset.allow("file.read.*")
        ruleset.allow("file.list.*")
        ruleset.allow("network.*")
        ruleset.deny("file.write.*")
        ruleset.deny("file.edit.*")
        ruleset.deny("shell.*")
        ruleset.deny("agent.spawn")
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

class CompactionEngine:
    """压缩引擎 - 借鉴 opencode 的结构化压缩"""

    def __init__(self, config: CompactionConfig):
        self.config = config
        self.buffer_tokens = config.buffer_tokens  # 默认 20000
        self.keep_tokens = config.keep_tokens      # 默认 8000

    async def should_compact(self, session: Session) -> bool:
        """判断是否需要压缩"""
        total_tokens = await session.count_tokens()
        return total_tokens > (self.config.max_tokens - self.buffer_tokens)

    async def compact(self, session: Session) -> CompactionResult:
        """
        执行压缩
        1. 获取当前完整历史
        2. 生成结构化摘要
        3. 创建新的 Context Epoch
        4. 保留摘要作为新的基线
        """
        history = await session.get_full_history()

        # 生成结构化摘要
        summary = await self._generate_summary(history)

        # 创建新的 Context Epoch
        new_epoch = await session.start_new_epoch()

        # 保留摘要
        await session.set_baseline_summary(summary)

        return CompactionResult(
            epoch_id=new_epoch.id,
            summary=summary,
            tokens_before=sum(m.tokens for m in history),
            tokens_after=self._count_tokens(summary),
        )

    async def _generate_summary(self, history: list[Message]) -> str:
        """生成结构化摘要"""
        # 使用轻量模型生成摘要
        response = await self.model.generate(
            system=COMPACTION_TEMPLATE,
            messages=[{
                "role": "user",
                "content": self._format_history_for_compaction(history),
            }],
        )
        return response.text
```

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
    """准备好的模型调用"""
    system_context: str
    messages: list[Message]
    tools: list[ToolDefinition]
    context_update: MidConversationSystemMessage | None
    admitted: list[Prompt]
    settled: list[ToolResult]

class ReconcileResult:
    """协调结果"""
    pass

@dataclass
class Unchanged(ReconcileResult):
    tag: str = "Unchanged"

@dataclass
class Updated(ReconcileResult):
    text: str
    snapshot: Snapshot
    tag: str = "Updated"

@dataclass
class ReplacementReady(ReconcileResult):
    generation: Generation
    tag: str = "ReplacementReady"

@dataclass
class ReplacementBlocked(ReconcileResult):
    unavailable_keys: list[str] = field(default_factory=list)
    tag: str = "ReplacementBlocked"
```

---

## 8. 与其他模块的接口

| 接口 | 方向 | 说明 |
|------|------|------|
| `ModelProvider` | 依赖 | 模型调用抽象层 |
| `SessionStore` | 依赖 | 会话持久化 |
| `PermissionRuleset` | 依赖 | 权限检查 |
| `AuditLog` | 输出 | 审计日志记录 |
| `EventBus` | 输出 | 事件发布 |

---

## 9. 实现计划

| 阶段 | 内容 | 产出 |
|------|------|------|
| Phase 1 | System Context 基础架构 | 上下文源可注册、可刷新 |
| Phase 2 | Session Drain + Provider Turn | 清晰的执行边界 |
| Phase 3 | Type-safe Tool System | Schema 验证的工具 |
| Phase 4 | Permission Ruleset | 通配符权限 |
| Phase 5 | Structured Compaction | 高质量压缩 |

---

*文档版本: v2.0 | 创建日期: 2026-08-27 | 基于 opencode 架构优化*
