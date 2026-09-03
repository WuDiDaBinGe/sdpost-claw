# Module 04: Extension System v2 — 扩展系统（opencode 增强版）

> **v2.1 实现对齐说明（2026-09-03）**：本节已按实际代码更新。要点变更：
> - `SkillRegistry`（extensions/skills.py）实际与设计基本一致；`get_available_for_agent()` 当前**未做权限过滤**（直接返回 `list_all()`）
> - `MCPConnector`（extensions/mcp.py）实际直接在传输层上走 JSON-RPC（initialize / tools/list / tools/call），无 `MCPClient` 类；MCP 工具到 `ToolDefinition` 的包装（权限前缀、输出限制）已设计但**尚未接线**到 ToolRegistry
> - Experts 实际为 `Expert` 数据类 + `ExpertRegistry`（5 个默认专家），构建于 `agent/modes.py` 的 `Agent` / `AgentMode` / `AgentRegistry` 之上；`AgentPermissions`（agent/permissions.py）提供 build/plan/general 三档默认权限构建器
> - `SkillContextSource` 独立类未实现；实际由 `AgentSkillsContextSource`（context/source.py）直接持有 `list[SkillInfo]`

## 1. 设计演进说明

本模块在初版设计基础上，吸收了 opencode 的以下优秀设计：

| 初版设计 | opencode 启发 | 改进 |
|----------|--------------|------|
| 简单 Skill 发现 | Multi-Source Skill Discovery | 多来源技能发现 |
| 基础 MCP | Permission-Aware Tools | 权限感知的工具 |
| 简单 Experts | Agent Modes (build/plan/general) | 多模式 Agent |

---

## 2. Skill System v2（实际实现于 extensions/skills.py）

### 2.1 设计理念（借鉴 opencode skill.ts）

```
Skill Source（embedded / directory / url）
        ↓ 多来源加载 + 按 source_key 缓存
SkillInfo（name / description / slash / location / content）
        ↓ list_all() 去重合并（后注册覆盖同名）
上下文注入 / 斜杠命令 / Web UI 侧边栏
```

### 2.2 Skill 发现实现

```python
@dataclass
class SkillInfo:
    name: str
    description: str | None
    slash: bool
    location: Path
    content: str


@dataclass
class Source:
    type: str  # "embedded" | "directory" | "url"
    path: str | None = None
    url: str | None = None
    skill: SkillInfo | None = None


class SkillSource:
    """来源工厂"""
    @staticmethod
    def embedded(skill: SkillInfo) -> Source: ...
    @staticmethod
    def directory(path: Path) -> Source: ...
    @staticmethod
    def url(url: str) -> Source: ...


class SkillRegistry:
    """多来源技能发现（带 per-source 缓存）"""

    def add_source(self, source: Source) -> None: ...   # 清空缓存
    async def list_all(self) -> list[SkillInfo]: ...
    async def get_available_for_agent(self, agent_id: str) -> list[SkillInfo]:
        # 现状：直接返回 list_all()，权限过滤待接入

    async def _load_from_directory(self, directory: Path) -> list[SkillInfo]:
        # patterns = ["*.md", "**/SKILL.md", "**/skill.md"]，用 set 去重文件
        # 解析 YAML frontmatter（^---\n...\n---\n），name 缺省取目录名/文件 stem
        # slash 取 frontmatter.get("slash", False)；读取失败静默跳过

    async def _load_from_url(self, url: str) -> list[SkillInfo]:
        # aiohttp 拉取，整体 try/except，失败返回 []


async def discover_skills(paths: list[Path]) -> list[SkillInfo]:
    """便捷函数：多路径一次性发现"""
```

### 2.3 Skill 注入系统上下文

桌面端 `DesktopServer.setup()` 注册技能源（bundled skills 目录 + `config.skill_dirs`），`/api/skills` 提供侧边栏列表。上下文注入由 `AgentSkillsContextSource`（context/source.py）承担——直接持有 `list[SkillInfo]`（见 Module 03 §2.3），slash 技能以 `**/name**` 形式渲染。

---

## 3. MCP Connectors（实际实现于 extensions/mcp.py）

### 3.1 传输层

```python
class MCPTransport(ABC):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def send(self, data: dict) -> None: ...
    async def receive(self) -> dict: ...

class StdioMCPTransport(MCPTransport):
    """子进程 stdio 传输：create_subprocess_exec + 行协议 JSON-RPC；
    disconnect 先 terminate 5s 超时后 kill；可选 env"""

class SSEMCPTransport(MCPTransport):
    """SSE 传输（简化实现）：send 走 POST；receive 尚未实现流式解析，
    返回 {} —— 完整 SSE 需要流式读取，属已知缺口"""
```

### 3.2 MCPConnector（JSON-RPC 直连）

```python
class MCPTool:
    name: str
    description: str
    input_schema: dict


class MCPConnector:
    """权限感知的 MCP 工具暴露（extensions/mcp.py，实际实现）"""

    def __init__(self, name, transport, permission_prefix="mcp", max_output_chars=2000): ...

    async def connect(self) -> None:
        # transport.connect()
        # → _initialize(): JSON-RPC "initialize"（protocolVersion 2024-11-05）
        # → _list_tools(): JSON-RPC "tools/list"，解析 inputSchema

    async def disconnect(self) -> None: ...

    def list_tools(self) -> list[MCPTool]:
        """同步返回 connect 时发现的工具缓存"""

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        # JSON-RPC "tools/call"

    def get_permission(self, tool_name: str) -> str:
        return f"{self.permission_prefix}.{self.name}.{tool_name}"
```

