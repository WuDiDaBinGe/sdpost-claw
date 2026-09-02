"""Agent Modes - build/plan/general multi-mode support."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from sdpost_claw.agent.permissions import PermissionRuleset, AgentPermissions
from sdpost_claw.common.utils import generate_id


class AgentMode(Enum):
    """Agent operation modes."""
    BUILD = "build"      # Full access
    PLAN = "plan"        # Read-only
    GENERAL = "general"  # Sub-agent mode


@dataclass
class Agent:
    """
    Agent - multi-mode agent with permissions and skills.
    """

    id: str = field(default_factory=generate_id)
    name: str = "sdpost"
    mode: AgentMode = AgentMode.BUILD
    permissions: PermissionRuleset = field(default_factory=PermissionRuleset)
    skills: list[str] = field(default_factory=list)
    system_prompt: str = ""

    def __post_init__(self):
        if not self.permissions.list_rules():
            self.permissions = self._default_permissions(self.mode)

    def _default_permissions(self, mode: AgentMode) -> PermissionRuleset:
        """Generate default permissions for mode."""
        if mode == AgentMode.BUILD:
            return AgentPermissions.build()
        elif mode == AgentMode.PLAN:
            return AgentPermissions.plan()
        elif mode == AgentMode.GENERAL:
            return AgentPermissions.general()
        return PermissionRuleset()

    def can(self, action: str) -> bool:
        """Check if agent can perform action."""
        return self.permissions.is_allowed(action)

    def cannot(self, action: str) -> bool:
        """Check if agent cannot perform action."""
        return self.permissions.is_denied(action)


class AgentRegistry:
    """Agent registry - manages agent instances."""

    def __init__(self):
        self._agents: dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        """Register an agent."""
        self._agents[agent.id] = agent

    def get(self, agent_id: str) -> Agent | None:
        """Get agent by ID."""
        return self._agents.get(agent_id)

    def get_by_name(self, name: str) -> Agent | None:
        """Get agent by name."""
        for agent in self._agents.values():
            if agent.name == name:
                return agent
        return None

    def unregister(self, agent_id: str) -> None:
        """Unregister an agent."""
        self._agents.pop(agent_id, None)

    def list_all(self) -> list[Agent]:
        """List all agents."""
        return list(self._agents.values())

    def create_sub_agent(
        self,
        parent: Agent,
        name: str,
        mode: AgentMode = AgentMode.GENERAL,
    ) -> Agent:
        """Create a sub-agent inheriting parent's permissions."""
        return Agent(
            name=name,
            mode=mode,
            permissions=parent.permissions,
            skills=parent.skills,
        )
