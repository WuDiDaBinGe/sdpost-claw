"""Output Externalization - store large tool outputs to files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiofiles

from sdpost_claw.common.utils import truncate_text


@dataclass
class ExternalizedOutput:
    """Externalized output reference."""
    original_size: int
    truncated_size: int
    file_path: str
    is_truncated: bool


class OutputExternalizer:
    """
    Output Externalizer - stores large tool outputs to files.

    When tool output exceeds max_output_chars:
    1. Store full output to file
    2. Return truncated version to model
    3. Reference file path for full content
    """

    def __init__(self, base_path: Path):
        self.base_path = base_path / "tool_outputs"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str, tool_call_id: str) -> Path:
        """Get output file path."""
        session_path = self.base_path / session_id
        session_path.mkdir(parents=True, exist_ok=True)
        return session_path / f"{tool_call_id}.txt"

    async def externalize(
        self,
        session_id: str,
        tool_call_id: str,
        output: str,
        max_chars: int = 2000,
    ) -> ExternalizedOutput:
        """
        Externalize output if too large.

        Returns reference with truncated content for model.
        """
        original_size = len(output)

        if original_size <= max_chars:
            return ExternalizedOutput(
                original_size=original_size,
                truncated_size=original_size,
                file_path="",
                is_truncated=False,
            )

        # Store full output to file
        file_path = self._path(session_id, tool_call_id)
        async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
            await f.write(output)

        # Truncate for model
        truncated, _ = truncate_text(output, max_chars)

        return ExternalizedOutput(
            original_size=original_size,
            truncated_size=len(truncated),
            file_path=str(file_path),
            is_truncated=True,
        )

    async def read_full_output(self, file_path: str) -> str | None:
        """Read full output from externalized file."""
        path = Path(file_path)
        if not path.exists():
            return None
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            return await f.read()

    async def cleanup_session(self, session_id: str) -> None:
        """Clean up externalized outputs for a session."""
        session_path = self.base_path / session_id
        if session_path.exists():
            import shutil
            shutil.rmtree(session_path)
