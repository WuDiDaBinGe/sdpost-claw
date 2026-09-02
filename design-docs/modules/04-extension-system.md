# Module 04: Extension System — 扩展系统

## 1. 模块概述

Extension System 是 sdpost-claw 的能力扩展层，负责 Skills（技能）、MCP Connectors（MCP 连接器）和 Experts（领域专家）三大扩展机制。本模块参考 learn-workbuddy 的 s16-s18 章节和腾讯 WorkBuddy 的 SkillHub 生态。

### 核心理念
> "Skills 添加指令，MCP 添加外部工具，Experts 打包人设+记忆+工具。三者共同构成开放但可治理的扩展生态。"

## 2. 子模块架构

```
Extension System
├── 4.1 Skills System (技能系统)
├── 4.2 MCP Connectors (MCP 连接器)
└── 4.3 Experts System (专家系统)
```

---

## 4.1 Skills System（技能系统）

### 4.1.1 设计目标
- 可复用的指令包
- 按需加载，不占用初始上下文
- 支持脚本和资源文件
- 社区共享与版本管理

### 4.1.2 Skill 结构

```
skills/
├── data-analysis/              # 数据分析技能
│   ├── SKILL.md                # 技能定义（必须）
│   ├── templates/              # 模板文件
│   │   ├── report.md
│   │   └── chart.html
│   └── scripts/                # 辅助脚本
│       └── analyze.py
├── ppt-creation/               # PPT 创建技能
│   ├── SKILL.md
│   └── templates/
├── code-review/                # 代码审查技能
│   ├── SKILL.md
│   └── checklists/
└── writing/                    # 写作技能
    ├── SKILL.md
    └── styles/
```

### 4.1.3 SKILL.md 格式

```markdown
---
name: data-analysis
version: 1.0.0
description: 数据分析与可视化技能，支持 CSV/Excel 数据的深度分析
author: sdpost-claw
tags: [data, analysis, visualization, chart]
keywords: [excel, csv, 数据, 分析, 图表, 统计]
triggers:
  - 数据分析
  - 分析数据
  - 生成图表
  - 数据可视化
  - analyze data
  - data visualization
---

# 数据分析技能

## 适用场景
- CSV/Excel 数据清洗与预处理
- 统计分析与洞察提取
- 数据可视化与图表生成
- 分析报告自动生成

## 工作流程
1. 读取数据文件，理解数据结构
2. 数据清洗（处理缺失值、异常值）
3. 执行统计分析
4. 生成可视化图表
5. 输出分析报告

## 输出格式
- 分析结果使用 Markdown 表格
- 图表使用 HTML/SVG 格式
- 报告包含：概述、方法、发现、建议

## 注意事项
- 大数据集（>10MB）先采样分析
- 敏感数据提醒用户确认
- 图表配色遵循可访问性标准
```

### 4.1.4 核心实现

```python
class SkillRegistry:
    """技能注册中心"""

    def __init__(self, skills_dir: Path):
        self.skills_dir = Path(skills_dir)
        self._skills: dict[str, Skill] = {}
        self._index: dict[str, list[str]] = {}  # 关键词索引

    async def discover(self):
        """发现并加载所有技能"""
        for skill_dir in self.skills_dir.iterdir():
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
                skill = await self._load_skill(skill_dir)
                self._skills[skill.name] = skill
                self._index_skill(skill)

    async def _load_skill(self, skill_dir: Path) -> Skill:
        """加载单个技能"""
        manifest = await self._parse_manifest(skill_dir / "SKILL.md")
        return Skill(
            name=manifest["name"],
            version=manifest["version"],
            description=manifest["description"],
            manifest=manifest,
            path=skill_dir,
            templates=self._load_templates(skill_dir / "templates"),
            scripts=self._load_scripts(skill_dir / "scripts"),
        )

    async def _parse_manifest(self, path: Path) -> dict:
        """解析 SKILL.md 清单"""
        content = path.read_text(encoding="utf-8")
        # 解析 YAML frontmatter
        if content.startswith("---"):
            _, frontmatter, body = content.split("---", 2)
            manifest = yaml.safe_load(frontmatter)
            manifest["body"] = body.strip()
        else:
            manifest = {"body": content}
        return manifest

    def search(self, query: str, top_k: int = 5) -> list[Skill]:
        """搜索相关技能"""
        query_lower = query.lower()
        scores = {}
        for keyword, skill_names in self._index.items():
            if any(kw in query_lower for kw in keyword.split()):
                for name in skill_names:
                    scores[name] = scores.get(name, 0) + 1

        sorted_skills = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [self._skills[name] for name, _ in sorted_skills[:top_k] if name in self._skills]

    async def load_skill_content(self, skill_name: str) -> str:
        """加载技能完整内容（按需加载）"""
        skill = self._skills.get(skill_name)
        if not skill:
            raise ValueError(f"技能不存在: {skill_name}")
        return skill.path.read_text(encoding="utf-8")

@dataclass
class Skill:
    """技能数据模型"""
    name: str
    version: str
    description: str
    manifest: dict
    path: Path
    templates: list[Path]
    scripts: list[Path]
```

