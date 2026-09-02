# Module 01: Agent Core — 智能体核心引擎

## 1. 模块概述

Agent Core 是 sdpost-claw 的心脏，负责理解用户意图、规划任务、调用工具并交付结果。本模块参考 learn-workbuddy 的 s01-s04 章节，实现一个完整的 Agent Loop 系统。

### 核心理念
> "模型是大脑，Harness 是操作系统。Agent Core 就是让大脑能够思考、决策、执行的神经系统。"

## 2. 子模块架构

```
Agent Core
├── 2.1 Agent Loop (智能体循环)
├── 2.2 Tool Dispatch (工具分发)
├── 2.3 Deferred Loading (延迟加载)
└── 2.4 Permission Hooks (权限钩子)
```

---

## 2.1 Agent Loop（智能体循环）

### 2.1.1 设计目标
实现一个 ReAct (Reasoning + Acting) 风格的 Agent Loop，支持：
- 多轮对话与工具调用
- 自主任务拆解与执行
- 自我校验与修正
- 可中断与可恢复

### 2.1.2 核心流程

```
┌─────────────────────────────────────────────────────────────┐
│                      Agent Loop Flow                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  User Input ──► [Intent Parser] ──► [Task Planner]           │
│                                         │                    │
│                                         ▼                    │
│                              [Context Assembler]              │
│                                         │                    │
│                                         ▼                    │
│                              [Model Invocation]               │
│                                         │                    │
│                              ┌──────────┴──────────┐        │
│                              │                     │        │
│                         文本回复              工具调用        │
│                              │                     │        │
│                              ▼                     ▼        │
│                         [Deliver]         [Tool Dispatcher]   │
│                                                  │           │
│                                                  ▼           │
│                                          [Tool Execution]    │
│                                                  │           │
│                                                  ▼           │
│                                          [Result Handler]    │
│                                                  │           │
│                              ┌───────────────────┘           │
│                              ▼                               │
│                    [Self-Verification]                        │
│                              │                               │
│                    ┌─────────┴─────────┐                    │
│                    │                   │                    │
│               任务完成            需要继续执行                 │
│                    │                   │                    │
│                    ▼                   ▼                    │
│              [Deliver]         [Loop Back to Model]          │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 2.1.3 核心接口设计

```python
class AgentLoop:
    """智能体主循环"""

    def __init__(
        self,
        model_provider: ModelProvider,
        tool_registry: ToolRegistry,
        memory_manager: MemoryManager,
        permission_guard: PermissionGuard,
        config: AgentConfig,
    ):
        self.model = model_provider
        self.tools = tool_registry
        self.memory = memory_manager
        self.guard = permission_guard
        self.config = config
        self.max_iterations = config.max_iterations  # 防止无限循环
        self.iteration_count = 0

    async def run(self, user_input: str, session: Session) -> AgentResult:
        """
        主执行循环
        1. 解析用户意图
        2. 组装上下文 (system prompt + memory + history)
        3. 调用模型获取响应
        4. 如果有工具调用，执行工具并回传结果
        5. 自我校验结果
        6. 返回最终结果或继续循环
        """
        self.iteration_count = 0

        while self.iteration_count < self.max_iterations:
            self.iteration_count += 1

            # 1. 组装上下文
            context = await self.memory.assemble_context(session)

            # 2. 调用模型
            model_turn = await self.model.generate(
                system=context.system_prompt,
                messages=context.messages,
                tools=context.tool_specs,
            )

            # 3. 处理文本输出
            if model_turn.text:
                await self._emit_message(session, model_turn.text)

            # 4. 检查是否需要工具调用
            if not model_turn.wants_tools:
                return AgentResult(
                    status="completed",
                    output=model_turn.text,
                    iterations=self.iteration_count,
                )

            # 5. 执行工具调用
            tool_results = await self._execute_tool_calls(
                model_turn.tool_calls, session
            )

            # 6. 将工具结果回传上下文
            await self.memory.append_tool_results(session, tool_results)

            # 7. 自我校验
            if await self._self_verify(session, tool_results):
                return AgentResult(
                    status="completed",
                    output=self._collect_artifacts(tool_results),
                    iterations=self.iteration_count,
                )

        # 达到最大迭代次数
        return AgentResult(
            status="max_iterations_reached",
            output="任务未能在限定步骤内完成，请简化需求或分批处理",
            iterations=self.iteration_count,
        )

    async def _execute_tool_calls(
        self, tool_calls: list[ToolCall], session: Session
    ) -> list[ToolResult]:
        """并发执行多个工具调用"""
        tasks = []
        for call in tool_calls:
            # 权限检查
            if not await self.guard.check_permission(call, session):
                tasks.append(asyncio.create_task(
                    self._permission_denied_result(call)
                ))
            else:
                tasks.append(asyncio.create_task(
                    self.tools.dispatch(call, session)
                ))
        return await asyncio.gather(*tasks)

    async def _self_verify(self, session: Session, results: list[ToolResult]) -> bool:
        """自我校验：检查任务是否完成"""
        # 检查是否有错误
        if any(r.error for r in results):
            return False
        # 检查是否达到终止条件
        return all(r.is_final for r in results)
