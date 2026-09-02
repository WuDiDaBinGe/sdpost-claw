"""Automation Scheduler - cron-based task scheduling."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from croniter import croniter

from sdpost_claw.common.utils import generate_id


@dataclass
class ScheduledTask:
    """Scheduled task."""
    id: str = field(default_factory=generate_id)
    name: str = ""
    session_id: str = ""
    cwd: str = ""
    prompt: str = ""
    cron: str = ""
    enabled: bool = True
    last_run: datetime | None = None
    next_run: datetime | None = None

    def should_run(self, now: datetime) -> bool:
        """Check if task should run."""
        if not self.enabled:
            return False
        if self.next_run is None:
            return self._compute_next_run(now)
        return now >= self.next_run

    def _compute_next_run(self, now: datetime) -> bool:
        """Compute if task should run based on cron."""
        try:
            itr = croniter(self.cron, now)
            next_run = itr.get_next(datetime)
            # If next run is in the past, we should run
            self.next_run = next_run
            return next_run <= now
        except Exception:
            return False

    def compute_next_run(self, base: datetime | None = None) -> datetime | None:
        """Compute next run time."""
        try:
            base_ = base or datetime.now() if self.last_run is None else self.last_run
            itr = croniter(self.cron, base_)
            self.next_run = itr.get_next(datetime)
            return self.next_run
        except Exception:
            return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "session_id": self.session_id,
            "cwd": self.cwd,
            "prompt": self.prompt,
            "cron": self.cron,
            "enabled": self.enabled,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "next_run": self.next_run.isoformat() if self.next_run else None,
        }


class AutomationScheduler:
    """
    Automation Scheduler - cron-based task scheduling.

    Integrates with Session Drain model:
    - Scheduled tasks trigger Session Drain
    - Supports cron expressions
    - Supports event triggers
    """

    def __init__(self):
        self._tasks: dict[str, ScheduledTask] = {}
        self._running = False
        self._task: asyncio.Task | None = None
        self._callbacks: list = []

    def on_task_due(self, callback) -> None:
        """Register callback for when task is due."""
        self._callbacks.append(callback)

    async def start(self) -> None:
        """Start the scheduler."""
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self) -> None:
        """Main scheduler loop."""
        while self._running:
            now = datetime.now()
            for task in list(self._tasks.values()):
                if task.should_run(now):
                    await self._execute_task(task)
            await asyncio.sleep(60)  # Check every minute

    async def _execute_task(self, task: ScheduledTask) -> None:
        """Execute a scheduled task."""
        task.last_run = datetime.now()
        task.compute_next_run()

        # Notify callbacks
        for callback in self._callbacks:
            try:
                await callback(task)
            except Exception:
                pass

    def add_task(self, task: ScheduledTask) -> None:
        """Add a scheduled task."""
        task.compute_next_run()
        self._tasks[task.id] = task

    def remove_task(self, task_id: str) -> None:
        """Remove a scheduled task."""
        self._tasks.pop(task_id, None)

    def get_task(self, task_id: str) -> ScheduledTask | None:
        """Get task by ID."""
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[ScheduledTask]:
        """List all tasks."""
        return list(self._tasks.values())

    def enable_task(self, task_id: str) -> None:
        """Enable a task."""
        task = self._tasks.get(task_id)
        if task:
            task.enabled = True

    def disable_task(self, task_id: str) -> None:
        """Disable a task."""
        task = self._tasks.get(task_id)
        if task:
            task.enabled = False
