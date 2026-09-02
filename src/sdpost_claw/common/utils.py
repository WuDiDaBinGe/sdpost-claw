"""Utility functions."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any


def generate_id() -> str:
    """Generate a unique ID."""
    return str(uuid.uuid4())


def now() -> datetime:
    """Get current datetime."""
    return datetime.now()


def compute_sha256(content: str) -> str:
    """Compute SHA256 hash of content."""
    return hashlib.sha256(content.encode()).hexdigest()


def truncate_text(text: str, max_chars: int = 2000) -> tuple[str, bool]:
    """Truncate text to max_chars, preserving head and tail.

    Returns (truncated_text, was_truncated).
    """
    if len(text) <= max_chars:
        return text, False

    head_chars = max_chars // 2 - 100
    tail_chars = max_chars // 2 - 100
    truncated = text[:head_chars] + "\n... (truncated) ...\n" + text[-tail_chars:]
    return truncated, True


def count_tokens_approx(text: str) -> int:
    """Approximate token count (rough: 1 token ~= 4 chars for English, 1-2 chars for CJK)."""
    # Simple approximation
    return max(1, len(text) // 3)


def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def safe_json_dumps(obj: Any, **kwargs) -> str:
    """Safe JSON serialization with datetime handling."""
    def default(o):
        if isinstance(o, datetime):
            return o.isoformat()
        if hasattr(o, "__dict__"):
            return o.__dict__
        return str(o)

    import json
    return json.dumps(obj, default=default, ensure_ascii=False, **kwargs)