### 3.3 与 ToolRegistry 的接线（待办）

设计上的包装（未接线）：

```python
# 计划：将 MCPTool 包装为 ToolDefinition 注册进 ToolRegistry
ToolDefinition(
    name=f"{connector.name}.{mcp_tool.name}",
    input_schema=mcp_tool.input_schema,
    permission=connector.get_permission(mcp_tool.name),  # mcp.<name>.<tool>
    max_output_chars=connector.max_output_chars,
    execute_fn=...,   # 转发 call_tool
)
```

> 现状：`/api/connectors` 返回 `config.mcp_servers` 配置列表；连接器生命周期管理与工具注入执行链路尚未接入 `SessionRunner`。

---

## 4. Experts & Agent Modes（实际实现于 agent/modes.py + extensions/experts.py）

### 4.1 Agent Modes（agent/modes.py）

```python
class AgentMode(Enum):
    BUILD = "build"      # 完全访问
    PLAN = "plan"        # 只读
    GENERAL = "general"  # 子 Agent 模式


@dataclass
class Agent:
    id: str = generate_id()
    name: str = "sdpost"
    mode: AgentMode = AgentMode.BUILD
    permissions: PermissionRuleset
    skills: list[str]
    system_prompt: str = ""

    def __post_init__(self):
        # 无规则时按模式生成默认权限
        # build → AgentPermissions.build() / plan → AgentPermissions.plan()
        # general → AgentPermissions.general()

    def can(self, action: str) -> bool: ...
    def cannot(self, action: str) -> bool: ...


class AgentRegistry:
    def register / get / get_by_name / unregister / list_all
    def create_sub_agent(self, parent, name, mode=AgentMode.GENERAL) -> Agent:
        # 继承父 Agent 的 permissions 与 skills
```

### 4.2 Experts（extensions/experts.py）

```python
@dataclass
class Expert:
    """预配置的专家人格：system_prompt + 工具集 + 权限 + 技能"""
    id: str
    name: str
    description: str
    system_prompt: str
    mode: AgentMode
    tools: list[str]
    skills: list[str]
    metadata: dict

    def to_agent(self) -> Agent: ...


class ExpertRegistry:
    """内置 5 个默认专家（注册时即创建）：
    - coder（BUILD）    软件开发
    - analyst（BUILD）  数据分析
    - writer（BUILD）   技术写作
    - reviewer（PLAN）  代码评审
    - planner（PLAN）   项目规划
    """

    def register / get / list_all / list_names / unregister
    def create_agent_from_expert(self, expert_name, agent_name=None) -> Agent | None: ...
```

`/api/experts` 以 `{id, name, description, mode}` 形式暴露给 Web UI。

---

## 5. 数据模型

```python
# extensions/skills.py
@dataclass
class SkillInfo:
    name: str
    description: str | None
    slash: bool
    location: Path
    content: str

# agent/modes.py
class AgentMode(Enum):
    BUILD = "build"
    PLAN = "plan"
    GENERAL = "general"

@dataclass
class Agent:
    id: str
    name: str
    mode: AgentMode
    permissions: PermissionRuleset
    skills: list[str]
    system_prompt: str

# extensions/experts.py
@dataclass
class Expert:
    id: str
    name: str
    description: str
    system_prompt: str
    mode: AgentMode
    tools: list[str]
    skills: list[str]
    metadata: dict

# context/source.py（上下文值类型）
@dataclass
class AgentValue:
    name: str
    mode: str
    skills_count: int

@dataclass
class SkillsValue:
    skills: list[SkillInfo]
```

---

## 6. 与其他模块的接口

| 接口 | 方向 | 说明 |
|------|------|------|
| `SkillRegistry` | 核心 | 技能管理（DesktopServer 侧边栏 + 技能源注册） |
| `MCPConnector` | 扩展 | MCP 工具暴露（ToolRegistry 接线待完成） |
| `ExpertRegistry` | 核心 | 专家人格管理 |
| `Agent` / `AgentRegistry` | 核心 | 多模式 Agent（agent/modes.py） |
| `PermissionRuleset` | 依赖 | 权限检查（AgentPermissions 三档默认） |
| `AgentSkillsContextSource` / `AgentContextSource` | 输出 | 上下文注入（context/source.py） |

---

## 7. 实现计划

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 | Skill Discovery（多来源 + 缓存） | ✅ 已完成（extensions/skills.py） |
| Phase 2 | Skill Context Source | ✅ 已完成（AgentSkillsContextSource，直接持有技能列表） |
| Phase 3 | MCP Connector（传输 + JSON-RPC） | ⚠️ 骨架完成；SSE receive 与 ToolRegistry 接线待完成 |
| Phase 4 | Agent Modes（build/plan/general） | ✅ 已完成（agent/modes.py + AgentPermissions） |
| Phase 5 | Experts 系统 | ✅ 已完成（extensions/experts.py，5 个默认专家） |
| Phase 6 | Skill 权限过滤（get_available_for_agent） | ⏳ 待接入 |

---

*文档版本: v2.1 | 创建日期: 2026-08-27 | 最近更新: 2026-09-03 | 基于 opencode 架构优化*