```

### 2.1.4 状态机设计

```python
class AgentState(Enum):
    IDLE = "idle"
    PARSING = "parsing"           # 解析用户输入
    PLANNING = "planning"         # 规划任务步骤
    EXECUTING = "executing"       # 执行工具调用
    VERIFYING = "verifying"       # 校验结果
    DELIVERING = "delivering"     # 交付结果
    WAITING_USER = "waiting_user" # 等待用户确认
    ERROR = "error"               # 错误状态
    COMPLETED = "completed"       # 完成
```

---

## 2.2 Tool Dispatch（工具分发）

### 2.2.1 设计目标
- 统一的工具注册与发现机制
- 支持同步/异步工具执行
- 工具调用并发控制
- 工具结果标准化

### 2.2.2 工具注册中心

```python
class ToolRegistry:
    """工具注册中心 — 管理所有可用工具"""

    def __init__(self):
        self._tools: dict[str, ToolHandler] = {}
        self._categories: dict[str, list[str]] = {}
        self._middleware_chain: list[Middleware] = []

    def register(
        self,
        name: str,
        handler: Callable,
        spec: ToolSpec,
        category: str = "general",
        permissions: list[str] = None,
    ):
        """注册工具"""
        self._tools[name] = ToolHandler(
            name=name,
            handler=handler,
            spec=spec,
            category=category,
            permissions=permissions or [],
        )
        self._categories.setdefault(category, []).append(name)

    async def dispatch(self, call: ToolCall, session: Session) -> ToolResult:
        """分发工具调用"""
        handler = self._tools.get(call.name)
        if not handler:
            return ToolResult.error(f"未知工具: {call.name}")

        # 执行中间件链
        context = MiddlewareContext(call, session)
        for middleware in self._middleware_chain:
            context = await middleware.before(context)

        # 执行工具
        try:
            result = await handler.execute(call.arguments, session)
        except Exception as e:
            result = ToolResult.error(str(e))

        # 执行后置中间件
        for middleware in reversed(self._middleware_chain:
            result = await middleware.after(result)

        return result

    def get_specs(self, category: str = None) -> list[ToolSpec]:
        """获取工具规格（用于发送给模型）"""
        if category:
            names = self._categories.get(category, [])
            return [self._tools[n].spec for n in names]
        return [h.spec for h in self._tools.values()]
```

### 2.2.3 内置工具集

```python
class BuiltInTools:
    """内置工具集"""

    @staticmethod
    def register_all(registry: ToolRegistry):
        # 文件操作
        registry.register("read_file", ReadFileHandler(), ToolSpec(
            name="read_file",
            description="读取文件内容",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "offset": {"type": "integer", "description": "起始行"},
                    "limit": {"type": "integer", "description": "读取行数"},
                },
                "required": ["path"],
            },
        ), category="file")

        registry.register("write_file", WriteFileHandler(), ...)
        registry.register("list_files", ListFilesHandler(), ...)
        registry.register("search_files", SearchFilesHandler(), ...)

        # Shell 执行
        registry.register("bash", BashHandler(), ..., category="shell")

        # 网络请求
        registry.register("http_request", HttpRequestHandler(), ..., category="network")

        # 子 Agent 调用
        registry.register("spawn_agent", SpawnAgentHandler(), ..., category="agent")

        # 工具发现
        registry.register("tool_search", ToolSearchHandler(), ..., category="meta")
