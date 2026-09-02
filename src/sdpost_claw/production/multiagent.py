"""Multi-Agent Collaboration - supervisor and message bus."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from sdpost_claw.agent.drain import Session, SessionRunner, DrainResult
from sdpost_claw.agent.modes import Agent, AgentMode, AgentRegistry
from sdpost_claw.agent.permissions import PermissionRuleset
from sdpost_claw.common.utils import generate_id


@dataclass
class SubTask:
    """Subtask for multi-agent execution."""
    id: str = field(default_factory=generate_id)
    prompt: str = ""
    dependencies: list[str] = field(default_factory=list)
    result: str | None = None


@dataclass
class SubTaskResult:
    """Subtask result."""
    subtask_id: str
    result: str


@dataclass
class TaskResult:
    """Aggregated task result."""
    subtask_results: list[SubTaskResult] = field(default_factory=list)
    summary: str = ""


class MessageBus:
    """
    Message Bus - priority-based message passing.

    Message priorities:
    - steer: Interrupt (highest priority)
    - followUp: Normal message
    - result: Result message
    """

    def __init__(self):
        self._queues: dict[str, asyncio.PriorityQueue] = {}

    async def send(
        self,
        agent_id: str,
        message: dict[str, Any],
        priority: int = 10,
    ) -> None:
        """Send message to agent."""
        if agent_id not in self._queues:
            self._queues[agent_id] = asyncio.PriorityQueue()
        await self._queues[agent_id].put((priority, message))

    async def recv(self, agent_id: str) -> dict[str, Any]:
        """Receive message for agent."""
        if agent_id not in self._queues:
            self._queues[agent_id] = asyncio.PriorityQueue()
        _, message = await self._queues[agent_id].get()
        return message

    async def steer(self, agent_id: str, message: dict[str, Any]) -> None:
        """Send interrupt message (highest priority)."""
        await self.send(agent_id, message, priority=0)

    async def follow_up(self, agent_id: str, message: dict[str, Any]) -> None:
        """Send normal message."""
        await self.send(agent_id, message, priority=10)

    async def send_result(self, agent_id: str, message: dict[str, Any]) -> None:
        """Send result message."""
        await self.send(agent_id, message, priority=5)


class Supervisor:
    """
    Supervisor - coordinates multi-agent task execution.

    Responsibilities:
    - Task decomposition
    - Agent assignment
    - Result aggregation
    """

    def __init__(
        self,
        agent_registry: AgentRegistry,
        session_runner: SessionRunner,
        message_bus: MessageBus,
    ):
        self.agent_registry = agent_registry
        self.session_runner = session_runner
        self.message_bus = message_bus

    async def execute_task(
        self,
        task: str,
        context: dict[str, Any] | None = None,
    ) -> TaskResult:
        """
        Execute a complex task using multi-agent collaboration.

        1. Use PLAN agent to analyze and decompose
        2. Execute subtasks with BUILD agents
        3. Aggregate results
        """
        # 1. Create PLAN agent for analysis
        plan_agent = Agent(
            name="planner",
            mode=AgentMode.PLAN,
            permissions=PermissionRuleset(),
        )

        plan_result = await self._run_agent(
            agent=plan_agent,
            prompt=f"Analyze this task and break it into subtasks:\n\n{task}\n\n"
                   f"Format each subtask on a new line starting with '- '",
        )

        # 2. Parse subtasks
        subtasks = self._parse_subtasks(plan_result)

        # 3. Execute subtasks in parallel
        results = await asyncio.gather(*[
            self._execute_subtask(subtask) for subtask in subtasks
        ])

        # 4. Aggregate results
        return TaskResult(
            subtask_results=results,
            summary=self._aggregate_results(results),
        )

    async def _execute_subtask(self, subtask: SubTask) -> SubTaskResult:
        """Execute a single subtask."""
        build_agent = Agent(
            name=f"worker-{subtask.id[:8]}",
            mode=AgentMode.BUILD,
            permissions=PermissionRuleset(),
        )

        result = await self._run_agent(
            agent=build_agent,
            prompt=subtask.prompt,
        )

        return SubTaskResult(
            subtask_id=subtask.id,
            result=result,
        )

    async def _run_agent(
        self,
        agent: Agent,
        prompt: str,
        system_context: str = "",
        session: Session | None = None,
    ) -> str:
        """Run an agent with a prompt."""
        if session is None:
            session = Session(
                cwd=".",
                title=f"Agent: {agent.name}",
                agent_mode=agent.mode.value,
            )

        # Run the session
        result = await self.session_runner.run(
            session=session,
            system_context=system_context or "You are a helpful AI assistant.",
            force=True,
        )

        return result.content or ""

    def _parse_subtasks(self, plan_result: str) -> list[SubTask]:
        """Parse subtasks from plan result."""
        subtasks: list[SubTask] = []
        for line in plan_result.splitlines():
            line = line.strip()
            if line.startswith("- ") or line.startswith("* "):
                subtasks.append(SubTask(prompt=line[2:].strip()))
            elif line and not line.startswith("#"):
                subtasks.append(SubTask(prompt=line))

        if not subtasks:
            # If no subtasks parsed, treat entire result as one task
            subtasks.append(SubTask(prompt=plan_result))

        return subtasks

    def _aggregate_results(self, results: list[SubTaskResult]) -> str:
        """Aggregate subtask results."""
        parts = ["## Task Execution Results\n"]
        for i, result in enumerate(results, 1):
            parts.append(f"\n### Subtask {i}\n{result.result}\n")
        return "\n".join(parts)
