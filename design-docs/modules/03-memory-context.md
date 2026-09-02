# Module 03: Memory & Context — 记忆与上下文管理

## 1. 模块概述

Memory & Context 是 sdpost-claw 的"长期记忆系统"，负责管理 Agent 在长期运行中产生和需要的各类上下文信息。本模块参考 learn-workbuddy 的 s10-s15 章节，实现五层记忆体系。

### 核心理念
> "上下文窗口是 RAM，JSONL、SQLite、记忆文件和 tool-results 是磁盘。记忆系统的目标是让有限上下文承载无限工作。"

## 2. 子模块架构

```
Memory & Context
├── 3.1 Workspace Memory (工作区记忆)
├── 3.2 User Memory (用户记忆)
├── 3.3 Output Externalization (输出外部化)
├── 3.4 Context Compact (上下文压缩)
└── 3.5 Prompt Assembly (Prompt 组装)
```

### 记忆体系总览

```
┌─────────────────────────────────────────────────────────────┐
│                    Five-Layer Memory System                   │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Layer 1: Workspace Memory (工作区记忆)               │    │
│  │  - 当前项目的事实、决策、每日工作日志                  │    │
│  │  - 存储位置: workspace/.sdpost/memory/               │    │
│  │  - 生命周期: 项目期间                                 │    │
│  └─────────────────────────────────────────────────────┘    │
│                          │                                   │
│                          ▼                                   │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Layer 2: User Memory (用户记忆)                      │    │
│  │  - 跨项目偏好、习惯、长期约束                         │    │
│  │  - 存储位置: ~/.sdpost/memory/                       │    │
│  │  - 生命周期: 永久                                     │    │
│  └─────────────────────────────────────────────────────┘    │
│                          │                                   │
│                          ▼                                   │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Layer 3: Transcript (会话转录)                       │    │
│  │  - 会话事件追加写入，可恢复可回放                      │    │
│  │  - 存储位置: workspace/.sdpost/transcripts/          │    │
│  │  - 生命周期: 会话期间                                 │    │
│  └─────────────────────────────────────────────────────┘    │
│                          │                                   │
│                          ▼                                   │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Layer 4: Tool-Result Swap (工具结果外部化)           │    │
│  │  - 大输出写磁盘，上下文留指针                         │    │
│  │  - 存储位置: workspace/.sdpost/tool-results/         │    │
│  │  - 生命周期: 可配置                                   │    │
│  └─────────────────────────────────────────────────────┘    │
│                          │                                   │
│                          ▼                                   │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Layer 5: Context Compact (上下文压缩)                │    │
│  │  - 长会话上下文压缩，保持关键信息                     │    │
│  │  - 运行时处理，不持久化                               │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 3.1 Workspace Memory（工作区记忆）

### 3.1.1 设计目标
- 记录当前项目的工作上下文
- 自动提取关键决策和事实
- 支持跨会话的项目记忆

### 3.1.2 存储结构

```
workspace/
└── .sdpost/
    └── memory/
        ├── workspace.json          # 工作区元数据
        ├── daily/                  # 每日工作日志
        │   ├── 2026-08-27.md
        │   └── 2026-08-26.md
        ├── decisions/              # 关键决策记录
        │   └── decision-001.md
        └── facts/                  # 项目事实
            └── project-facts.md
```

### 3.1.3 核心实现

```python
class WorkspaceMemory:
    """工作区记忆管理器"""

    def __init__(self, workspace_path: Path):
        self.workspace_path = Path(workspace_path)
        self.memory_dir = self.workspace_path / ".sdpost" / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_structure()

    def _ensure_structure(self):
        """确保目录结构存在"""
        for subdir in ["daily", "decisions", "facts"]:
            (self.memory_dir / subdir).mkdir(exist_ok=True)

    async def log_daily_work(self, date: str, content: str):
        """记录每日工作"""
        path = self.memory_dir / "daily" / f"{date}.md"
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n## {datetime.now().strftime('%H:%M')}\n")
            f.write(content + "\n")

    async def record_decision(self, decision: str, context: str, rationale: str):
        """记录关键决策"""
        decision_id = f"decision-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        path = self.memory_dir / "decisions" / f"{decision_id}.md"
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# 决策: {decision}\n\n")
            f.write(f"**时间**: {datetime.now().isoformat()}\n")
            f.write(f"**上下文**: {context}\n")
            f.write(f"**理由**: {rationale}\n")

    async def add_fact(self, category: str, fact: str):
        """添加项目事实"""
        path = self.memory_dir / "facts" / f"{category}.md"
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"- {fact}\n")

    async def get_relevant_memory(self, query: str, limit: int = 10) -> list[str]:
        """检索相关记忆"""
        # 简单实现：关键词匹配
        # 后期可升级为向量检索
        results = []
        for md_file in self.memory_dir.rglob("*.md"):
            content = md_file.read_text(encoding="utf-8")
            if any(kw in content.lower() for kw in query.lower().split()):
                results.append(content[:500])  # 摘要
        return results[:limit]

    async def get_daily_summary(self, date: str = None) -> str:
        """获取每日摘要"""
        date = date or datetime.now().strftime("%Y-%m-%d")
        path = self.memory_dir / "daily" / f"{date}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""
