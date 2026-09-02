# Module 04: Extension System v2 — 扩展系统（opencode 增强版）

## 1. 设计演进说明

本模块在初版设计基础上，吸收了 opencode 的以下优秀设计：

| 初版设计 | opencode 启发 | 改进 |
|----------|--------------|------|
| 简单 Skill 发现 | Multi-Source Skill Discovery | 多来源技能发现 |
| 基础 MCP | Permission-Aware Tools | 权限感知的工具 |
| 简单 Experts | Agent Modes (build/plan/general) | 多模式 Agent |

---

## 2. Skill System v2（多来源技能发现）

### 2.1 设计理念（借鉴 opencode skill.ts）

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Skill Discovery Architecture                       │
│                    (借鉴 opencode skill.ts)                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                    Skill Source (技能来源)                   │   │
│   │                                                             │   │
│   │   - EmbeddedSource: 内置技能                                │   │
│   │   - DirectorySource: 本地目录技能                           │   │
│   │   - UrlSource: 远程 URL 技能                                │   │
│   │                                                             │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                    Skill Info (技能信息)                     │   │
│   │                                                             │   │
│   │   - name: 技能名称                                          │   │
│   │   - description: 技能描述                                   │   │
│   │   - slash: 是否支持斜杠命令                                  │   │
│   │   - location: 文件位置                                      │   │
│   │   - content: 技能内容 (Markdown)                            │   │
│   │                                                             │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                    Skill Filtering (技能过滤)                │   │
│   │                                                             │   │
│   │   - 基于 Agent 权限过滤                                     │   │
│   │   - 基于 Agent 模式过滤                                     │   │
│   │   - 基于用户配置过滤                                        │   │
│   │                                                             │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Skill 发现实现

```python
class SkillSource:
    """技能来源 - 借鉴 opencode 的 Source 设计"""

    @staticmethod
    def embedded(skill: SkillInfo) -> EmbeddedSource:
        """内置技能"""
        return EmbeddedSource(skill=skill)

    @staticmethod
    def directory(path: Path) -> DirectorySource:
        """本地目录技能"""
        return DirectorySource(path=str(path))

    @staticmethod
    def url(url: str) -> UrlSource:
        """远程 URL 技能"""
        return UrlSource(url=url)

@dataclass
class SkillInfo:
    """技能信息"""
    name: str
    description: str | None
    slash: bool
    location: Path
    content: str

class SkillRegistry:
    """
    技能注册表 - 借鉴 opencode 的 Skill Registry

    支持多来源技能发现:
    - 内置技能 (Embedded)
    - 本地目录 (Directory)
    - 远程 URL (Url)
    """

    def __init__(self):
        self._sources: list[Source] = []
        self._cache: dict[str, list[SkillInfo]] = {}

    def add_source(self, source: Source):
        """添加技能来源"""
        self._sources.append(source)
        # 清除缓存
        self._cache.clear()

    async def list_all(self) -> list[SkillInfo]:
        """列出所有技能"""
        skills: dict[str, SkillInfo] = {}

        for source in self._sources:
            source_key = self._source_key(source)
            cached = self._cache.get(source_key)
            if cached is not None:
                for skill in cached:
                    skills[skill.name] = skill
                continue

            loaded = await self._load_from_source(source)
            self._cache[source_key] = loaded
            for skill in loaded:
                skills[skill.name] = skill

        return list(skills.values())

    async def get_available_for_agent(self, agent_id: str) -> list[SkillInfo]:
        """获取 Agent 可用的技能（基于权限过滤）"""
        all_skills = await self.list_all()
        agent = await self.agent_store.get(agent_id)

        return [
            skill for skill in all_skills
            if self._check_permission(skill, agent)
        ]

    async def _load_from_source(self, source: Source) -> list[SkillInfo]:
        """从来源加载技能"""
        if source.type == "embedded":
            return [source.skill]

        if source.type == "directory":
            return await self._load_from_directory(Path(source.path))

        if source.type == "url":
            return await self._load_from_url(source.url)

        return []

    async def _load_from_directory(self, directory: Path) -> list[SkillInfo]:
        """从目录加载技能"""
        skills = []

        # 查找所有 SKILL.md 和 *.md 文件
        patterns = ["*.md", "**/SKILL.md"]
        for pattern in patterns:
            for filepath in directory.rglob(pattern):
                if not filepath.is_file():
                    continue

                content = filepath.read_text(encoding="utf-8")
                frontmatter = self._parse_frontmatter(content)

                if frontmatter is None:
                    continue

                name = frontmatter.get("name")
                if name is None:
                    # 使用目录名或文件名
                    if filepath.name == "SKILL.md":
                        name = filepath.parent.name
                    else:
                        name = filepath.stem

                skills.append(SkillInfo(
                    name=name,
                    description=frontmatter.get("description"),
                    slash=frontmatter.get("slash", False),
                    location=filepath,
                    content=content,
                ))

        return skills

    async def _load_from_url(self, url: str) -> list[SkillInfo]:
        """从 URL 加载技能"""
        # 下载并缓存远程技能
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    content = await response.text()
                    # 解析并返回技能
                    return self._parse_remote_skills(content)
        return []

    def _parse_frontmatter(self, content: str) -> dict | None:
        """解析 YAML frontmatter"""
        match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
        if not match:
            return None
        try:
            return yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            return None

    def _check_permission(self, skill: SkillInfo, agent: Agent) -> bool:
        """检查 Agent 是否有权限使用该技能"""
        decision = agent.permissions.evaluate(f"skill.{skill.name}")
        return decision.effect != "deny"

    def _source_key(self, source: Source) -> str:
        """生成来源 key"""
        if source.type == "embedded":
            return f"embedded:{source.skill.name}"
        elif source.type == "directory":
            return f"directory:{source.path}"
        elif source.type == "url":
            return f"url:{source.url}"
        return ""
```

