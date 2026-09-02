"""Reconciliation logic for context sources."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sdpost_claw.context.registry import (
        SystemContextRegistry,
        Snapshot,
        ReconcileResult,
    )


async def reconcile_all(
    registry: SystemContextRegistry,
    snapshot: Snapshot,
) -> ReconcileResult:
    """Reconcile all context sources."""
    return await registry.reconcile(snapshot)


async def reconcile_single(
    registry: SystemContextRegistry,
    snapshot: Snapshot,
    key: str,
) -> ReconcileResult:
    """Reconcile a single context source."""
    # For now, just do full reconciliation
    # In the future, this could be optimized to only check one source
    return await registry.reconcile(snapshot)