```

---

## 3.2 User Memory（用户记忆）

### 3.2.1 设计目标
- 跨项目持久化用户偏好
- 记录用户工作习惯
- 隐私保护（本地存储）

### 3.2.2 存储结构

```
~/.sdpost/
├── memory/
│   ├── user_profile.json         # 用户画像
│   ├── preferences.json          # 偏好设置
│   ├── habits.md                 # 工作习惯
│   └── constraints.md            # 长期约束
└── config.yaml                   # 全局配置
```

### 3.2.3 核心实现

```python
class UserMemory:
    """用户记忆管理器"""

    def __init__(self, home_path: Path = None):
        self.home_path = home_path or Path.home()
        self.memory_dir = self.home_path / ".sdpost" / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._profile = self._load_profile()
        self._preferences = self._load_preferences()

    def _load_profile(self) -> dict:
        """加载用户画像"""
        path = self.memory_dir / "user_profile.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {
            "created_at": datetime.now().isoformat(),
            "expertise": [],
            "goals": [],
        }

    def _load_preferences(self) -> dict:
        """加载偏好设置"""
        path = self.memory_dir / "preferences.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {
            "language": "zh-CN",
            "output_style": "detailed",
            "preferred_model": "deepseek",
            "auto_confirm_safe_tools": True,
        }

    async def update_preference(self, key: str, value: Any):
        """更新偏好"""
        self._preferences[key] = value
        await self._save_preferences()

    async def add_expertise(self, domain: str, level: str = "intermediate"):
        """添加专业领域"""
        self._profile.setdefault("expertise", []).append({
            "domain": domain,
            "level": level,
            "added_at": datetime.now().isoformat(),
        })
        await self._save_profile()

    async def get_user_context(self) -> str:
        """获取用户上下文（用于注入 prompt）"""
        context_parts = []
        if self._profile.get("expertise"):
            expertise_str = ", ".join(
                f"{e['domain']}({e['level']})" for e in self._profile["expertise"]
            )
            context_parts.append(f"用户专业领域: {expertise_str}")
        if self._preferences:
            context_parts.append(f"用户偏好: {json.dumps(self._preferences, ensure_ascii=False)}")
        return "\n".join(context_parts)

    async def _save_profile(self):
        """保存用户画像"""
        path = self.memory_dir / "user_profile.json"
        path.write_text(
            json.dumps(self._profile, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def _save_preferences(self):
        """保存偏好"""
        path = self.memory_dir / "preferences.json"
        path.write_text(
            json.dumps(self._preferences, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
```

---

## 3.3 Output Externalization（输出外部化）

### 3.3.1 设计目标
- 大工具输出不塞入上下文
- 上下文只保留摘要和指针
- 按需加载完整输出

### 3.3.2 外部化策略

```python
class OutputExternalizer:
    """输出外部化器"""

    def __init__(self, workspace_path: Path, threshold: int = 2000):
        self.workspace_path = Path(workspace_path)
        self.external_dir = self.workspace_path / ".sdpost" / "tool-results"
        self.external_dir.mkdir(parents=True, exist_ok=True)
        self.threshold = threshold  # 字符数阈值

    def should_externalize(self, content: str) -> bool:
        """判断是否需要外部化"""
        return len(content) > self.threshold

    async def externalize(
        self, session_id: str, tool_call_id: str, content: str
    ) -> ExternalizedRef:
        """外部化大输出"""
        # 生成文件路径
        ref_id = f"{tool_call_id}"
        path = self.external_dir / session_id / f"{ref_id}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)

        # 写入完整内容
        path.write_text(content, encoding="utf-8")

        # 生成摘要
        summary = self._generate_summary(content)

        return ExternalizedRef(
            ref_id=ref_id,
            path=str(path),
            summary=summary,
            size=len(content),
            created_at=datetime.now(),
        )

    def _generate_summary(self, content: str, max_lines: int = 5) -> str:
        """生成内容摘要"""
        lines = content.split("\n")
        head = lines[:max_lines]
        tail = lines[-max_lines:] if len(lines) > max_lines * 2 else []
        summary = "\n".join(head)
        if tail:
            summary += f"\n... ({len(lines) - max_lines * 2} lines omitted) ...\n"
            summary += "\n".join(tail)
        return summary

    async def load_full_content(self, ref: ExternalizedRef) -> str:
        """加载完整内容"""
        return Path(ref.path).read_text(encoding="utf-8")

@dataclass
class ExternalizedRef:
    """外部化引用"""
    ref_id: str
    path: str
    summary: str
    size: int
    created_at: datetime
```

---

## 3.4 Context Compact（上下文压缩）

### 3.4.1 设计目标
- 长会话上下文溢出时自动压缩
- 保持关键信息不丢失
- 支持多种压缩策略

### 3.4.2 压缩策略

```python
class CompactStrategy(Enum):
    """压缩策略"""
    TRUNCATE = "truncate"       # 截断最早的消息
    SUMMARIZE = "summarize"     # 摘要压缩
    PRUNE = "prune"             # 修剪工具结果

class ContextCompactor:
    """上下文压缩器"""

    def __init__(self, config: CompactConfig):
        self.config = config
        self.token_counter = TokenCounter()

    async def compact(
        self,
        messages: list[dict],
        max_tokens: int,
        strategy: CompactStrategy = CompactStrategy.SUMMARIZE,
    ) -> list[dict]:
        """压缩上下文"""
        current_tokens = self.token_counter.count(messages)

        if current_tokens <= max_tokens:
            return messages

        if strategy == CompactStrategy.TRUNCATE:
            return await self._truncate(messages, max_tokens)
        elif strategy == CompactStrategy.SUMMARIZE:
            return await self._summarize(messages, max_tokens)
        elif strategy == CompactStrategy.PRUNE:
            return await self._prune(messages, max_tokens)

    async def _truncate(
        self, messages: list[dict], max_tokens: int
    ) -> list[dict]:
        """截断策略：保留最近 N 条消息"""
        # 始终保留 system 和第一条用户消息
        system = [m for m in messages if m["role"] == "system"]
        non_system = [m for m in messages if m["role"] != "system"]

        # 从旧到新截断
        truncated = []
        total_tokens = self.token_counter.count(system)

        for msg in reversed(non_system):
            msg_tokens = self.token_counter.count([msg])
            if total_tokens + msg_tokens > max_tokens:
                break
            truncated.insert(0, msg)
            total_tokens += msg_tokens

        return system + truncated

    async def _summarize(
        self, messages: list[dict], max_tokens: int
    ) -> list[dict]:
        """摘要策略：将旧消息压缩为摘要"""
        # 保留最近 50% 的消息
        split_point = len(messages) // 2
        old_messages = messages[:split_point]
        recent_messages = messages[split_point:]

        # 生成旧消息摘要
        summary = await self._generate_summary(old_messages)

        # 构造压缩后的消息列表
        compressed = [
            messages[0],  # system
            {"role": "system", "content": f"[历史摘要] {summary}"},
        ] + recent_messages

        return compressed

    async def _prune(
        self, messages: list[dict], max_tokens: int
    ) -> list[dict]:
        """修剪策略：精简工具结果"""
        pruned = []
        for msg in messages:
            if msg.get("role") == "tool":
                # 只保留工具结果的前 200 字符
                content = msg.get("content", "")
                if len(content) > 200:
                    msg = {
                        **msg,
                        "content": content[:200] + "... (truncated)",
                    }
            pruned.append(msg)
        return pruned

    async def _generate_summary(self, messages: list[dict]) -> str:
        """生成消息摘要"""
        # 使用轻量模型生成摘要
        # 简化实现：提取关键信息
        key_points = []
        for msg in messages:
            if msg.get("role") == "user":
                key_points.append(f"用户: {msg['content'][:100]}")
            elif msg.get("role") == "assistant" and msg.get("content"):
                key_points.append(f"AI: {msg['content'][:100]}")
        return " | ".join(key_points[-10:])  # 最近 10 条关键信息
```

---

## 3.5 Prompt Assembly（Prompt 组装）

### 3.5.1 设计目标
- 运行时动态组装 prompt
- 精确控制上下文预算
- 按需注入记忆和工具

### 3.5.2 Prompt 结构

```python
class PromptAssembler:
    """Prompt 组装器"""

    def __init__(self, config: PromptConfig):
        self.config = config
        self.token_budget = config.token_budget

    async def assemble(
        self,
        session: Session,
        workspace_memory: WorkspaceMemory,
        user_memory: UserMemory,
        tools: list[ToolSpec],
    ) -> AssembledContext:
        """组装完整 prompt"""
        blocks = []
        budget = self.token_budget

        # Block 1: System Prompt (固定)
        system_block = await self._build_system_prompt(session)
        blocks.append(system_block)
        budget -= self._count_tokens(system_block.content)

        # Block 2: User Memory (高优先级)
        user_context = await user_memory.get_user_context()
        if user_context:
            user_block = ContextBlock(
                name="user_memory",
                content=user_context,
                priority=Priority.HIGH,
            )
            blocks.append(user_block)
            budget -= self._count_tokens(user_context)

        # Block 3: Workspace Memory (按需)
        relevant_memory = await workspace_memory.get_relevant_memory(
            session.current_topic or "", limit=5
        )
        if relevant_memory:
            memory_content = "\n".join(relevant_memory)
            memory_block = ContextBlock(
                name="workspace_memory",
                content=memory_content,
                priority=Priority.MEDIUM,
            )
            blocks.append(memory_block)
            budget -= self._count_tokens(memory_content)

        # Block 4: Tool Specs (按需)
        tool_specs_text = json.dumps([t.__dict__ for t in tools], ensure_ascii=False)
        tool_block = ContextBlock(
            name="tool_specs",
            content=tool_specs_text,
            priority=Priority.HIGH,
        )
        blocks.append(tool_block)
        budget -= self._count_tokens(tool_specs_text)

        # Block 5: 对话历史 (剩余预算)
        history = await self._get_history_within_budget(session, budget)
        blocks.append(ContextBlock(
            name="history",
            content=history,
            priority=Priority.HIGH,
        ))

        return AssembledContext(
            system_prompt=system_block.content,
            messages=history,
            tools=tools,
            blocks=blocks,
        )

    async def _build_system_prompt(self, session: Session) -> ContextBlock:
        """构建 system prompt"""
        system = f"""你是 sdpost-claw，一个全场景 AI 办公智能体。

## 你的能力
- 理解自然语言任务
- 自主规划和拆解复杂任务
- 调用工具执行实际操作
- 交付可验收的工作成果

## 工作目录
{session.cwd}

## 行为准则
1. 先理解用户意图，再开始执行
2. 复杂任务拆分为小步骤
3. 敏感操作前请求确认
4. 完成后提供清晰的交付说明
"""
        return ContextBlock(name="system", content=system, priority=Priority.FIXED)

@dataclass
class AssembledContext:
    """组装后的上下文"""
    system_prompt: str
    messages: list[dict]
    tools: list[ToolSpec]
    blocks: list[ContextBlock]

@dataclass
class ContextBlock:
    """上下文块"""
    name: str
    content: str
    priority: Priority

class Priority(Enum):
    """优先级"""
    FIXED = 0       # 固定，不可裁剪
    HIGH = 1        # 高优先级
    MEDIUM = 2      # 中优先级
    LOW = 3         # 低优先级，可裁剪
```

---

## 4. 数据模型

```python
@dataclass
class CompactConfig:
    """压缩配置"""
    max_tokens: int = 8000
    strategy: CompactStrategy = CompactStrategy.SUMMARIZE
    preserve_first_n: int = 5  # 保留前 N 条消息
    preserve_last_n: int = 20  # 保留后 N 条消息

@dataclass
class PromptConfig:
    """Prompt 配置"""
    token_budget: int = 10000
    max_tool_specs: int = 20
    memory_injection_limit: int = 5
```

---

## 5. 与其他模块的接口

| 接口 | 方向 | 说明 |
|------|------|------|
| `AgentLoop` | 服务 | 为 Agent 提供上下文 |
| `SessionManager` | 依赖 | 获取会话信息 |
| `JSONLTranscript` | 输入 | 读取历史消息 |
| `Storage` | 依赖 | 持久化存储 |

---

## 6. 实现计划

| 阶段 | 内容 | 产出 |
|------|------|------|
| Phase 1 | Workspace Memory | 项目记忆可读写 |
| Phase 2 | User Memory | 用户偏好可持久化 |
| Phase 3 | Output Externalization | 大输出自动外部化 |
| Phase 4 | Context Compact | 长会话自动压缩 |
| Phase 5 | Prompt Assembly | 动态 prompt 组装 |

---

*文档版本: v1.0 | 创建日期: 2026-08-27*
