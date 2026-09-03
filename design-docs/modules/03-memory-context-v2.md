# Module 03: Memory & Context v2 — 记忆与上下文系统（opencode 增强版）

> **v2.1 实现对齐说明（2026-09-03）**：本节已按实际代码更新。要点变更：
> - `initialize()` 对 `Unavailable` 源的实际行为是**跳过**（不贡献基线），不抛 `InitializationBlocked`（仅保留异常类型）
> - 内置 Context Sources 实际为 7 个：Date / ProjectInstructions / AgentSkills / WorkspaceMemory / UserPreferences / **Summary** / **AgentInfo**；后两者的构造方式与初版不同（直接持有值而非依赖注册表）
> - `MidConversationSystemMessageHandler.handle(snapshot)` 返回 `(message, new_snapshot)` 元组，不依赖 Session 对象
> - **压缩的实际链路**：无状态 `CompactionEngine`（策略）+ 有状态 `CompactionBridge`（协调器）→ 摘要写入 `session.summary` → 由 `SummaryContextSource` 经 reconcile"新可用源"路径注入，**不做基线 replace**
> - Workspace Memory 实际为**文件存储**（`memory/workspace.py` JSON），非 SQLite

## 1. 设计演进说明

本模块是本次升级的核心模块，全面借鉴 opencode 的上下文管理系统：

| 初版设计 | opencode 启发 | 改进 |
|----------|--------------|------|
| 简单 Prompt 组装 | System Context Registry | 可组合、可刷新的上下文源 |
| 基础压缩 | Structured Compaction Template | 高质量结构化摘要 |
| 简单记忆分层 | Context Epoch + Snapshot | 上下文版本管理 |
| 无状态变更机制 | Mid-Conversation System Message | 对话中状态变更指令 |

---

## 2. System Context 架构（实际实现于 context/ 包）

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
│   │   load: 加载当前值 (可能返回 Unavailable)                     │   │
│   │   baseline: 首次渲染为模型可见文本                            │   │
│   │   update: 变更时生成更新文本                                  │   │
│   │   removed: 可选移除文本生成器                                 │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                    System Context (系统上下文)               │   │
│   │   不透明载体，组合多个 Context Source                         │   │
│   │   支持顺序组合，拒绝重复 key（DuplicateKeyError）             │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                    Context Epoch (上下文纪元)                │   │
│   │   - 初始渲染的 System Context 保持不变的时间跨度             │   │
│   │   - 每个 Epoch 有不可变的 Baseline System Context            │   │
│   │   - 实际位于 context/epoch.py（类型已实现，持久化触发待接入）│   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                    Context Snapshot (上下文快照)             │   │
│   │   - 可比较的模型隐藏 JSON 状态                                │   │
│   │   - reconcile 用来比较每个源上次提交的值                      │   │
│   │   - 与 Mid-Conversation System Message 同步更新              │   │
│   └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

实际文件布局（context/）：

| 文件 | 内容 |
|------|------|
| source.py | `ContextSource` 接口、`Unavailable`、全部内置源与值类型 |
| registry.py | `SystemContextRegistry`、`Snapshot` / `SourceSnapshot` / `Generation`、协调与替换结果类型 |
| epoch.py | `ContextEpoch`（`initial()` / `is_active` / `end(reason)` / baseline/snapshot 属性） |
| reconcile.py | 协调辅助 |
| midconv.py | `MidConversationSystemMessage`、`MidConversationSystemMessageHandler`、`ContextUnavailableError` |
| compaction.py | `COMPACTION_TEMPLATE`、`COMPACTION_UPDATE_INSTRUCTIONS`、`CompactionConfig`、无状态 `CompactionEngine` |
| snapshot.py | 快照辅助 |

### 2.2 Context Source 接口与 Registry（实际签名）

