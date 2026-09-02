"""SQLite Database - persistent storage with WAL mode."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


# Database schema
SCHEMA = """
-- Sessions table
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    cwd TEXT NOT NULL,
    title TEXT,
    agent_mode TEXT DEFAULT 'build',
    status TEXT DEFAULT 'active',
    context_epoch_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Context epochs table
CREATE TABLE IF NOT EXISTS context_epochs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    baseline TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    end_reason TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- Messages table
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    metadata TEXT,
    tokens INTEGER DEFAULT 0,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- Tool calls table
CREATE TABLE IF NOT EXISTS tool_calls (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    input TEXT,
    output TEXT,
    structured_output TEXT,
    externalized_path TEXT,
    is_truncated BOOLEAN DEFAULT FALSE,
    is_error BOOLEAN DEFAULT FALSE,
    duration_ms INTEGER,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- Audit log table
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    action TEXT NOT NULL,
    effect TEXT NOT NULL,
    rule TEXT,
    context TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- Scheduled tasks table
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    session_id TEXT,
    cwd TEXT NOT NULL,
    prompt TEXT NOT NULL,
    cron TEXT NOT NULL,
    enabled BOOLEAN DEFAULT TRUE,
    last_run TIMESTAMP,
    next_run TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Usage stats table
CREATE TABLE IF NOT EXISTS usage_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost REAL DEFAULT 0,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- Workspace memory table
CREATE TABLE IF NOT EXISTS workspace_memory (
    workspace_id TEXT PRIMARY KEY,
    daily_summary TEXT,
    recent_decisions TEXT DEFAULT '[]',
    key_facts TEXT DEFAULT '[]',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- User preferences table
CREATE TABLE IF NOT EXISTS user_preferences (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    language TEXT DEFAULT 'zh-CN',
    communication_style TEXT DEFAULT 'professional',
    expertise_level TEXT DEFAULT 'intermediate',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_log(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_context_epochs_session ON context_epochs(session_id, started_at);
CREATE INDEX IF NOT EXISTS idx_usage_session ON usage_stats(session_id, timestamp);
"""


class Database:
    """
    SQLite database with WAL mode for concurrent access.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._connection: sqlite3.Connection | None = None

    def connect(self) -> None:
        """Connect to database and initialize schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.db_path))
        self._connection.row_factory = sqlite3.Row
        # Enable WAL mode for better concurrent performance
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._initialize_schema()

    def close(self) -> None:
        """Close database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None

    def _initialize_schema(self) -> None:
        """Initialize database schema."""
        assert self._connection
        self._connection.executescript(SCHEMA)
        self._connection.commit()

    @property
    def connection(self) -> sqlite3.Connection:
        """Get database connection."""
        if not self._connection:
            self.connect()
        return self._connection

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute SQL."""
        return self.connection.execute(sql, params)

    def executemany(self, sql: str, params: list[tuple]) -> sqlite3.Cursor:
        """Execute SQL with multiple params."""
        return self.connection.executemany(sql, params)

    def commit(self) -> None:
        """Commit transaction."""
        self.connection.commit()

    def fetch_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        """Fetch single row."""
        cursor = self.execute(sql, params)
        return cursor.fetchone()

    def fetch_all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        """Fetch all rows."""
        cursor = self.execute(sql, params)
        return cursor.fetchall()

    # --- Session operations ---

    def save_session(self, session_data: dict[str, Any]) -> None:
        """Save or update session."""
        self.execute(
            """INSERT OR REPLACE INTO sessions (id, cwd, title, agent_mode, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                session_data["id"],
                session_data["cwd"],
                session_data.get("title", ""),
                session_data.get("agent_mode", "build"),
                session_data.get("status", "active"),
                session_data.get("created_at", datetime.now()),
                session_data.get("updated_at", datetime.now()),
            ),
        )
        self.commit()

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Get session by ID."""
        row = self.fetch_one("SELECT * FROM sessions WHERE id = ?", (session_id,))
        return dict(row) if row else None

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all sessions."""
        rows = self.fetch_all("SELECT * FROM sessions ORDER BY updated_at DESC")
        return [dict(r) for r in rows]

    def delete_session(self, session_id: str) -> None:
        """Delete session."""
        self.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self.commit()

    # --- Message operations ---

    def save_message(self, session_id: str, message: dict[str, Any]) -> None:
        """Save message."""
        import json
        self.execute(
            """INSERT INTO messages (id, session_id, role, content, metadata, tokens, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                message.get("id", str(__import__('uuid').uuid4())),
                session_id,
                message.get("role", ""),
                message.get("content", ""),
                json.dumps(message.get("metadata", {})),
                message.get("tokens", 0),
                message.get("timestamp", datetime.now().isoformat()),
            ),
        )
        self.commit()

    def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Get messages for session."""
        rows = self.fetch_all(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp",
            (session_id,),
        )
        return [dict(r) for r in rows]

    # --- Tool call operations ---

    def save_tool_call(self, session_id: str, tool_call: dict[str, Any]) -> None:
        """Save tool call."""
        import json
        self.execute(
            """INSERT INTO tool_calls (id, session_id, message_id, tool_name, input, output,
                  structured_output, externalized_path, is_truncated, is_error, duration_ms, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tool_call.get("id", str(__import__('uuid').uuid4())),
                session_id,
                tool_call.get("message_id", ""),
                tool_call.get("tool_name", ""),
                json.dumps(tool_call.get("input", {})),
                tool_call.get("output", ""),
                json.dumps(tool_call.get("structured_output")) if tool_call.get("structured_output") else None,
                tool_call.get("externalized_path"),
                tool_call.get("is_truncated", False),
                tool_call.get("is_error", False),
                tool_call.get("duration_ms", 0),
                tool_call.get("timestamp", datetime.now().isoformat()),
            ),
        )
        self.commit()

    # --- Audit operations ---

    def save_audit_log(self, session_id: str, action: str, effect: str, rule: str | None = None, context: dict | None = None) -> None:
        """Save audit log entry."""
        import json
        self.execute(
            """INSERT INTO audit_log (session_id, action, effect, rule, context, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, action, effect, rule, json.dumps(context) if context else None, datetime.now()),
        )
        self.commit()

    def get_audit_trail(self, session_id: str) -> list[dict[str, Any]]:
        """Get audit trail for session."""
        rows = self.fetch_all(
            "SELECT * FROM audit_log WHERE session_id = ? ORDER BY timestamp",
            (session_id,),
        )
        return [dict(r) for r in rows]

    # --- Usage stats ---

    def save_usage(self, session_id: str, model: str, input_tokens: int, output_tokens: int, cost: float = 0.0) -> None:
        """Save usage statistics."""
        self.execute(
            """INSERT INTO usage_stats (session_id, model, input_tokens, output_tokens, cost, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, model, input_tokens, output_tokens, cost, datetime.now()),
        )
        self.commit()

    def get_usage_summary(self, session_id: str) -> dict[str, Any]:
        """Get usage summary for session."""
        row = self.fetch_one(
            """SELECT SUM(input_tokens) as total_input, SUM(output_tokens) as total_output,
                      SUM(cost) as total_cost, COUNT(*) as request_count
               FROM usage_stats WHERE session_id = ?""",
            (session_id,),
        )
        return dict(row) if row else {}

    # --- Scheduled tasks ---

    def save_scheduled_task(self, task_data: dict[str, Any]) -> None:
        """Save scheduled task."""
        self.execute(
            """INSERT OR REPLACE INTO scheduled_tasks
               (id, name, session_id, cwd, prompt, cron, enabled, last_run, next_run, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_data["id"],
                task_data["name"],
                task_data.get("session_id"),
                task_data["cwd"],
                task_data["prompt"],
                task_data["cron"],
                task_data.get("enabled", True),
                task_data.get("last_run"),
                task_data.get("next_run"),
                task_data.get("created_at", datetime.now()),
            ),
        )
        self.commit()

    def get_scheduled_tasks(self) -> list[dict[str, Any]]:
        """Get all scheduled tasks."""
        rows = self.fetch_all("SELECT * FROM scheduled_tasks ORDER BY created_at")
        return [dict(r) for r in rows]

    def delete_scheduled_task(self, task_id: str) -> None:
        """Delete scheduled task."""
        self.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
        self.commit()

    # --- Workspace memory ---

    def get_workspace_memory(self, workspace_id: str) -> dict[str, Any] | None:
        """Get workspace memory."""
        import json
        row = self.fetch_one(
            "SELECT * FROM workspace_memory WHERE workspace_id = ?",
            (workspace_id,),
        )
        if not row:
            return None
        data = dict(row)
        data["recent_decisions"] = json.loads(data.get("recent_decisions", "[]"))
        data["key_facts"] = json.loads(data.get("key_facts", "[]"))
        return data

    def save_workspace_memory(self, workspace_id: str, data: dict[str, Any]) -> None:
        """Save workspace memory."""
        import json
        self.execute(
            """INSERT OR REPLACE INTO workspace_memory
               (workspace_id, daily_summary, recent_decisions, key_facts, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                workspace_id,
                data.get("daily_summary"),
                json.dumps(data.get("recent_decisions", [])),
                json.dumps(data.get("key_facts", [])),
                datetime.now(),
            ),
        )
        self.commit()

    # --- User preferences ---

    def get_preferences(self) -> dict[str, Any]:
        """Get user preferences."""
        row = self.fetch_one("SELECT * FROM user_preferences WHERE id = 1")
        if not row:
            return {"language": "zh-CN", "communication_style": "professional", "expertise_level": "intermediate"}
        return dict(row)

    def save_preferences(self, prefs: dict[str, Any]) -> None:
        """Save user preferences."""
        self.execute(
            """INSERT OR REPLACE INTO user_preferences (id, language, communication_style, expertise_level, updated_at)
               VALUES (1, ?, ?, ?, ?)""",
            (
                prefs.get("language", "zh-CN"),
                prefs.get("communication_style", "professional"),
                prefs.get("expertise_level", "intermediate"),
                datetime.now(),
            ),
        )
        self.commit()
