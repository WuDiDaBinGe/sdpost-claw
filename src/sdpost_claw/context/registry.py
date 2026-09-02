"""System Context Registry - manages and coordinates context sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sdpost_claw.context.source import ContextSource, Unavailable


@dataclass
class SourceSnapshot:
    """Single source snapshot."""
    value: Any
    removed: str | None = None


@dataclass
class Snapshot:
    """Context snapshot - comparable JSON state."""
    entries: dict[str, SourceSnapshot] = field(default_factory=dict)


@dataclass
class Generation:
    """Context generation - baseline + snapshot."""
    baseline: str
    snapshot: Snapshot


# --- Reconcile Results ---


@dataclass
class ReconcileResult:
    """Base reconcile result."""
    tag: str = ""


@dataclass
class Unchanged(ReconcileResult):
    """No changes detected."""
    tag: str = "Unchanged"


@dataclass
class Updated(ReconcileResult):
    """Has updates."""
    text: str = ""
    snapshot: Snapshot = field(default_factory=Snapshot)
    tag: str = "Updated"


@dataclass
class ReplacementReady(ReconcileResult):
    """Ready to replace baseline."""
    generation: Generation = field(default_factory=lambda: Generation(baseline="", snapshot=Snapshot()))
    tag: str = "ReplacementReady"


@dataclass
class ReplacementBlocked(ReconcileResult):
    """Replacement blocked by unavailable sources."""
    unavailable_keys: list[str] = field(default_factory=list)
    tag: str = "ReplacementBlocked"


# --- Replacement Results ---


@dataclass
class ReplacementResult:
    """Base replacement result."""
    tag: str = ""


@dataclass
class ReplacementReadyRep(ReplacementResult):
    """Ready for replacement."""
    generation: Generation = field(default_factory=lambda: Generation(baseline="", snapshot=Snapshot()))
    tag: str = "ReplacementReady"


@dataclass
class ReplacementBlockedRep(ReplacementResult):
    """Replacement blocked."""
    unavailable_keys: list[str] = field(default_factory=list)
    tag: str = "ReplacementBlocked"


class DuplicateKeyError(Exception):
    """Raised when registering a duplicate context source key."""
    pass


class InitializationBlocked(Exception):
    """Raised when initialization is blocked by unavailable sources."""
    def __init__(self, unavailable_keys: list[str]):
        self.unavailable_keys = unavailable_keys
        super().__init__(f"Initialization blocked: {unavailable_keys}")


class SystemContextRegistry:
    """
    System Context Registry - manages ordered, scoped context contributors.

    Inspired by opencode's System Context Registry design.
    """

    def __init__(self):
        self._sources: dict[str, ContextSource] = {}
        self._order: list[str] = []

    def register(self, source: ContextSource) -> None:
        """Register a context source."""
        if source.key in self._sources:
            raise DuplicateKeyError(f"Duplicate context source key: {source.key}")
        self._sources[source.key] = source
        self._order.append(source.key)

    def unregister(self, key: str) -> None:
        """Unregister a context source."""
        self._sources.pop(key, None)
        if key in self._order:
            self._order.remove(key)

    def get_source(self, key: str) -> ContextSource | None:
        """Get a context source by key."""
        return self._sources.get(key)

    @property
    def keys(self) -> list[str]:
        """Get all registered source keys."""
        return list(self._order)

    async def initialize(self) -> Generation:
        """
        Initialize System Context.

        Generates baseline text and snapshot from all registered sources.
        Sources that are temporarily unavailable are skipped (they simply
        contribute nothing to the baseline); raising here would make the
        whole context system fall back whenever any optional source
        (e.g. project instructions) is missing.
        """
        baseline_parts: list[str] = []
        snapshot = Snapshot()

        for key in self._order:
            source = self._sources[key]
            value = await source.load()

            if isinstance(value, Unavailable):
                continue

            baseline_parts.append(source.baseline(value))
            snapshot.entries[key] = SourceSnapshot(value=value)

        return Generation(
            baseline="\n\n".join(baseline_parts),
            snapshot=snapshot,
        )

    async def reconcile(self, snapshot: Snapshot) -> ReconcileResult:
        """
        Reconcile context - compare current values with snapshot.

        Returns:
        - Unchanged: No changes
        - Updated: Has updates, generate Mid-Conversation System Message
        - ReplacementReady: Need to replace baseline (e.g., after compaction)
        - ReplacementBlocked: Replacement blocked (unavailable context)
        """
        updates: list[str] = []
        new_snapshot = Snapshot()
        has_changes = False

        for key in self._order:
            source = self._sources[key]
            value = await source.load()

            if isinstance(value, Unavailable):
                # Unavailable - keep last state
                if key in snapshot.entries:
                    new_snapshot.entries[key] = snapshot.entries[key]
                continue

            old_entry = snapshot.entries.get(key)

            if old_entry is None:
                # Newly registered source
                updates.append(source.baseline(value))
                new_snapshot.entries[key] = SourceSnapshot(value=value)
                has_changes = True
            elif old_entry.value != value:
                # Value changed
                updates.append(source.update(old_entry.value, value))
                new_snapshot.entries[key] = SourceSnapshot(value=value)
                has_changes = True
            else:
                new_snapshot.entries[key] = old_entry

        if not has_changes:
            return Unchanged()

        return Updated(
            text="\n".join(updates),
            snapshot=new_snapshot,
        )

    async def replace(self, snapshot: Snapshot) -> ReplacementResult:
        """
        Completely replace System Context.
        Used after compaction or session migration.
        """
        unavailable_keys: list[str] = []
        baseline_parts: list[str] = []
        new_snapshot = Snapshot()

        for key in self._order:
            source = self._sources[key]
            value = await source.load()

            if isinstance(value, Unavailable):
                # Committed source became unobservable -> replacement is
                # blocked because dropping it would lose context.
                if key in snapshot.entries:
                    unavailable_keys.append(key)
                continue

            baseline_parts.append(source.baseline(value))
            new_snapshot.entries[key] = SourceSnapshot(value=value)

        if unavailable_keys:
            return ReplacementBlockedRep(unavailable_keys=unavailable_keys)

        return ReplacementReadyRep(
            generation=Generation(
                baseline="\n\n".join(baseline_parts),
                snapshot=new_snapshot,
            ),
        )

    def __len__(self) -> int:
        return len(self._sources)

    def __contains__(self, key: str) -> bool:
        return key in self._sources