### 2.3 Skill 作为 Context Source

```python
class SkillContextSource(ContextSource[SkillsValue]):
    """技能上下文源 - 将可用技能注入系统上下文"""

    key = "agent/skills"

    def __init__(self, agent: Agent, skill_registry: SkillRegistry):
        self.agent = agent
        self.registry = skill_registry

    async def load(self) -> SkillsValue | Unavailable:
        skills = await self.registry.get_available_for_agent(self.agent.id)
        if not skills:
            return Unavailable("No skills available")
        return SkillsValue(skills=skills)

    def baseline(self, value: SkillsValue) -> str:
        parts = ["## Available Skills"]
        for skill in value.skills:
            if skill.slash:
                parts.append(f"- **/{skill.name}**: {skill.description or 'No description'}")
            else:
                parts.append(f"- **{skill.name}**: {skill.description or 'No description'}")
        return "\n".join(parts)

    def update(self, previous: SkillsValue, current: SkillsValue) -> str:
        added = len(current.skills) - len(previous.skills)
        if added > 0:
            return f"{added} new skill(s) available"
        elif added < 0:
            return f"{abs(added)} skill(s) removed"
        return "Skills updated"
```

---

## 3. MCP Connectors v2（权限感知）

### 3.1 设计理念

```python
class MCPConnector:
    """
    MCP 连接器 - 增强版

    特性:
    - 权限感知的工具暴露
    - 自动 Schema 验证
    - 输出大小限制
    """

    def __init__(
        self,
        name: str,
        transport: MCPTransport,
        permission_prefix: str = "mcp",
        max_output_chars: int = 2000,
    ):
        self.name = name
        self.transport = transport
        self.permission_prefix = permission_prefix
        self.max_output_chars = max_output_chars
        self._client: MCPClient | None = None

    async def connect(self):
        """连接到 MCP 服务器"""
        self._client = MCPClient(self.transport)
        await self._client.connect()

    async def disconnect(self):
        """断开连接"""
        if self._client:
            await self._client.disconnect()
            self._client = None

    async def list_tools(self) -> list[ToolDefinition]:
        """列出可用工具（带权限和输出限制）"""
        if not self._client:
            raise ConnectionError("Not connected to MCP server")

        mcp_tools = await self._client.list_tools()
        return [
            self._wrap_tool(tool) for tool in mcp_tools
        ]

    def _wrap_tool(self, mcp_tool: MCPTool) -> ToolDefinition:
        """包装 MCP 工具，添加权限和输出限制"""
        return ToolDefinition(
            name=f"{self.name}.{mcp_tool.name}",
            description=mcp_tool.description,
            input_schema=mcp_tool.input_schema,
            output_schema=mcp_tool.output_schema,
            permission=f"{self.permission_prefix}.{self.name}.{mcp_tool.name}",
            max_output_chars=self.max_output_chars,
            execute=lambda input_data, context: self._execute(mcp_tool.name, input_data, context),
        )

    async def _execute(
        self,
        tool_name: str,
        input_data: dict,
        context: ToolContext,
    ) -> dict:
        """执行 MCP 工具"""
        if not self._client:
            raise ConnectionError("Not connected to MCP server")

        result = await self._client.call_tool(tool_name, input_data)
        return result

class MCPTransport(ABC):
    """MCP 传输抽象"""

    @abstractmethod
    async def connect(self):
        pass

    @abstractmethod
    async def disconnect(self):
        pass

    @abstractmethod
    async def send(self, data: dict):
        pass

    @abstractmethod
    async def receive(self) -> dict:
        pass

class StdioMCPTransport(MCPTransport):
    """stdio 传输"""

    def __init__(self, command: str, args: list[str] | None = None):
        self.command = command
        self.args = args or []
        self._process: asyncio.subprocess.Process | None = None

    async def connect(self):
        self._process = await asyncio.create_subprocess_exec(
            self.command, *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def disconnect(self):
        if self._process:
            self._process.terminate()
            await self._process.wait()
            self._process = None

    async def send(self, data: dict):
        if self._process and self._process.stdin:
            self._process.stdin.write(json.dumps(data).encode() + b"\n")
            await self._process.stdin.drain()

    async def receive(self) -> dict:
        if self._process and self._process.stdout:
            line = await self._process.stdout.readline()
            return json.loads(line.decode())
        return {}

class SSEMCPTransport(MCPTransport):
    """SSE 传输"""

    def __init__(self, url: str, headers: dict | None = None):
        self.url = url
        self.headers = headers or {}
        self._session: aiohttp.ClientSession | None = None

    async def connect(self):
        self._session = aiohttp.ClientSession(headers=self.headers)

    async def disconnect(self):
        if self._session:
            await self._session.close()
            self._session = None

    async def send(self, data: dict):
        if self._session:
            async with self._session.post(self.url, json=data) as response:
                return await response.json()
        return {}

    async def receive(self) -> dict:
        # SSE 长连接接收
        if self._session:
            async with self._session.get(self.url) as response:
                async for line in response.content:
                    if line.startswith(b"data: "):
                        return json.loads(line[6:])
        return {}
```