```

---

## 2.3 Deferred Loading（延迟加载）

### 2.3.1 设计目标
- 减少初始 prompt 大小（工具 schema 可能很大）
- 按需加载工具定义
- 支持工具搜索与发现

### 2.3.2 延迟加载机制

```python
class DeferredToolLoader:
    """工具延迟加载器"""

    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self._loaded_categories: set[str] = set()
        self._tool_index = self._build_search_index()

    def _build_search_index(self) -> dict[str, list[str]]:
        """构建工具搜索索引"""
        index = {}
        for name, handler in self.registry._tools.items():
            # 索引工具名称、描述、关键词
            keywords = [
                name,
                handler.spec.description,
                handler.category,
                *(handler.spec.keywords or []),
            ]
            for keyword in keywords:
                index.setdefault(keyword.lower(), []).append(name)
        return index

    async def search_tools(self, query: str, top_k: int = 5) -> list[ToolSpec]:
        """根据查询搜索相关工具"""
        query_lower = query.lower()
        scores = {}
        for keyword, tools in self.tool_index.items():
            if query_lower in keyword or keyword in query_lower:
                for tool in tools:
                    scores[tool] = scores.get(tool, 0) + 1

        sorted_tools = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [
            self.registry._tools[name].spec
            for name, _ in sorted_tools[:top_k]
        ]

    async def load_category(self, category: str) -> list[ToolSpec]:
        """按需加载某个分类的工具"""
        if category in self._loaded_categories:
            return self.registry.get_specs(category)
        self._loaded_categories.add(category)
        return self.registry.get_specs(category)

    def get_bootstrap_specs(self) -> list[ToolSpec]:
        """获取初始最小工具集（只包含 tool_search）"""
        return [self.registry._tools["tool_search"].spec]
```

### 2.3.3 工具发现流程

```
用户输入: "帮我分析这个 Excel 数据"

1. Agent 只有 tool_search 工具
2. Agent 调用 tool_search("Excel 数据分析")
3. 返回: ["read_file", "bash", "write_file"] 等工具
4. Agent 获得完整工具 schema
5. Agent 使用完整工具执行任务
```

---

## 2.4 Permission Hooks（权限钩子）

### 2.4.1 设计目标
- 分级权限控制
- 敏感操作需用户确认
- 可审计的权限决策
- 沙盒边界控制

### 2.4.2 权限等级设计

```python
class PermissionLevel(Enum):
    """权限等级"""
    AUTO = "auto"           # 自动执行，无需确认
    NOTIFY = "notify"       # 执行后通知用户
    CONFIRM = "confirm"     # 执行前需用户确认
    DENY = "deny"           # 禁止执行

class PermissionRule:
    """权限规则"""

    def __init__(
        self,
        tool_name: str,
        level: PermissionLevel,
        conditions: dict = None,
        description: str = "",
    ):
        self.tool_name = tool_name
        self.level = level
        self.conditions = conditions or {}
        self.description = description

