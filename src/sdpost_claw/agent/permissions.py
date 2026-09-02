"""Permission System - Wildcard Ruleset inspired by opencode."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class PermissionRule:
    """
    Permission rule with wildcard matching.

    Supports:
    - "file.*" matches all file operations
    - "file.read.*" matches all read operations
    - "*" matches everything
    """

    action: str
    effect: str  # "allow" | "deny"
    priority: int = 0

    def __post_init__(self):
        # Convert wildcard to regex pattern
        escaped = re.escape(self.action).replace(r"\*", ".*")
        self._pattern = re.compile(f"^{escaped}$")

    def matches(self, action: str) -> bool:
        """Check if action matches this rule."""
        return bool(self._pattern.match(action))


@dataclass
class PermissionDecision:
    """Permission evaluation result."""
    effect: str  # "allow" | "deny" | "ask"
    rule: PermissionRule | None = None


class PermissionRuleset:
    """
    Permission ruleset - Last Match Wins strategy.

    Inspired by opencode's findLast approach.
    """

    def __init__(self):
        self._rules: list[PermissionRule] = []

    def allow(self, action: str, priority: int = 0) -> PermissionRule:
        """Add allow rule."""
        rule = PermissionRule(action=action, effect="allow", priority=priority)
        self._rules.append(rule)
        return rule

    def deny(self, action: str, priority: int = 0) -> PermissionRule:
        """Add deny rule."""
        rule = PermissionRule(action=action, effect="deny", priority=priority)
        self._rules.append(rule)
        return rule

    def remove_rule(self, action: str, effect: str | None = None) -> None:
        """Remove rules matching action."""
        self._rules = [
            r for r in self._rules
            if not (r.action == action and (effect is None or r.effect == effect))
        ]

    def evaluate(self, action: str) -> PermissionDecision:
        """
        Evaluate permission for an action.

        Strategy: highest priority matching rule wins.
        If same priority, last matching rule wins.
        """
        matching = [r for r in self._rules if r.matches(action)]
        if not matching:
            return PermissionDecision(effect="ask", rule=None)

        # Sort by priority descending, then by position (last first)
        matching.sort(key=lambda r: (r.priority, self._rules.index(r)), reverse=True)
        winner = matching[0]

        return PermissionDecision(effect=winner.effect, rule=winner)

    def is_allowed(self, action: str) -> bool:
        """Quick check if action is allowed."""
        return self.evaluate(action).effect == "allow"

    def is_denied(self, action: str) -> bool:
        """Quick check if action is denied."""
        return self.evaluate(action).effect == "deny"

    def list_rules(self) -> list[PermissionRule]:
        """List all rules."""
        return list(self._rules)

    def clear(self) -> None:
        """Clear all rules."""
        self._rules.clear()


class AgentPermissions:
    """Agent permission presets - build/plan/general modes."""

    @staticmethod
    def build() -> PermissionRuleset:
        """Build agent - full access."""
        ruleset = PermissionRuleset()
        ruleset.allow("*")
        return ruleset

    @staticmethod
    def plan() -> PermissionRuleset:
        """Plan agent - read-only access."""
        ruleset = PermissionRuleset()
        ruleset.allow("file.read", priority=5)
        ruleset.allow("file.list", priority=5)
        ruleset.allow("network.*", priority=5)
        ruleset.deny("file.write", priority=10)
        ruleset.deny("file.edit", priority=10)
        ruleset.deny("shell.*", priority=10)
        ruleset.deny("agent.spawn", priority=10)
        return ruleset

    @staticmethod
    def general() -> PermissionRuleset:
        """General agent - standard access."""
        ruleset = PermissionRuleset()
        ruleset.allow("*", priority=5)
        ruleset.deny("shell.rm", priority=10)
        return ruleset

    @staticmethod
    def custom(allow: list[str], deny: list[str]) -> PermissionRuleset:
        """Custom permissions."""
        ruleset = PermissionRuleset()
        for action in deny:
            ruleset.deny(action, priority=10)
        for action in allow:
            ruleset.allow(action, priority=5)
        return ruleset