---

## 4. Experts System v2（多模式 Agent）

### 4.1 设计理念（借鉴 opencode 的 Agent Modes）

```python
class AgentMode(Enum):
    """Agent 模式 - 借鉴 opencode 的 build/plan/general"""
    BUILD = "build"        # 完全访问权限
    PLAN = "plan"          # 只读权限
    GENERAL = "general"    # 子 Agent 模式

class Agent:
    """
    Agent - 增强版

    特性:
    - 多模式支持 (build/plan/general)
    - 权限规则集
    - 技能过滤
    """

    def __init__(
        self,
        id: str,
        name: str,
        mode: AgentMode = AgentMode.BUILD,
        permissions: PermissionRuleset | None = None,
        skills: list[str] | None = None,
    ):
        self.id = id
        self.name = name
        self.mode = mode
        self.permissions = permissions or self._default_permissions(mode)
        self.skills = skills or []

    def _default_permissions(self, mode: AgentMode) -> PermissionRuleset:
        """根据模式生成默认权限"""
        if mode == AgentMode.BUILD:
            return PermissionRuleset.build()
        elif mode == AgentMode.PLAN:
            return PermissionRuleset.plan()
        elif mode == AgentMode.GENERAL:
            return PermissionRuleset.build()  # 子 Agent 默认完全访问
        return PermissionRuleset()

class AgentRegistry:
    """Agent 注册表"""

    def __init__(self):
        self._agents: dict[str, Agent] = {}

    def register(self, agent: Agent):
        """注册 Agent"""
        self._agents[agent.id] = agent

    def get(self, agent_id: str) -> Agent | None:
        """获取 Agent"""
        return self._agents.get(agent_id)

    def create_sub_agent(
        self,
        parent: Agent,
        name: str,
        mode: AgentMode = AgentMode.GENERAL,
    ) -> Agent:
        """创建子 Agent"""
        return Agent(
            id=generate_id(),
            name=name,
            mode=mode,
            permissions=parent.permissions,  # 继承父 Agent 权限
            skills=parent.skills,
        )
```