### 4.1.5 技能发现与加载流程

```
用户输入: "帮我分析这个 Excel 数据"

1. Agent 调用 skill_search("Excel 数据分析")
2. SkillRegistry 搜索匹配技能
3. 返回: ["data-analysis", "data-visualization"]
4. Agent 选择 "data-analysis"
5. 加载 SKILL.md 完整内容
6. Agent 按照技能定义的工作流执行任务
```

---

## 4.2 MCP Connectors（MCP 连接器）

### 4.2.1 设计目标
- 标准协议接入外部工具
- 支持多种传输方式（stdio / SSE / HTTP）
- 信任模型与安全边界
- 自动发现与配置

### 4.2.2 MCP 协议支持

```python
class MCPConnector:
    """MCP 连接器"""

    def __init__(self, config: MCPConfig):
        self.config = config
        self._clients: dict[str, MCPClient] = {}
        self._tool_cache: dict[str, list[ToolSpec]] = {}

    async def connect(self, server_name: str, server_config: MCPServerConfig):
        """连接 MCP 服务器"""
        client = MCPClient(server_config)
        await client.initialize()
        self._clients[server_name] = client

        # 缓存工具列表
        tools = await client.list_tools()
        self._tool_cache[server_name] = [
            ToolSpec(
                name=f"{server_name}.{tool.name}",
                description=tool.description,
                parameters=tool.inputSchema,
            )
            for tool in tools
        ]

    async def call_tool(
        self, server_name: str, tool_name: str, arguments: dict
    ) -> ToolResult:
        """调用 MCP 工具"""
        client = self._clients.get(server_name)
        if not client:
            return ToolResult.error(f"MCP 服务器未连接: {server_name}")

        result = await client.call_tool(tool_name, arguments)
        return ToolResult(
            tool_call_id=str(uuid.uuid4()),
            name=f"{server_name}.{tool_name}",
            content=result.content,
        )

    async def disconnect(self, server_name: str):
        """断开连接"""
        client = self._clients.pop(server_name, None)
        if client:
            await client.close()
        self._tool_cache.pop(server_name, None)

class MCPClient:
    """MCP 客户端实现"""

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.transport = self._create_transport()
        self.session = None

    def _create_transport(self):
        """创建传输层"""
        if self.config.transport == "stdio":
            return StdioTransport(self.config.command, self.config.args)
        elif self.config.transport == "sse":
            return SSETransport(self.config.url)
        elif self.config.transport == "http":
            return HTTPTransport(self.config.url)

    async def initialize(self):
        """初始化 MCP 会话"""
        await self.transport.connect()
        self.session = MCPSession(self.transport)
        await self.session.initialize()

    async def list_tools(self) -> list[MCPTool]:
        """列出可用工具"""
        return await self.session.list_tools()

    async def call_tool(self, name: str, arguments: dict) -> MCPResult:
        """调用工具"""
        return await self.session.call_tool(name, arguments)

    async def close(self):
        """关闭连接"""
        if self.session:
            await self.session.close()
        await self.transport.close()
```

### 4.2.3 MCP 配置格式

```yaml
# .sdpost/mcp.yaml
mcp_servers:
  filesystem:
    transport: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem"]
    args: ["/workspace"]
    auto_connect: true

  github:
    transport: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_TOKEN: ${GITHUB_TOKEN}
    auto_connect: false

  database:
    transport: sse
    url: "http://localhost:8080/sse"
    auto_connect: false
```

---

## 4.3 Experts System（专家系统）

### 4.3.1 设计目标
- 领域专家打包（人设 + 记忆 + 工具）
- 一键切换工作模式
- 专家间协作
- 自定义企业专家

### 4.3.2 Expert 结构

```
experts/
├── data-scientist/             # 数据科学家
│   ├── EXPERT.md               # 专家定义
│   ├── persona.md              # 人设描述
│   ├── knowledge/              # 领域知识
│   │   ├── statistics.md
│   │   └── visualization.md
│   └── tools.yaml              # 工具配置
├── product-manager/            # 产品经理
│   ├── EXPERT.md
│   ├── persona.md
│   └── knowledge/
├── legal-advisor/              # 法律顾问
│   ├── EXPERT.md
│   ├── persona.md
│   └── knowledge/
└── custom/                     # 自定义专家
    └── ...
```

### 4.3.3 EXPERT.md 格式

