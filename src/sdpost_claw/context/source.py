"""Context Source interface and built-in implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Generic, TypeVar

A = TypeVar("A")


class Unavailable:
    """Represents a temporarily unavailable context source."""

    def __init__(self, reason: str = ""):
        self.reason = reason

    def __repr__(self) -> str:
        return f"Unavailable({self.reason!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Unavailable):
            return self.reason == other.reason
        return False


class ContextSource(ABC, Generic[A]):
    """
    Context Source - independently refreshable typed value.

    Each Context Source contributes a portion of the system context
    that is visible to the model.
    """

    @property
    @abstractmethod
    def key(self) -> str:
        """Stable namespace identifier, e.g. 'date/current'."""
        ...

    @abstractmethod
    async def load(self) -> A | Unavailable:
        """Load current value. Returns Unavailable if temporarily unobservable."""
        ...

    @abstractmethod
    def baseline(self, value: A) -> str:
        """First render to model-visible text."""
        ...

    @abstractmethod
    def update(self, previous: A, current: A) -> str:
        """Generate update text when value changes."""
        ...

    def removed(self, previous: A) -> str | None:
        """Optional removal text generator."""
        return None


# --- Value Types ---


@dataclass
class DateValue:
    date: str
    time: str
    timezone: str
    weekday: str


@dataclass
class Instruction:
    source: str
    content: str


@dataclass
class InstructionsValue:
    instructions: list[Instruction]


@dataclass
class SkillInfo:
    name: str
    description: str | None
    slash: bool = False


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


@dataclass
class AgentValue:
    name: str
    mode: str
    skills_count: int


# --- Built-in Context Sources ---


class DateContextSource(ContextSource[DateValue]):
    """Date and time context source."""

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
        return f"Date changed: {previous.date} -> {current.date}"


class ProjectInstructionsContextSource(ContextSource[InstructionsValue]):
    """Project instructions context source - discovers AGENTS.md / CLAUDE.md / .cursorrules etc."""

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
        instructions: list[Instruction] = []

        for pattern in self.INSTRUCTION_FILES:
            file_path = self.project_path / pattern
            if file_path.exists():
                try:
                    content = file_path.read_text(encoding="utf-8")
                    instructions.append(Instruction(
                        source=str(file_path),
                        content=content,
                    ))
                except (OSError, UnicodeDecodeError):
                    continue

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
            f"{len(previous.instructions)} -> {len(current.instructions)} files"
        )


class AgentSkillsContextSource(ContextSource[SkillsValue]):
    """Agent available skills context source."""

    def __init__(self, skills: list[SkillInfo] | None = None):
        self._skills = skills or []

    @property
    def key(self) -> str:
        return "agent/skills"

    async def load(self) -> SkillsValue | Unavailable:
        if not self._skills:
            return Unavailable("No skills available")
        return SkillsValue(skills=self._skills)

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


class WorkspaceMemoryContextSource(ContextSource[MemoryValue]):
    """Workspace memory context source."""

    key = "workspace/memory"

    def __init__(self, memory: MemoryValue | None = None):
        self._memory = memory

    async def load(self) -> MemoryValue | Unavailable:
        if not self._memory:
            return Unavailable("No workspace memory")
        return self._memory

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

    def update(self, previous: MemoryValue, current: MemoryValue) -> str:
        return "Workspace memory updated"


class UserPreferencesContextSource(ContextSource[PreferencesValue]):
    """User preferences context source."""

    key = "user/preferences"

    def __init__(self, preferences: PreferencesValue | None = None):
        self._preferences = preferences

    async def load(self) -> PreferencesValue | Unavailable:
        if not self._preferences:
            return Unavailable("No user preferences")
        return self._preferences

    def baseline(self, value: PreferencesValue) -> str:
        return (
            f"## User Preferences\n"
            f"Language: {value.language}\n"
            f"Style: {value.style}\n"
            f"Expertise: {value.expertise}"
        )

    def update(self, previous: PreferencesValue, current: PreferencesValue) -> str:
        return "User preferences updated"


class SummaryContextSource(ContextSource[SummaryValue]):
    """Session summary context source - injected after compaction."""

    key = "session/summary"

    def __init__(self, summary: str = ""):
        self._summary = summary

    def update_summary(self, summary: str) -> None:
        """Replace the held summary (called by the compaction loop post-run).

        The next baseline rebuild (``initialize`` / ``replace``) picks this up
        so the compacted summary is surfaced to the model as
        ``## Previous Session Summary``.
        """
        self._summary = summary

    async def load(self) -> SummaryValue | Unavailable:
        if not self._summary:
            return Unavailable("No summary available")
        return SummaryValue(summary=self._summary)

    def baseline(self, value: SummaryValue) -> str:
        return f"## Previous Session Summary\n{value.summary}"

    def update(self, previous: SummaryValue, current: SummaryValue) -> str:
        return "Session summary updated with new context."


class AgentContextSource(ContextSource[AgentValue]):
    """Agent info context source."""

    key = "agent/info"

    def __init__(self, agent_name: str = "sdpost", agent_mode: str = "build", skills_count: int = 0):
        self._agent_name = agent_name
        self._agent_mode = agent_mode
        self._skills_count = skills_count

    async def load(self) -> AgentValue | Unavailable:
        return AgentValue(
            name=self._agent_name,
            mode=self._agent_mode,
            skills_count=self._skills_count,
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
            return f"Agent mode changed: {previous.mode} -> {current.mode}"
        return "Agent info updated"
