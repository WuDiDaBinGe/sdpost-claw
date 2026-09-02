"""Multi-Source Skill Discovery - inspired by opencode skill.ts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class SkillInfo:
    """Skill information."""
    name: str
    description: str | None
    slash: bool
    location: Path
    content: str


@dataclass
class Source:
    """Skill source."""
    type: str  # "embedded" | "directory" | "url"
    path: str | None = None
    url: str | None = None
    skill: SkillInfo | None = None


class SkillSource:
    """Skill source factory."""

    @staticmethod
    def embedded(skill: SkillInfo) -> Source:
        """Create embedded skill source."""
        return Source(type="embedded", skill=skill)

    @staticmethod
    def directory(path: Path) -> Source:
        """Create directory skill source."""
        return Source(type="directory", path=str(path))

    @staticmethod
    def url(url: str) -> Source:
        """Create URL skill source."""
        return Source(type="url", url=url)


class SkillRegistry:
    """
    Skill Registry - multi-source skill discovery.

    Supports:
    - Embedded skills (built-in)
    - Directory skills (local filesystem)
    - URL skills (remote)
    """

    def __init__(self):
        self._sources: list[Source] = []
        self._cache: dict[str, list[SkillInfo]] = {}

    def add_source(self, source: Source) -> None:
        """Add a skill source."""
        self._sources.append(source)
        self._cache.clear()

    async def list_all(self) -> list[SkillInfo]:
        """List all skills from all sources."""
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
        """Get skills available for an agent (with permission filtering)."""
        return await self.list_all()

    async def _load_from_source(self, source: Source) -> list[SkillInfo]:
        """Load skills from a source."""
        if source.type == "embedded" and source.skill:
            return [source.skill]

        if source.type == "directory" and source.path:
            return await self._load_from_directory(Path(source.path))

        if source.type == "url" and source.url:
            return await self._load_from_url(source.url)

        return []

    async def _load_from_directory(self, directory: Path) -> list[SkillInfo]:
        """Load skills from a directory."""
        skills: list[SkillInfo] = []

        if not directory.exists():
            return skills

        # Find all markdown files
        patterns = ["*.md", "**/SKILL.md", "**/skill.md"]
        found_files: set[Path] = set()

        for pattern in patterns:
            for filepath in directory.rglob(pattern):
                if filepath.is_file() and filepath not in found_files:
                    found_files.add(filepath)

        for filepath in found_files:
            try:
                content = filepath.read_text(encoding="utf-8")
                frontmatter = self._parse_frontmatter(content)

                if frontmatter is None:
                    continue

                name = frontmatter.get("name")
                if name is None:
                    if filepath.name.lower() in ("skill.md",):
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
            except (OSError, UnicodeDecodeError):
                continue

        return skills

    async def _load_from_url(self, url: str) -> list[SkillInfo]:
        """Load skills from a URL."""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        content = await response.text()
                        return self._parse_remote_skills(content)
        except Exception:
            pass
        return []

    def _parse_frontmatter(self, content: str) -> dict[str, Any] | None:
        """Parse YAML frontmatter from markdown content."""
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not match:
            return None
        try:
            return yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            return None

    def _parse_remote_skills(self, content: str) -> list[SkillInfo]:
        """Parse remote skill content."""
        # For now, treat as single skill
        frontmatter = self._parse_frontmatter(content)
        if frontmatter:
            return [SkillInfo(
                name=frontmatter.get("name", "remote-skill"),
                description=frontmatter.get("description"),
                slash=frontmatter.get("slash", False),
                location=Path("/remote"),
                content=content,
            )]
        return []

    def _source_key(self, source: Source) -> str:
        """Generate cache key for a source."""
        if source.type == "embedded":
            return f"embedded:{source.skill.name if source.skill else 'unknown'}"
        elif source.type == "directory":
            return f"directory:{source.path}"
        elif source.type == "url":
            return f"url:{source.url}"
        return "unknown"


async def discover_skills(paths: list[Path]) -> list[SkillInfo]:
    """Discover skills from multiple paths."""
    registry = SkillRegistry()
    for path in paths:
        registry.add_source(SkillSource.directory(path))
    return await registry.list_all()
