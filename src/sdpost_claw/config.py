"""Configuration management for sdpost-claw."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Default paths
DEFAULT_SDPOST_HOME = Path.home() / ".sdpost"
DEFAULT_CONFIG_PATH = DEFAULT_SDPOST_HOME / "config.yaml"
DEFAULT_DB_PATH = DEFAULT_SDPOST_HOME / "database.db"


@dataclass
class ModelConfig:
    """Model provider configuration."""

    provider: str = "deepseek"  # deepseek | 阿里云 | 智谱 | 月之暗面 | 百川 | 火山
    model: str = "deepseek-chat"
    api_key: str = ""
    base_url: str | None = None
    max_tokens: int = 128000
    temperature: float = 0.7


@dataclass
class ModelEntry:
    """A single model entry (one model = one entry)."""

    id: str
    name: str
    provider: str
    model: str
    api_key: str = ""
    base_url: str = ""
    enabled: bool = True


@dataclass
class RoutingConfig:
    """Model routing configuration."""

    lite_model: str = "deepseek-chat"
    default_model: str = "deepseek-v3"
    craft_model: str = "qwen-max"


# 国产模型默认列表（全部走 OpenAI 兼容协议）
DEFAULT_MODELS: list[dict[str, Any]] = [
    {
        "id": "deepseek-chat",
        "name": "DeepSeek Chat",
        "provider": "DeepSeek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
    },
    {
        "id": "deepseek-v3",
        "name": "DeepSeek V3",
        "provider": "DeepSeek",
        "model": "deepseek-v3",
        "base_url": "https://api.deepseek.com",
    },
    {
        "id": "qwen-plus",
        "name": "通义千问 Plus",
        "provider": "阿里云",
        "model": "qwen-plus",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    {
        "id": "qwen-max",
        "name": "通义千问 Max",
        "provider": "阿里云",
        "model": "qwen-max",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    {
        "id": "glm-4-plus",
        "name": "智谱 GLM-4 Plus",
        "provider": "智谱AI",
        "model": "glm-4-plus",
        "base_url": "https://open.bigmodel.ai/api/paas/v4",
    },
    {
        "id": "moonshot-v1-8k",
        "name": "月之暗面 Moonshot 8K",
        "provider": "月之暗面",
        "model": "moonshot-v1-8k",
        "base_url": "https://api.moonshot.cn/v1",
    },
    {
        "id": "doubao-pro-128k",
        "name": "豆包 Doubao Pro 128K",
        "provider": "火山引擎",
        "model": "doubao-pro-128k",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    },
    {
        "id": "baichuan2-turbo",
        "name": "百川 Baichuan2 Turbo",
        "provider": "百川智能",
        "model": "baichuan2-turbo",
        "base_url": "https://api.baichuan-ai.com/v1",
    },
]


@dataclass
class CompactionConfig:
    """Context compaction configuration."""

    enabled: bool = True
    max_tokens: int = 100000
    buffer_tokens: int = 20000
    keep_tokens: int = 8000


@dataclass
class PermissionConfig:
    """Permission configuration."""

    default_mode: str = "build"  # build | plan | general
    rules: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Config:
    """Main configuration."""

    # Paths
    sdpost_home: Path = DEFAULT_SDPOST_HOME
    db_path: Path = DEFAULT_DB_PATH

    # Model
    model: ModelConfig = field(default_factory=ModelConfig)
    routing: RoutingConfig = field(default_factory=RoutingConfig)

    # Models list (model-level entries, each is one model)
    models: list[ModelEntry] = field(default_factory=list)

    # Context
    compaction: CompactionConfig = field(default_factory=CompactionConfig)

    # Permissions
    permissions: PermissionConfig = field(default_factory=PermissionConfig)

    # UI
    theme: str = "default"
    language: str = "zh-CN"

    # Extensions
    skill_dirs: list[str] = field(default_factory=list)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)

    # Logging
    log_level: str = "INFO"
    audit_enabled: bool = True

    @property
    def all_models(self) -> list[ModelEntry]:
        """Get all visible models (defaults + user-configured, enabled only).

        Disabled entries (e.g. defaults hidden via batch delete) are excluded
        so deleted models disappear from the settings list and the model
        dropdown. Re-adding a model with the same id re-enables it.
        """
        existing_ids = {m.id for m in self.models}
        result: list[ModelEntry] = []
        for dm in DEFAULT_MODELS:
            if dm["id"] not in existing_ids:
                result.append(ModelEntry(
                    id=dm["id"],
                    name=dm["name"],
                    provider=dm["provider"],
                    model=dm["model"],
                    base_url=dm["base_url"],
                    enabled=True,
                ))
        result.extend(self.models)
        return [m for m in result if m.enabled]

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """Load configuration from file."""
        config_path = path or DEFAULT_CONFIG_PATH
        if not config_path.exists():
            return cls()

        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> Config:
        """Create Config from dictionary."""
        config = cls()

        # Paths
        if "sdpost_home" in data:
            config.sdpost_home = Path(data["sdpost_home"])
        if "db_path" in data:
            config.db_path = Path(data["db_path"])

        # Model (backward compatible)
        if "model" in data:
            model_data = data["model"]
            config.model = ModelConfig(
                provider=model_data.get("provider", config.model.provider),
                model=model_data.get("model", config.model.model),
                api_key=model_data.get("api_key", config.model.api_key),
                base_url=model_data.get("base_url"),
                max_tokens=model_data.get("max_tokens", config.model.max_tokens),
                temperature=model_data.get("temperature", config.model.temperature),
            )

        # Models (model-level entries)
        if "models" in data and isinstance(data["models"], list):
            models: list[ModelEntry] = []
            for m in data["models"]:
                models.append(ModelEntry(
                    id=m.get("id", ""),
                    name=m.get("name", m.get("id", "")),
                    provider=m.get("provider", ""),
                    model=m.get("model", m.get("id", "")),
                    api_key=m.get("api_key", ""),
                    base_url=m.get("base_url", ""),
                    enabled=m.get("enabled", True),
                ))
            config.models = models

        # Routing
        if "routing" in data:
            routing_data = data["routing"]
            config.routing = RoutingConfig(
                lite_model=routing_data.get("lite_model", config.routing.lite_model),
                default_model=routing_data.get("default_model", config.routing.default_model),
                craft_model=routing_data.get("craft_model", config.routing.craft_model),
            )

        # Compaction
        if "compaction" in data:
            comp_data = data["compaction"]
            config.compaction = CompactionConfig(
                enabled=comp_data.get("enabled", config.compaction.enabled),
                max_tokens=comp_data.get("max_tokens", config.compaction.max_tokens),
                buffer_tokens=comp_data.get("buffer_tokens", config.compaction.buffer_tokens),
                keep_tokens=comp_data.get("keep_tokens", config.compaction.keep_tokens),
            )

        # Permissions
        if "permissions" in data:
            perm_data = data["permissions"]
            config.permissions = PermissionConfig(
                default_mode=perm_data.get("default_mode", config.permissions.default_mode),
                rules=perm_data.get("rules", []),
            )

        # UI
        config.theme = data.get("theme", config.theme)
        config.language = data.get("language", config.language)

        # Extensions
        config.skill_dirs = data.get("skill_dirs", [])
        config.mcp_servers = data.get("mcp_servers", [])

        # Logging
        config.log_level = data.get("log_level", config.log_level)
        config.audit_enabled = data.get("audit_enabled", config.audit_enabled)

        return config

    def save(self, path: Path | None = None) -> None:
        """Save configuration to file."""
        config_path = path or DEFAULT_CONFIG_PATH
        config_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "model": {
                "provider": self.model.provider,
                "model": self.model.model,
                "api_key": self.model.api_key,
                "base_url": self.model.base_url,
                "max_tokens": self.model.max_tokens,
                "temperature": self.model.temperature,
            },
            "routing": {
                "lite_model": self.routing.lite_model,
                "default_model": self.routing.default_model,
                "craft_model": self.routing.craft_model,
            },
            "models": [
                {
                    "id": m.id,
                    "name": m.name,
                    "provider": m.provider,
                    "model": m.model,
                    "api_key": m.api_key,
                    "base_url": m.base_url,
                    "enabled": m.enabled,
                }
                for m in self.models
            ],
            "compaction": {
                "enabled": self.compaction.enabled,
                "max_tokens": self.compaction.max_tokens,
                "buffer_tokens": self.compaction.buffer_tokens,
                "keep_tokens": self.compaction.keep_tokens,
            },
            "permissions": {
                "default_mode": self.permissions.default_mode,
                "rules": self.permissions.rules,
            },
            "theme": self.theme,
            "language": self.language,
            "skill_dirs": self.skill_dirs,
            "mcp_servers": self.mcp_servers,
            "log_level": self.log_level,
            "audit_enabled": self.audit_enabled,
        }

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    def ensure_directories(self) -> None:
        """Ensure all required directories exist."""
        self.sdpost_home.mkdir(parents=True, exist_ok=True)
        (self.sdpost_home / "memory").mkdir(exist_ok=True)
        (self.sdpost_home / "skills").mkdir(exist_ok=True)
        (self.sdpost_home / "experts").mkdir(exist_ok=True)


def get_config() -> Config:
    """Get global configuration instance."""
    return Config.load()