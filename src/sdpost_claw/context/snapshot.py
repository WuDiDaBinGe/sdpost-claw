"""Context Snapshot - for comparing context state across epochs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sdpost_claw.context.registry import Snapshot, SourceSnapshot


def compute_snapshot_diff(old: Snapshot, new: Snapshot) -> dict[str, Any]:
    """Compute diff between two snapshots."""
    diff: dict[str, Any] = {
        "added": [],
        "removed": [],
        "changed": [],
        "unchanged": [],
    }

    all_keys = set(old.entries.keys()) | set(new.entries.keys())

    for key in all_keys:
        old_entry = old.entries.get(key)
        new_entry = new.entries.get(key)

        if old_entry is None and new_entry is not None:
            diff["added"].append(key)
        elif old_entry is not None and new_entry is None:
            diff["removed"].append(key)
        elif old_entry is not None and new_entry is not None:
            if old_entry.value != new_entry.value:
                diff["changed"].append(key)
            else:
                diff["unchanged"].append(key)

    return diff


def merge_snapshots(base: Snapshot, override: Snapshot) -> Snapshot:
    """Merge two snapshots, with override taking precedence."""
    merged = Snapshot()
    merged.entries.update(base.entries)
    merged.entries.update(override.entries)
    return merged
