# Module 03: Memory & Context v2 — 记忆与上下文系统（opencode 增强版）

## 1. 设计演进说明

本模块是本次升级的核心模块，全面借鉴 opencode 的上下文管理系统：

| 初版设计 | opencode 启发 | 改进 |
|----------|--------------|------|
| 简单 Prompt 组装 | System Context Registry | 可组合、可刷新的上下文源 |
| 基础压缩 | Structured Compaction Template | 高质量结构化摘要 |
| 简单记忆分层 | Context Epoch + Snapshot | 上下文版本管理 |
| 无状态变更机制 | Mid-Conversation System Message | 对话中状态变更指令 |

---

## 2. System Context 架构（核心改进）

### 2.1 设计理念

```
┌─────────────────────────────────────────────────────────────────────┐
│                 System Context Architecture                           │
│                 (借鉴 opencode CONTEXT.md)                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                    Context Source (上下文源)                 │   │
│   │                                                             │   │
│   │   key: 稳定命名空间标识符                                     │   │
│   │        格式: "domain/subdomain[/qualifier]"                  │   │
│   │        示例: "date/current", "project/instructions",        │   │
│   │              "agent/skills", "workspace/memory"             │   │
│   │                                                             │   │
│   │   codec: Schema 编解码器                                     │   │
│   │   load: 加载当前值 (可能返回 Unavailable)                     │   │
│   │   baseline: 首次渲染为模型可见文本                            │   │
│   │   update: 变更时生成更新文本                                  │   │
│   │   removed: 可选移除文本生成器                                 │   │
│   │                                                             │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                    System Context (系统上下文)               │   │
│   │                                                             │   │
│   │   不透明载体，组合多个 Context Source                         │   │
│   │   支持顺序组合，拒绝重复 key                                  │   │
│   │                                                             │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                    Context Epoch (上下文纪元)                │   │
│   │                                                             │   │
│   │   - 初始渲染的 System Context 保持不变的时间跨度             │   │
│   │   - 在压缩、会话迁移或不兼容转换时结束                        │   │
│   │   - 每个 Epoch 有不可变的 Baseline System Context            │   │
│   │                                                             │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                    Context Snapshot (上下文快照)             │   │
│   │                                                             │   │
│   │   - 可覆盖的模型隐藏 JSON 状态                                │   │
│   │   - 用于比较每个 Context Source 上次提交的值                  │   │
│   │   - 原子性更新，与 Mid-Conversation System Message 同步     │   │
│   │                                                             │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Context Source 接口定义

```python
class ContextSource(ABC, Generic[A]):
    """
    上下文源 - 借鉴 opencode 的 Source<A> 设计

    每个 Context Source 是独立可刷新的类型化值
    """

    @property
    @abstractmethod
    def key(self) -> str:
        """
        稳定的命名空间标识符
        格式: "domain/subdomain[/qualifier]"
        示例: "date/current", "project/instructions"
        """
        pass

    @abstractmethod
    async def load(self) -> A | Unavailable:
        """
        加载当前值
        返回 Unavailable 表示临时无法观察，保留上次状态
        与移除不同：刷新保留快照，替换等待
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
        - Updated: 有更新，生成 Mid-Conversation System Message
        - ReplacementReady: 需要替换基线（如压缩后）
        - ReplacementBlocked: 替换被阻塞（有不可用上下文）
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

    async def replace(
        self,
        snapshot: Snapshot,
    ) -> ReplacementResult:
        """
        完全替换 System Context
        用于压缩后或会话迁移
        """
        has_unavailable = False
        baseline_parts = []
        new_snapshot = {}

        for key in self._order:
            source = self._sources[key]
            value = await source.load()

            if isinstance(value, Unavailable):
                # 检查是否有已提交的快照
                if key in snapshot.entries:
                    has_unavailable = True
                    break
                continue

            baseline_parts.append(source.baseline(value))
            new_snapshot[key] = SourceSnapshot(value=value)

        if has_unavailable:
            return ReplacementResult(tag="ReplacementBlocked")

        return ReplacementResult(
            tag="ReplacementReady",
            generation=Generation(
                baseline="\n\n".join(baseline_parts),
                snapshot=Snapshot(entries=new_snapshot),
            ),
        )
```

### 2.3 内置 Context Sources

```python
class DateContextSource(ContextSource[DateValue]):
    """日期上下文源"""

    key = "date/current"

    async def load(self) -> DateValue | Unavailable:
        now = datetime.now()
        return DateValue(
            date=now.strftime("%Y-%m-%d"),
            time=now.strftime("%H:%M:%S"),
            timezone=str(now.astimezone().tzinfo),
            weekday=now.strftime("%A"),
        )

    def baseline(self, value: DateValue) -> str:
        return (
            f"## Current Date & Time\n"
            f"Date: {value.date} ({value.weekday})\n"
            f"Time: {value.time} ({value.timezone})"
        )

    def update(self, previous: DateValue, current: DateValue) -> str:
        return f"Date changed: {previous.date} → {current.date}"

class ProjectInstructionsContextSource(ContextSource[InstructionsValue]):
    """项目指令上下文源 - 发现 AGENTS.md / CLAUDE.md / .cursorrules 等"""

    key = "project/instructions"

    INSTRUCTION_FILES = [
        "AGENTS.md",
        "CLAUDE.md",
        ".cursorrules",
        ".claude/CLAUDE.md",
        ".github/copilot-instructions.md",
    ]

    def __init__(self, project_path: Path):
        self.project_path = project_path

    async def load(self) -> InstructionsValue | Unavailable:
        instructions = []

        for pattern in self.INSTRUCTION_FILES:
            file_path = self.project_path / pattern
            if file_path.exists():
                instructions.append(Instruction(
                    source=str(file_path),
                    content=file_path.read_text(encoding="utf-8"),
                ))

        if not instructions:
            return Unavailable("No instruction files found")

        return InstructionsValue(instructions=instructions)

    def baseline(self, value: InstructionsValue) -> str:
        parts = ["## Project Instructions"]
        for inst in value.instructions:
            parts.append(f"\n### From: {inst.source}\n{inst.content}")
        return "\n".join(parts)

    def update(self, previous: InstructionsValue, current: InstructionsValue) -> str:
        return (
            f"Project instructions updated: "
            f"{len(previous.instructions)} → {len(current.instructions)} files"
        )

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
        parts = ["## Available Skills"]
        for skill in value.skills:
            parts.append(f"- **{skill.name}**: {skill.description}")
        return "\n".join(parts)

    def update(self, previous: SkillsValue, current: SkillsValue) -> str:
        return f"Skills updated: {len(previous.skills)} → {len(current.skills)} available"

class WorkspaceMemoryContextSource(ContextSource[MemoryValue]):
    """工作区记忆上下文源"""

    key = "workspace/memory"

    def __init__(self, memory_store: MemoryStore, workspace_id: str):
        self.memory_store = memory_store
        self.workspace_id = workspace_id

    async def load(self) -> MemoryValue | Unavailable:
        memory = await self.memory_store.get_workspace_memory(self.workspace_id)
        if not memory:
            return Unavailable("No workspace memory")
        return MemoryValue(
            daily_summary=memory.daily_summary,
            recent_decisions=memory.recent_decisions[-5:],  # 最近 5 条
            key_facts=memory.key_facts[-10:],  # 最近 10 条
        )

    def baseline(self, value: MemoryValue) -> str:
        parts = ["## Workspace Memory"]

        if value.daily_summary:
            parts.append(f"\n### Today\n{value.daily_summary}")

        if value.recent_decisions:
            parts.append("\n### Recent Decisions")
            for d in value.recent_decisions:
                parts.append(f"- {d}")

        if value.key_facts:
            parts.append("\n### Key Facts")
            for f in value.key_facts:
                parts.append(f"- {f}")

        return "\n".join(parts)

class UserPreferencesContextSource(ContextSource[PreferencesValue]):
    """用户偏好上下文源"""

    key = "user/preferences"

    def __init__(self, user_store: UserStore):
        self.user_store = user_store

    async def load(self) -> PreferencesValue | Unavailable:
        prefs = await self.user_store.get_preferences()
        if not prefs:
            return Unavailable("No user preferences")
        return PreferencesValue(
            language=prefs.language,
            style=prefs.communication_style,
            expertise=prefs.expertise_level,
        )

    def baseline(self, value: PreferencesValue) -> str:
        return (
            f"## User Preferences\n"
            f"Language: {value.language}\n"
            f"Style: {value.style}\n"
            f"Expertise: {value.expertise}"
        )
```

---

## 3. Mid-Conversation System Message（对话中系统消息）

### 3.1 设计理念（借鉴 opencode）

```python
class MidConversationSystemMessage:
    """
    对话中系统消息 - 借鉴 opencode 设计

    用途: 在对话过程中传递状态变更指令
    特点:
    - 按时间顺序纳入 Session History
    - 与 Context Snapshot 同步更新
    - 在 Safe Provider-Turn Boundary 处理

    示例:
    - "The date is now 2026-08-28"
    - "Project instructions updated: 2 files active"
    - "Skills updated: 5 new skills available"
    """

    def __init__(self, text: str, source_key: str | None = None):
        self.text = text
        self.source_key = source_key
        self.timestamp = datetime.now()

    def to_message(self) -> Message:
        return Message(
            role="system",
            content=self.text,
            metadata={
                "type": "mid_conversation_update",
                "source_key": self.source_key,
                "timestamp": self.timestamp.isoformat(),
            },
        )

class MidConversationSystemMessageHandler:
    """
    对话中系统消息处理器

    在每次 Provider Turn 前协调上下文变更
    """

    def __init__(self, system_context: SystemContextRegistry):
        self.system_context = system_context

    async def handle(
        self,
        session: Session,
    ) -> MidConversationSystemMessage | None:
        """
        处理上下文协调

        1. 获取当前快照
        2. 协调上下文
        3. 如有变更，生成 Mid-Conversation System Message
        4. 更新快照
        """
        snapshot = await session.get_context_snapshot()
        result = await self.system_context.reconcile(snapshot)

        if result.tag == "Unchanged":
            return None

        elif result.tag == "Updated":
            # 更新快照
            await session.update_context_snapshot(result.snapshot)
            return MidConversationSystemMessage(
                text=result.text,
                source_key=None,
            )

        elif result.tag == "ReplacementReady":
            # 需要替换基线，开始新 Epoch
            await session.start_new_epoch(result.generation)
            return None

        elif result.tag == "ReplacementBlocked":
            # 有不可用上下文，阻塞
            raise ContextUnavailableError(result.unavailable_keys)

        return None
```

---

## 4. Compaction v2（结构化压缩）

### 4.1 设计理念（借鉴 opencode 的 SUMMARY_TEMPLATE）

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

    def __init__(self, config: CompactionConfig, model: ModelProvider):
        self.config = config
        self.model = model
        self.buffer_tokens = config.buffer_tokens  # 默认 20000
        self.keep_tokens = config.keep_tokens      # 默认 8000

    async def should_compact(self, session: Session) -> bool:
        """判断是否需要压缩"""
        total_tokens = await session.count_tokens()
        return total_tokens > (self.config.max_tokens - self.buffer_tokens)

    async def compact(self, session: Session) -> CompactionResult:
        """
        执行压缩

        流程:
        1. 获取当前完整历史
        2. 生成结构化摘要
        3. 创建新的 Context Epoch
        4. 保留摘要作为新的基线
        """
        history = await session.get_full_history()

        # 生成结构化摘要
        summary = await self._generate_summary(history)

        # 创建新的 Context Epoch
        new_epoch = await session.start_new_epoch(
            generation=await self._create_generation_with_summary(summary),
            reason="compaction",
        )

        return CompactionResult(
            epoch_id=new_epoch.id,
            summary=summary,
            tokens_before=sum(m.tokens for m in history),
            tokens_after=self._count_tokens(summary),
        )

    async def _generate_summary(self, history: list[Message]) -> str:
        """生成结构化摘要"""
        # 检查是否有之前的摘要
        prior_summary = await self._get_prior_summary(history)

        if prior_summary:
            # 增量更新
            system = COMPACTION_TEMPLATE + "\n\n" + COMPACTION_UPDATE_INSTRUCTIONS
            user_content = (
                f"<prior-summary>\n{prior_summary}\n</prior-summary>\n\n"
                f"<conversation>\n{self._format_history_for_compaction(history)}\n</conversation>"
            )
        else:
            # 首次压缩
            system = COMPACTION_TEMPLATE
            user_content = self._format_history_for_compaction(history)

        response = await self.model.generate(
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        return response.text

    async def _create_generation_with_summary(self, summary: str) -> Generation:
        """创建包含摘要的新 Generation"""
        # 重新初始化所有 Context Source
        generation = await self.system_context.initialize()

        # 将摘要作为特殊的 Context Source 注入
        summary_source = SummaryContextSource(summary)
        self.system_context.register(summary_source)

        return await self.system_context.initialize()
```

### 4.2 Summary Context Source

```python
class SummaryContextSource(ContextSource[SummaryValue]):
    """摘要上下文源 - 压缩后的结构化摘要"""

    key = "session/summary"

    def __init__(self, summary: str):
        self._summary = summary

    async def load(self) -> SummaryValue | Unavailable:
        if not self._summary:
            return Unavailable("No summary available")
        return SummaryValue(summary=self._summary)

    def baseline(self, value: SummaryValue) -> str:
        return f"## Previous Session Summary\n{value.summary}"

    def update(self, previous: SummaryValue, current: SummaryValue) -> str:
        return f"Session summary updated with new context."
```

---

## 5. Memory System v2（记忆系统）

### 5.1 五层记忆架构（保留初版设计，增强上下文集成）

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Five-Layer Memory Architecture                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│   Layer 1: Context Epoch Memory (上下文纪元记忆)                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │ - 当前 Epoch 的结构化摘要                                    │   │
│   │ - 通过 Summary Context Source 注入                           │   │
│   │ - 每次压缩时自动更新                                         │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│   Layer 2: Session History (会话历史)                                │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │ - 当前 Epoch 内的完整对话历史                                │   │
│   │ - JSONL 持久化                                               │   │
│   │ - 包含 Mid-Conversation System Messages                      │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│   Layer 3: Workspace Memory (工作区记忆)                             │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │ - 每日摘要 (daily_summary)                                   │   │
│   │ - 关键决策 (recent_decisions)                                │   │
│   │ - 重要事实 (key_facts)                                       │   │
│   │ - 通过 WorkspaceMemoryContextSource 注入                     │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│   Layer 4: User Memory (用户记忆)                                    │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │ - 用户偏好 (preferences)                                     │   │
│   │ - 沟通风格 (communication_style)                             │   │
│   │ - 专业水平 (expertise_level)                                 │   │
│   │ - 通过 UserPreferencesContextSource 注入                     │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│   Layer 5: Output Externalization (输出外部化)                       │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │ - 大工具输出存储到文件                                        │   │
│   │ - 模型可见输出截断                                           │   │
│   │ - 通过 Tool Definition 的 max_output_chars 控制              │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 5.2 记忆存储实现

```python
class MemoryStore:
    """
    记忆存储 - 增强版

    集成 Context Source 模式，支持:
    - 自动刷新上下文
    - 变更检测
    - 增量更新
    """

    def __init__(self, db: Database):
        self.db = db

    async def get_workspace_memory(self, workspace_id: str) -> WorkspaceMemory | None:
        """获取工作区记忆"""
        row = await self.db.fetch_one(
            "SELECT * FROM workspace_memory WHERE workspace_id = ?",
            (workspace_id,),
        )
        if not row:
            return None
        return WorkspaceMemory(
            workspace_id=row["workspace_id"],
            daily_summary=row["daily_summary"],
            recent_decisions=json.loads(row["recent_decisions"]),
            key_facts=json.loads(row["key_facts"]),
        )

    async def add_decision(self, workspace_id: str, decision: str):
        """添加决策记录"""
        memory = await self.get_workspace_memory(workspace_id)
        if memory:
            memory.recent_decisions.append(decision)
            # 只保留最近 50 条
            memory.recent_decisions = memory.recent_decisions[-50:]
            await self._save_workspace_memory(memory)

    async def add_fact(self, workspace_id: str, fact: str):
        """添加事实记录"""
        memory = await self.get_workspace_memory(workspace_id)
        if memory:
            memory.key_facts.append(fact)
            # 只保留最近 100 条
            memory.key_facts = memory.key_facts[-100:]
            await self._save_workspace_memory(memory)

    async def update_daily_summary(self, workspace_id: str, summary: str):
        """更新每日摘要"""
        memory = await self.get_workspace_memory(workspace_id)
        if memory:
            memory.daily_summary = summary
            await self._save_workspace_memory(memory)
```

---

## 6. Prompt Assembly v2（提示组装）

### 6.1 设计理念

```python
class PromptAssembler:
    """
    提示组装器 - 增强版

    不再手动组装，而是通过 System Context Registry 自动组合
    每个 Context Source 贡献自己的部分
    """

    def __init__(self, system_context: SystemContextRegistry):
        self.system_context = system_context

    async def assemble(self, session: Session) -> AssembledPrompt:
        """
        组装提示

        1. 协调上下文变更
        2. 获取 Baseline System Context
        3. 组装消息历史
        4. 返回完整的模型调用请求
        """
        # 1. 协调上下文
        snapshot = await session.get_context_snapshot()
        result = await self.system_context.reconcile(snapshot)

        if result.tag == "Unchanged":
            system = session.context_epoch.generation.baseline
        elif result.tag == "Updated":
            system = session.context_epoch.generation.baseline
            await session.update_context_snapshot(result.snapshot)
        elif result.tag == "ReplacementReady":
            await session.start_new_epoch(result.generation)
            system = result.generation.baseline
        elif result.tag == "ReplacementBlocked":
            raise ContextUnavailableError(result.unavailable_keys)

        # 2. 获取消息历史
        messages = await session.get_history()

        return AssembledPrompt(
            system=system,
            messages=messages,
            context_snapshot=await session.get_context_snapshot(),
        )

@dataclass
class AssembledPrompt:
    """组装后的提示"""
    system: str
    messages: list[Message]
    context_snapshot: Snapshot
```

---

## 7. 数据模型

```python
@dataclass
class Generation:
    """上下文生成"""
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
class ReplacementResult:
    """替换结果"""
    pass

@dataclass
class ReplacementReady(ReplacementResult):
    generation: Generation
    tag: str = "ReplacementReady"

@dataclass
class ReplacementBlocked(ReplacementResult):
    unavailable_keys: list[str] = field(default_factory=list)
    tag: str = "ReplacementBlocked"

@dataclass
class CompactionResult:
    """压缩结果"""
    epoch_id: str
    summary: str
    tokens_before: int
    tokens_after: int

@dataclass
class AssembledPrompt:
    """组装后的提示"""
    system: str
    messages: list[Message]
    context_snapshot: Snapshot

# Context Source 值类型
@dataclass
class DateValue:
    date: str
    time: str
    timezone: str
    weekday: str

@dataclass
class InstructionsValue:
    instructions: list[Instruction]

@dataclass
class Instruction:
    source: str
    content: str

@dataclass
class SkillsValue:
    skills: list[SkillInfo]

@dataclass
class MemoryValue:
    daily_summary: str | None
    recent_decisions: list[str]
    key_facts: list[str]

@dataclass
class PreferencesValue:
    language: str
    style: str
    expertise: str

@dataclass
class SummaryValue:
    summary: str
```

---

## 8. 与其他模块的接口

| 接口 | 方向 | 说明 |
|------|------|------|
| `SystemContextRegistry` | 核心 | 上下文管理 |
| `ContextSource` | 扩展 | 自定义上下文源 |
| `CompactionEngine` | 输出 | 压缩触发 |
| `MemoryStore` | 依赖 | 记忆持久化 |
| `SessionStore` | 依赖 | 会话持久化 |
| `ModelProvider` | 依赖 | 摘要生成 |

---

## 9. 实现计划

| 阶段 | 内容 | 产出 |
|------|------|------|
| Phase 1 | Context Source 基础接口 | 可注册的上下文源 |
| Phase 2 | System Context Registry | 上下文组合与协调 |
| Phase 3 | 内置 Context Sources | 日期/项目/技能/记忆/偏好 |
| Phase 4 | Mid-Conversation System Message | 对话中状态变更 |
| Phase 5 | Structured Compaction | 高质量压缩 |
| Phase 6 | Memory Store 集成 | 五层记忆系统 |

---

*文档版本: v2.0 | 创建日期: 2026-08-27 | 基于 opencode 架构优化*