```python
class ContextSource(ABC, Generic[A]):
    """上下文源 - 独立可刷新的类型化值"""

    @property
    @abstractmethod
    def key(self) -> str: ...
    @abstractmethod
    async def load(self) -> A | Unavailable: ...
    @abstractmethod
    def baseline(self, value: A) -> str: ...
    @abstractmethod
    def update(self, previous: A, current: A) -> str: ...
    def removed(self, previous: A) -> str | None:
        return None


class SystemContextRegistry:
    """管理有序、有作用域的上下文贡献者（context/registry.py）"""

    def register(self, source): ...   # 重复 key 抛 DuplicateKeyError
    def unregister(self, key): ...
    def get_source(self, key) -> ContextSource | None: ...
    keys: list[str]                   # 属性

    async def initialize(self) -> Generation:
        """生成基线文本与快照。

        实际行为：Unavailable 的源被**跳过**（不贡献基线、不入快照），
        不抛 InitializationBlocked——否则任何可选源缺失
        （如项目无 AGENTS.md）都会让整个上下文系统不可用。
        """

    async def reconcile(self, snapshot: Snapshot) -> ReconcileResult:
        """比较当前值与快照，返回 Unchanged / Updated /
        ReplacementReady / ReplacementBlocked。
        Unavailable 且已有已提交快照 → 保留上次状态；新注册的源 →
        用 baseline(value) 作为更新文本。"""

    async def replace(self, snapshot: Snapshot) -> ReplacementResult:
        """完全替换基线（压缩后/会话迁移）。
        已提交的源变为 Unavailable → ReplacementBlocked（丢弃会丢上下文）；
        否则 ReplacementReadyRep 携带新 Generation。"""
```

> 注：`ReplacementReady` / `ReplacementBlocked` 是 `ReconcileResult` 的子类（reconcile 路径），`ReplacementReadyRep` / `ReplacementBlockedRep` 是 `ReplacementResult` 的子类（replace 路径），实际代码中两组类型并存。

### 2.3 内置 Context Sources（实际 7 个，见 context/source.py）

```python
class DateContextSource(ContextSource[DateValue]):
    """key="date/current"：date/time/timezone/weekday，变更时输出 "Date changed: X -> Y" """

class ProjectInstructionsContextSource(ContextSource[InstructionsValue]):
    """key="project/instructions"：发现 AGENTS.md / CLAUDE.md / .cursorrules /
    .claude/CLAUDE.md / .github/copilot-instructions.md（读取失败静默跳过）；
    无任何指令文件时返回 Unavailable"""

class AgentSkillsContextSource(ContextSource[SkillsValue]):
    """key="agent/skills"：构造时直接传入 list[SkillInfo]（name/description/slash），
    不依赖 SkillRegistry 实例；空列表返回 Unavailable"""

class WorkspaceMemoryContextSource(ContextSource[MemoryValue]):
    """key="workspace/memory"：构造时直接传入 MemoryValue
    （daily_summary + recent_decisions + key_facts）"""

class UserPreferencesContextSource(ContextSource[PreferencesValue]):
    """key="user/preferences"：构造时直接传入 PreferencesValue
    （language/style/expertise）"""

class SummaryContextSource(ContextSource[SummaryValue]):
    """key="session/summary"：压缩摘要注入点（见 §4.3）"""

class AgentContextSource(ContextSource[AgentValue]):
    """key="agent/info"：name/mode/skills_count；mode 变更时输出
    "Agent mode changed: X -> Y" """
```

桌面端（desktop/server.py `setup()`）实际注册顺序：`DateContextSource → ProjectInstructionsContextSource(Path.cwd()) → AgentContextSource → SummaryContextSource`。SummaryContextSource 初始为空（Unavailable），首次压缩后才变为可用。

---

## 3. Mid-Conversation System Message（实际实现于 context/midconv.py）