```markdown
---
name: data-scientist
version: 1.0.0
description: 资深数据科学家，擅长数据分析、统计建模和可视化
model_tier: craft
skills:
  - data-analysis
  - data-visualization
  - report-generation
tools:
  - read_file
  - write_file
  - bash
  - http_request
mcp_servers:
  - database
  - visualization
---

# 数据科学家专家

## 人设
你是一位拥有 10 年经验的数据科学家，精通统计学、机器学习和数据可视化。
你习惯用数据说话，善于从复杂数据中发现洞察。

## 工作方式
1. 先理解业务问题和数据背景
2. 进行探索性数据分析（EDA）
3. 选择合适的分析方法
4. 执行分析并验证结果
5. 用可视化呈现发现
6. 给出可操作的建议

## 输出标准
- 分析结果必须包含置信区间
- 图表必须标注数据来源
- 结论必须有数据支撑
- 建议必须具体可执行
```

### 4.3.4 核心实现

```python
class ExpertRegistry:
    """专家注册中心"""

    def __init__(self, experts_dir: Path):
        self.experts_dir = Path(experts_dir)
        self._experts: dict[str, Expert] = {}
        self._active_expert: Expert = None

    async def discover(self):
        """发现所有专家"""
        for expert_dir in self.experts_dir.iterdir():
            if expert_dir.is_dir() and (expert_dir / "EXPERT.md").exists():
                expert = await self._load_expert(expert_dir)
                self._experts[expert.name] = expert

    async def activate(self, expert_name: str) -> ExpertSession:
        """激活专家模式"""
        expert = self._experts.get(expert_name)
        if not expert:
            raise ValueError(f"专家不存在: {expert_name}")

        self._active_expert = expert

        return ExpertSession(
            expert=expert,
            system_prompt=await self._build_expert_prompt(expert),
            tools=await self._resolve_tools(expert),
            skills=await self._resolve_skills(expert),
        )

    async def deactivate(self):
        """停用当前专家"""
        self._active_expert = None

    async def _build_expert_prompt(self, expert: Expert) -> str:
        """构建专家 system prompt"""
        persona = (expert.path / "persona.md").read_text(encoding="utf-8")
        knowledge = await self._load_knowledge(expert.path / "knowledge")

        return f"""## 专家模式: {expert.name}

{persona}

## 领域知识
{knowledge}

## 工作规范
{expert.manifest.get('guidelines', '')}
"""

@dataclass
class Expert:
    """专家数据模型"""
    name: str
    version: str
    description: str
    manifest: dict
    path: Path
    model_tier: ModelTier
    skills: list[str]
    tools: list[str]
    mcp_servers: list[str]

@dataclass
class ExpertSession:
    """专家会话"""
    expert: Expert
    system_prompt: str
    tools: list[ToolSpec]
    skills: list[Skill]
```

### 4.3.5 专家协作模式

```python
class ExpertOrchestrator:
    """专家编排器 — 支持多专家协作"""

    async def collaborate(
        self,
        task: str,
        expert_names: list[str],
        session: Session,
    ) -> CollaborationResult:
        """多专家协作执行任务"""
        # 创建共享上下文
        shared_context = CollaborationContext(task=task)

        # 并行激活多个专家
        expert_sessions = []
        for name in expert_names:
            expert_session = await self.registry.activate(name)
            expert_sessions.append(expert_session)

        # 分配子任务
        subtasks = await self._decompose_task(task, expert_sessions)

        # 并行执行
        results = await asyncio.gather(*[
            self._execute_with_expert(es, st, shared_context)
            for es, st in zip(expert_sessions, subtasks)
        ])

        # 汇总结果
        return await self._synthesize_results(results, shared_context)
```

---

## 5. 数据模型

```python
@dataclass
class MCPConfig:
    """MCP 配置"""
    auto_discover: bool = True
    trusted_servers: list[str] = None
    max_connections: int = 10

@dataclass
class MCPServerConfig:
    """MCP 服务器配置"""
    name: str
    transport: str  # stdio / sse / http
    command: str = None
    args: list[str] = None
    url: str = None
    env: dict[str, str] = None
    auto_connect: bool = False

@dataclass
class SkillManifest:
    """技能清单"""
    name: str
    version: str
    description: str
    author: str
    tags: list[str]
    keywords: list[str]
    triggers: list[str]
    body: str
```

---

## 6. 与其他模块的接口

| 接口 | 方向 | 说明 |
|------|------|------|
| `ToolRegistry` | 扩展 | 注册 Skill/MCP 工具 |
| `AgentLoop` | 服务 | 提供扩展能力 |
| `ModelRouter` | 依赖 | 专家模式模型选择 |
| `PromptAssembler` | 输入 | 注入专家 prompt |

---

## 7. 实现计划

| 阶段 | 内容 | 产出 |
|------|------|------|
| Phase 1 | Skills System 基础 | 技能可发现、可加载 |
| Phase 2 | MCP Connectors | 支持 stdio/SSE 连接 |
| Phase 3 | Experts System | 专家模式可切换 |
| Phase 4 | 专家协作 | 多专家并行工作 |

---

*文档版本: v1.0 | 创建日期: 2026-08-27*
