"""Model Router - lite/default/craft tiered routing."""

from __future__ import annotations

from typing import Any

from sdpost_claw.agent.drain import ModelResponse, ToolCall
from sdpost_claw.runtime.providers import ModelProvider


class ModelRouter:
    """
    Model Router - tiered model selection.

    Tiers:
    - LITE: Fast, cheap model for simple tasks
    - DEFAULT: Standard model for most tasks
    - CRAFT: High-quality model for complex tasks
    """

    def __init__(self):
        self._providers: dict[str, ModelProvider] = {}
        self._tiers: dict[str, str] = {
            "LITE": "deepseek",
            "DEFAULT": "deepseek",
            "CRAFT": "阿里云",
        }
        self._tier_models: dict[str, str] = {
            "LITE": "deepseek-chat",
            "DEFAULT": "deepseek-v3",
            "CRAFT": "qwen-max",
        }

    def register(self, name: str, provider: ModelProvider) -> None:
        """Register a model provider."""
        self._providers[name] = provider

    def set_tier(self, tier: str, provider_name: str, model: str | None = None) -> None:
        """Set the provider for a tier."""
        self._tiers[tier] = provider_name
        if model:
            self._tier_models[tier] = model

    def get_provider(self, name: str | None = None, tier: str | None = None) -> ModelProvider:
        """Get a provider by name or tier."""
        if name:
            provider = self._providers.get(name)
            if not provider:
                raise ValueError(f"Provider not found: {name}")
            return provider

        if tier:
            provider_name = self._tiers.get(tier, "openai")
            provider = self._providers.get(provider_name)
            if not provider:
                raise ValueError(f"Provider not found for tier {tier}: {provider_name}")
            return provider

        raise ValueError("Must specify name or tier")

    async def generate(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tier: str = "DEFAULT",
    ) -> ModelResponse:
        """
        Generate model response using specified tier.

        Args:
            system: System context
            messages: Message history
            tools: Available tools
            tier: Model tier (LITE/DEFAULT/CRAFT)
        """
        provider = self.get_provider(tier=tier)
        return await provider.generate(
            system=system,
            messages=messages,
            tools=tools,
        )

    def select_tier_for_task(self, task_complexity: str = "normal") -> str:
        """Select appropriate tier based on task complexity."""
        complexity_map = {
            "simple": "LITE",
            "normal": "DEFAULT",
            "complex": "CRAFT",
        }
        return complexity_map.get(task_complexity, "DEFAULT")