```python
@dataclass
class MidConversationSystemMessage:
    """对话中系统消息：role="system"，
    metadata={"type": "mid_conversation_update", "source_key", "timestamp"}"""
    text: str
    source_key: str | None = None
    timestamp: datetime


class MidConversationSystemMessageHandler:
    """在每次 Provider Turn 前协调上下文变更"""

    def __init__(self, system_context: SystemContextRegistry): ...

    async def handle(self, snapshot: Snapshot) -> tuple[MidConversationSystemMessage | None, Snapshot | None]:
        """
        实际签名：接收快照（非 Session），返回 (message, new_snapshot)。

        - Unchanged           → (None, None)
        - Updated             → (MidConversationSystemMessage(text), result.snapshot)
        - ReplacementReady    → (None, None)（基线替换由压缩链路处理，见 §4.3）
        - ReplacementBlocked  → raise ContextUnavailableError(unavailable_keys)
        """
```

> **实际接线**：`SafeProviderTurnBoundary.prepare()` 在 step 边界 `session.drain_injections()` 领取非唤醒注入（含 reconcile 产生的更新文本），拼接到 system_context 的 `## Context Update` 段（见 Module 01 §2.4）。压缩摘要走同一条 Inbox 注入路径。

---

## 4. Compaction v2（实际实现：context/compaction.py + harness/compaction_bridge.py）

### 4.1 结构化摘要模板（无变化）

