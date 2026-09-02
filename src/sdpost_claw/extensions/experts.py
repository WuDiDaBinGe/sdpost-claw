"""Experts System - specialized agent personas."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sdpost_claw.agent.modes import Agent, AgentMode, AgentRegistry
from sdpost_claw.agent.permissions import PermissionRuleset
from sdpost_claw.common.utils import generate_id


@dataclass
class Expert:
    """
    Expert - specialized agent persona.

    An expert is a pre-configured agent with specific:
    - System prompt
    - Tool set
    - Permission rules
    - Skills
    """

    id: str = field(default_factory=generate_id)
    name: str = "default"
    description: str = ""
    system_prompt: str = ""
    mode: AgentMode = AgentMode.BUILD
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_agent(self) -> Agent:
        """Convert to Agent instance."""
        return Agent(
            id=self.id,
            name=self.name,
            mode=self.mode,
            system_prompt=self.system_prompt,
            skills=self.skills,
        )


class ExpertRegistry:
    """Expert registry - manages expert personas."""

    def __init__(self):
        self._experts: dict[str, Expert] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        """Register default experts."""
        self.register(Expert(
            name="coder",
            description="Software development expert",
            system_prompt="""You are an expert software developer. You write clean, well-documented code.
Follow best practices, use meaningful variable names, and include error handling.
When reviewing code, look for bugs, performance issues, and style violations.""",
            mode=AgentMode.BUILD,
        ))

        self.register(Expert(
            name="analyst",
            description="Data analysis expert",
            system_prompt="""You are an expert data analyst. You analyze data, create visualizations,
and provide insights. You are proficient in Python, SQL, and data visualization tools.
You explain your analysis clearly and provide actionable recommendations.""",
            mode=AgentMode.BUILD,
        ))

        self.register(Expert(
            name="writer",
            description="Technical writing expert",
            system_prompt="""You are an expert technical writer. You create clear, concise documentation,
reports, and presentations. You adapt your writing style to the audience and purpose.
You follow standard formatting conventions and ensure consistency.""",
            mode=AgentMode.BUILD,
        ))

        self.register(Expert(
            name="reviewer",
            description="Code review expert",
            system_prompt="""You are an expert code reviewer. You analyze code for:
- Correctness and potential bugs
- Performance and efficiency
- Security vulnerabilities
- Code style and maintainability
- Test coverage
Provide constructive feedback with specific suggestions for improvement.""",
            mode=AgentMode.PLAN,
        ))

        self.register(Expert(
            name="planner",
            description="Project planning expert",
            system_prompt="""You are an expert project planner. You break down complex tasks into
manageable subtasks, identify dependencies, and create realistic timelines.
You consider risks, resources, and constraints when planning.""",
            mode=AgentMode.PLAN,
        ))

    def register(self, expert: Expert) -> None:
        """Register an expert."""
        self._experts[expert.name] = expert

    def get(self, name: str) -> Expert | None:
        """Get expert by name."""
        return self._experts.get(name)

    def list_all(self) -> list[Expert]:
        """List all experts."""
        return list(self._experts.values())

    def list_names(self) -> list[str]:
        """List all expert names."""
        return list(self._experts.keys())

    def unregister(self, name: str) -> None:
        """Unregister an expert."""
        self._experts.pop(name, None)

    def create_agent_from_expert(
        self,
        expert_name: str,
        agent_name: str | None = None,
    ) -> Agent | None:
        """Create an agent from an expert template."""
        expert = self.get(expert_name)
        if not expert:
            return None

        agent = expert.to_agent()
        if agent_name:
            agent.name = agent_name
        return agent