### 4.2 Agent 作为 Context Source

```python
class AgentContextSource(ContextSource[AgentValue]):
    """Agent 上下文源 - 将 Agent 信息注入系统上下文"""

    key = "agent/info"

    def __init__(self, agent: Agent):
        self.agent = agent

    async def load(self) -> AgentValue | Unavailable:
        return AgentValue(
            name=self.agent.name,
            mode=self.agent.mode.value,
            skills_count=len(self.agent.skills),
        )

    def baseline(self, value: AgentValue) -> str:
        return (
            f"## Current Agent\n"
            f"Name: {value.name}\n"
            f"Mode: {value.mode}\n"
            f"Available Skills: {value.skills_count}"
        )

    def update(self, previous: AgentValue, current: AgentValue) -> str:
        if previous.mode != current.mode:
            return f"Agent mode changed: {previous.mode} → {current.mode}"
        return "Agent info updated"
```

---

## 5. 数据模型

```python
@dataclass
class SkillInfo:
    """技能信息"""
    name: str
    description: str | None
    slash: bool
    location: Path
    content: str

@dataclass
class Agent:
    """Agent"""
    id: str
    name: str
    mode: AgentMode
    permissions: PermissionRuleset
    skills: list[str]

@dataclass
class AgentValue:
    """Agent 上下文值"""
    name: str
    mode: str
    skills_count: int

@dataclass
class SkillsValue:
    """技能上下文值"""
    skills: list[SkillInfo]

class AgentMode(Enum):
    """Agent 模式"""
    BUILD = "build"
    PLAN = "plan"
    GENERAL = "general"
```

---

## 6. 与其他模块的接口

| 接口 | 方向 | 说明 |
|------|------|------|
| `SkillRegistry` | 核心 | 技能管理 |
| `MCPConnector` | 扩展 | MCP 工具暴露 |
| `AgentRegistry` | 核心 | Agent 管理 |
| `PermissionRuleset` | 依赖 | 权限检查 |
| `ContextSource` | 输出 | 上下文注入 |

---

## 7. 实现计划

| 阶段 | 内容 | 产出 |
|------|------|------|
| Phase 1 | Skill Discovery | 多来源技能发现 |
| Phase 2 | Skill Context Source | 技能上下文注入 |
| Phase 3 | MCP Connector v2 | 权限感知的 MCP |
| Phase 4 | Agent Modes | 多模式 Agent |
| Phase 5 | Agent Context Source | Agent 上下文注入 |

---

*文档版本: v2.0 | 创建日期: 2026-08-27 | 基于 opencode 架构优化*