`COMPACTION_TEMPLATE`（Objective / Important Details / Work State[Completed|Active|Blocked] / Next Move / Relevant Files）与 `COMPACTION_UPDATE_INSTRUCTIONS`（`<prior-summary>` + `<conversation>` 合并规则）与初版设计一致，见 [context/compaction.py](file:///f:/MyCoding/sdpost-claw/src/sdpost_claw/context/compaction.py)。

### 4.2 CompactionEngine（无状态策略）

```python
@dataclass
class CompactionConfig:
    enabled: bool = True
    max_tokens: int = 100000
    buffer_tokens: int = 20000
    keep_tokens: int = 8000


class CompactionEngine:
    """无状态策略：阈值 + 提示模板（不调用模型）"""

    def should_compact(self, total_tokens: int) -> bool:
        # enabled 且 total_tokens > (max_tokens - buffer_tokens)

    def build_compaction_prompt(self, messages: list[dict],
                                prior_summary: str | None = None
                                ) -> tuple[str, str]:
        # 返回 (system_prompt, user_content)
        # 有 prior_summary 时走增量合并提示（<prior-summary> + <conversation>）

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 3)   # 近似
```

### 4.3 CompactionBridge（有状态协调器，harness/compaction_bridge.py）

初版设计中 `CompactionEngine.compact(session)` 的编排职责实际由 `CompactionBridge` 承担（Phase 5 引入，此前 CompactionEngine 是死代码）：

```python
class CompactionBridge:
    def __init__(self, engine: CompactionEngine, provider: ModelProvider | None): ...
    # provider 复用既有单一 OpenAIProvider（央企国产-only，不新增适配器）

    async def maybe_compact(self, session) -> bool:
        """
        触发流程：
        1. messages = session.log.derive_messages()   # SessionLog 为权威数据源
        2. total_tokens = 估算（与压缩提示格式对齐的 str/多段 content 处理）
        3. engine.should_compact(total_tokens) 未过阈值 → False
        4. 再触发守卫：total_tokens <= _last_compaction_tokens + buffer_tokens → False
           （deferred surface op 意味着派生历史不会物理收缩，
            裸阈值检查会每步重复触发；策略保持无状态，守卫状态在桥里）
        5. engine.build_compaction_prompt(messages, prior_summary=session.summary)
        6. provider.generate(...) → session.summary = summary
        7. session._emit(COMPACTION_OCCURRED, {...}, ignorable=True)
           # 可忽略、不上表面的事件，保持日志可重放/可审计

        任何失败（engine/provider 为空、无消息、模型调用异常、摘要为空）
        都返回 False 且**绝不打断当前 turn**。
        """
```

### 4.4 Summary 如何到达模型（reconcile"新可用源"路径）

```
session.summary 写入
   ↓ 调用方（SessionRunner）更新 SummaryContextSource.update_summary(summary)
下一次 turn 的 reconcile：
   该源在 initialize 时是 Unavailable（摘要为空）→ 不在快照里
   → reconcile 视其为"新注册的源"，返回 Updated，
     携带 "## Previous Session Summary\n{summary}"
   → Phase 4 Inbox 注入路径在 step 边界折叠进模型可见 system context
```

- **无需基线 replace**：`SystemContextRegistry.replace()` 已实现，但压缩链路当前不走基线替换；物理替换派生历史中段的 `SessionLog.replace(start, end)`（dsh `SurfaceOp.replace`）留待后续阶段。
- 摘要增量合并：再次压缩时 `build_compaction_prompt` 以旧摘要为 `<prior-summary>`，遵循"对话比旧摘要新，冲突时以对话为准"的合并规则。

---

## 5. Memory System v2（实际实现于 memory/ 包）

### 5.1 分层记忆架构（含实际接线状态）

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Five-Layer Memory Architecture                     │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 1: Context Epoch Memory    → SummaryContextSource 注入        │
│    （session.summary，压缩时更新；epoch 持久化待接入）                │
│  Layer 2: Session History         → SessionLog / messages JSONL      │
│    （含 Mid-Conversation System Messages）                           │
│  Layer 3: Workspace Memory        → WorkspaceMemoryContextSource     │
│    （memory/workspace.py 文件存储：daily_summary/recent_decisions/   │
│     key_facts，上限 50/100 条）                                      │
│  Layer 4: User Memory             → UserPreferencesContextSource     │
│    （memory/user.py）                                                │
│  Layer 5: Output Externalization  → memory/externalize.py            │
│    （大工具输出落盘 + 模型可见截断，ToolDefinition.max_output_chars） │
└─────────────────────────────────────────────────────────────────────┘
```

### 5.2 Workspace Memory（文件存储）

```python
@dataclass
class WorkspaceMemory:
    workspace_id: str
    daily_summary: str | None = None
    recent_decisions: list[str] = field(default_factory=list)   # 上限 50
    key_facts: list[str] = field(default_factory=list)          # 上限 100
    updated_at: datetime

    def add_decision(self, decision: str) -> None: ...   # 超限截断到最近 N 条
    def add_fact(self, fact: str) -> None: ...
    def update_daily_summary(self, summary: str) -> None: ...


class WorkspaceMemoryStore:
    """文件存储：<base_path>/workspace/<workspace_id>.json（aiofiles 异步 IO）"""

    def __init__(self, base_path: Path): ...
    async def get(self, workspace_id) -> WorkspaceMemory | None: ...
    async def save(self, memory: WorkspaceMemory) -> None: ...
    async def delete(self, workspace_id) -> None: ...
```

> 与初版差异：初版设计为 SQLite `MemoryStore`（db.fetch_one + workspace_memory 表）；实际为纯文件 JSON 存储，生产环境 SQLite（production/database.py）中预留了表结构（见 Module 05）。

### 5.3 Output Externalization（memory/externalize.py）

```python
class OutputExternalizer:
    """大工具输出外部化：落盘 tool_outputs/<session_id>/<tool_call_id>.txt，
    模型可见部分用 truncate_text 截断到 max_chars（默认 2000）"""

    async def externalize(self, session_id, tool_call_id, output,
                          max_chars=2000) -> ExternalizedOutput: ...
    async def read_full_output(self, file_path) -> str | None: ...
    async def cleanup_session(self, session_id) -> None: ...
```

---

## 6. Prompt Assembly（实际实现：SafeProviderTurnBoundary，见 Module 01 §2.4）

初版的独立 `PromptAssembler` 类未落地；组装职责由 `SafeProviderTurnBoundary.prepare()` 承担：

1. `PromptPromotion.promote()` 推进符合条件的输入
2. `_settle_tool_results()` 结算已完成的工具结果
3. `session.drain_injections()` 在 step 边界领取 non-waking 注入（reconcile 更新文本 / 压缩摘要），追加为 `## Context Update` 段
4. 组装 `(system_context, messages, tools)` → `PreparedTurn`

上下文协调（reconcile）与压缩压力检查由 `SessionRunner.run()` 每步内嵌执行（Phase 4/5），对所有客户端入口一致生效。

---

## 7. 数据模型（实际定义位置）

```python
# context/registry.py
@dataclass
class SourceSnapshot:
    value: Any
    removed: str | None = None

@dataclass
class Snapshot:
    entries: dict[str, SourceSnapshot] = field(default_factory=dict)

@dataclass
class Generation:
    baseline: str
    snapshot: Snapshot

# 协调结果（ReconcileResult 子类）
@dataclass
class Unchanged(ReconcileResult): tag: str = "Unchanged"
@dataclass
class Updated(ReconcileResult):
    text: str = ""
    snapshot: Snapshot = field(default_factory=Snapshot)
    tag: str = "Updated"
@dataclass
class ReplacementReady(ReconcileResult):
    generation: Generation = ...
    tag: str = "ReplacementReady"
@dataclass
class ReplacementBlocked(ReconcileResult):
    unavailable_keys: list[str] = field(default_factory=list)
    tag: str = "ReplacementBlocked"

# 替换结果（ReplacementResult 子类）：ReplacementReadyRep / ReplacementBlockedRep

# context/epoch.py
@dataclass
class ContextEpoch:
    id: str                    # generate_id()
    generation: Generation
    started_at: datetime
    ended_at: datetime | None = None
    end_reason: str | None = None
    # initial() / is_active / end(reason) / baseline / snapshot

# context/compaction.py
@dataclass
class CompactionConfig:
    enabled: bool = True
    max_tokens: int = 100000
    buffer_tokens: int = 20000
    keep_tokens: int = 8000

@dataclass
class CompactionResult:
    epoch_id: str
    summary: str
    tokens_before: int
    tokens_after: int

# context/source.py 值类型
DateValue(date, time, timezone, weekday)
Instruction(source, content) / InstructionsValue(instructions)
SkillInfo(name, description, slash=False) / SkillsValue(skills)
MemoryValue(daily_summary, recent_decisions, key_facts)
PreferencesValue(language, style, expertise)
SummaryValue(summary)
AgentValue(name, mode, skills_count)
```

---

## 8. 与其他模块的接口

| 接口 | 方向 | 说明 |
|------|------|------|
| `SystemContextRegistry` | 核心 | 上下文管理（SessionRunner.system_context 注入） |
| `ContextSource` | 扩展 | 自定义上下文源 |
| `CompactionBridge` | 输出 | 压缩触发（SessionRunner.compaction_bridge 注入） |
| `SummaryContextSource` | 内部 | 摘要注入点（SessionRunner.summary_source 注入） |
| `WorkspaceMemoryStore` | 依赖 | 记忆持久化（文件 JSON） |
| `OutputExternalizer` | 依赖 | 工具输出外部化 |
| `ModelProvider` | 依赖 | 摘要生成（复用单一 OpenAIProvider） |

---

## 9. 实现计划

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 | Context Source 基础接口 | ✅ 已完成（context/source.py，7 个内置源） |
| Phase 2 | System Context Registry | ✅ 已完成（context/registry.py） |
| Phase 3 | Context Epoch / Snapshot 类型 | ✅ 已完成（epoch 持久化触发待接入） |
| Phase 4 | Mid-Conversation 更新 + Inbox 注入路径 | ✅ 已完成（midconv.py + SessionRunner Phase 4） |
| Phase 5 | Structured Compaction + Bridge | ✅ 已完成（compaction.py + compaction_bridge.py；surface op replace 留后续） |
| Phase 6 | Memory Store / Externalization | ✅ 已完成（memory/ 包）；WorkspaceMemoryContextSource 与存储的自动同步待接线 |

---

*文档版本: v2.1 | 创建日期: 2026-08-27 | 最近更新: 2026-09-03 | 基于 opencode 架构优化*