class PermissionGuard:
    """权限守卫"""

    def __init__(self, config: PermissionConfig):
        self.config = config
        self._rules: list[PermissionRule] = []
        self._load_default_rules()

    def _load_default_rules(self):
        """加载默认权限规则"""
        defaults = [
            PermissionRule("read_file", PermissionLevel.AUTO),
            PermissionRule("list_files", PermissionLevel.AUTO),
            PermissionRule("search_files", PermissionLevel.AUTO),
            PermissionRule("tool_search", PermissionLevel.AUTO),
            PermissionRule("write_file", PermissionLevel.CONDITIONAL,
                         conditions={"protected_paths": ["/etc", "/sys", "/proc"]}),
            PermissionRule("bash", PermissionLevel.CONDITIONAL,
                         conditions={"blocked_commands": ["rm -rf /", "mkfs", "dd"]}),
            PermissionRule("http_request", PermissionLevel.NOTIFY),
            PermissionRule("spawn_agent", PermissionLevel.CONFIRM),
        ]
        self._rules.extend(defaults)

    async def check_permission(
        self, call: ToolCall, session: Session
    ) -> PermissionDecision:
        """检查权限"""
        rule = self._find_rule(call.name)
        if not rule:
            return PermissionDecision(allowed=True, level=PermissionLevel.AUTO)

        # 检查条件
        if rule.conditions:
            condition_result = await self._evaluate_conditions(
                rule.conditions, call.arguments
            )
            if condition_result.is_violated:
                return PermissionDecision(
                    allowed=False,
                    level=PermissionLevel.DENY,
                    reason=condition_result.reason,
                )

        # 需要用户确认
        if rule.level == PermissionLevel.CONFIRM:
            user_response = await self._request_user_confirmation(
                session, call, rule
            )
            return PermissionDecision(
                allowed=user_response.approved,
                level=rule.level,
                reason=user_response.reason,
            )

        return PermissionDecision(allowed=True, level=rule.level)
```

### 2.4.3 权限决策流程

```
工具调用请求
      │
      ▼
┌──────────────┐
│ 查找匹配规则  │
└──────┬───────┘
       │
       ▼
┌──────────────┐     ┌──────────────┐
│  AUTO 级别?  │─是─►│  直接执行     │
└──────┬───────┘     └──────────────┘
       │否
       ▼
┌──────────────┐     ┌──────────────┐
│ 检查条件约束  │─违规─►│  拒绝执行     │
└──────┬───────┘     └──────────────┘
       │通过
       ▼
┌──────────────┐     ┌──────────────┐
│ CONFIRM 级别?│─是─►│ 请求用户确认  │
└──────┬───────┘     └──────────────┘
       │否
       ▼
┌──────────────┐
│ 执行并记录    │
└──────────────┘
```

---

## 3. 数据模型

```python
@dataclass
class ToolSpec:
    """工具规格定义"""
    name: str
    description: str
    parameters: dict  # JSON Schema
    category: str = "general"
    keywords: list[str] = None

@dataclass
class ToolCall:
    """工具调用"""
    id: str
    name: str
    arguments: dict

@dataclass
class ToolResult:
    """工具执行结果"""
    tool_call_id: str
    name: str
    content: str
    error: str = None
    externalized_path: str = None  # 大输出外部化路径
    exit_code: int = 0
    is_final: bool = False
    artifacts: list[str] = None  # 产物文件列表

@dataclass
class ModelTurn:
    """模型响应"""
    text: str
    tool_calls: list[ToolCall]
    raw_response: Any = None

@dataclass
class AgentResult:
    """Agent 执行结果"""
    status: str
    output: str
    iterations: int
    artifacts: list[str] = None
    transcript_path: str = None
```

---

## 4. 与其他模块的接口

| 接口 | 方向 | 说明 |
|------|------|------|
| `ModelProvider` | 依赖 | 模型调用抽象层 |
| `MemoryManager` | 依赖 | 记忆与上下文管理 |
| `SessionManager` | 依赖 | 会话生命周期管理 |
| `AuditLog` | 输出 | 审计日志记录 |
| `EventBus` | 输出 | 事件发布 |

---

## 5. 实现计划

| 阶段 | 内容 | 产出 |
|------|------|------|
| Phase 1 | Agent Loop 基础框架 | 可运行的单轮对话 |
| Phase 2 | Tool Registry + 内置工具 | 支持文件/Shell 操作 |
| Phase 3 | Deferred Loading | 工具按需加载 |
| Phase 4 | Permission Hooks | 完整权限控制 |

---

*文档版本: v1.0 | 创建日期: 2026-08-27*
