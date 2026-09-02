"""Model Providers - 国产大模型适配（全部基于 OpenAI 兼容协议）."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from sdpost_claw.agent.drain import ModelResponse, ToolCall


class ModelProvider(ABC):
    """Abstract model provider."""

    @abstractmethod
    async def generate(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        """Generate model response."""
        ...

    async def generate_stream(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_delta: Any = None,
    ) -> ModelResponse:
        """Streamed generation. ``on_delta(kind, chunk)`` receives
        incremental output where kind is ``"text"`` or ``"reasoning"``.
        Default: fall back to one-shot generate with a single delta.
        """
        response = await self.generate(system, messages, tools)
        if on_delta and response.text:
            on_delta("text", response.text)
        return response

    @abstractmethod
    async def count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        ...


# 国产模型 Base URL 映射（用于自动补全）
_PROVIDER_BASE_URLS: dict[str, str] = {
    "deepseek": "https://api.deepseek.com",
    "阿里云": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "智谱AI": "https://open.bigmodel.ai/api/paas/v4",
    "月之暗面": "https://api.moonshot.cn/v1",
    "火山引擎": "https://ark.cn-beijing.volces.com/api/v3",
    "百川智能": "https://api.baichuan-ai.com/v1",
    "MiniMax": "https://api.minimax.chat/v1",
    "阶跃星辰": "https://api.stepfun.com/v1",
}


class OpenAIProvider(ModelProvider):
    """
    OpenAI 兼容协议 Provider —— 适配所有国产大模型 API。

    DeepSeek、通义千问、智谱GLM、月之暗面、豆包、百川、MiniMax、阶跃星辰
    等国产厂商的 API 均兼容 OpenAI 协议，统一走此实现。
    """

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        base_url: str | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self._client = None

    def _get_client(self):
        """Lazy-init OpenAI client."""
        if self._client is None:
            from openai import AsyncOpenAI
            # Pass None when no key is configured so the SDK falls back to
            # OPENAI_API_KEY from the environment. If neither is set the SDK
            # raises a clear error at call time.
            self._client = AsyncOpenAI(
                api_key=self.api_key or None,
                base_url=self.base_url,
            )
        return self._client

    async def generate(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        """Generate using OpenAI-compatible API."""
        client = self._get_client()

        all_messages = [{"role": "system", "content": system}] + messages

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": all_messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = await client.chat.completions.create(**kwargs)

        # Parse response
        choice = response.choices[0]
        message = choice.message

        tool_calls: list[ToolCall] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    input=args,
                ))

        return ModelResponse(
            text=message.content or "",
            tool_calls=tool_calls,
            has_tool_calls=bool(tool_calls),
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                "total_tokens": response.usage.total_tokens if response.usage else 0,
            },
        )

    async def generate_stream(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_delta: Any = None,
    ) -> ModelResponse:
        """Streamed generation via OpenAI-compatible API.

        Parses ``delta.content`` (text) and ``delta.reasoning_content``
        (DeepSeek-R1/GLM thinking style) incrementally. Falls back to
        one-shot ``generate()`` when streaming is unsupported.
        """
        client = self._get_client()
        all_messages = [{"role": "system", "content": system}] + messages
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": all_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools

        try:
            stream = await client.chat.completions.create(**kwargs)
        except Exception:
            # Provider rejected streaming (or stream_options) — degrade.
            return await super().generate_stream(system, messages, tools, on_delta)

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: dict[int, dict[str, str]] = {}  # index -> {id, name, args}
        usage: dict[str, Any] | None = None

        async for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens or 0,
                    "completion_tokens": chunk.usage.completion_tokens or 0,
                    "total_tokens": chunk.usage.total_tokens or 0,
                }
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue

            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                reasoning_parts.append(reasoning)
                if on_delta:
                    on_delta("reasoning", reasoning)

            if delta.content:
                text_parts.append(delta.content)
                if on_delta:
                    on_delta("text", delta.content)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    slot = tool_calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["args"] += tc.function.arguments

        parsed_calls: list[ToolCall] = []
        for _idx in sorted(tool_calls):
            slot = tool_calls[_idx]
            try:
                args = json.loads(slot["args"]) if slot["args"] else {}
            except json.JSONDecodeError:
                args = {}
            parsed_calls.append(ToolCall(id=slot["id"], name=slot["name"], input=args))

        return ModelResponse(
            text="".join(text_parts),
            tool_calls=parsed_calls,
            has_tool_calls=bool(parsed_calls),
            usage=usage,
            reasoning="".join(reasoning_parts),
        )

    async def count_tokens(self, text: str) -> int:
        """Approximate token count."""
        return max(1, len(text) // 3)


def _resolve_base_url(provider_name: str, base_url: str | None) -> str | None:
    """若未提供 base_url，尝试从厂商名映射默认地址。"""
    if base_url:
        return base_url
    return _PROVIDER_BASE_URLS.get(provider_name.strip())


def create_provider(
    provider_name: str,
    api_key: str,
    model: str | None = None,
    base_url: str | None = None,
) -> ModelProvider:
    """
    工厂函数 —— 创建国产大模型 Provider。

    所有国产模型均基于 OpenAI 兼容协议，统一返回 OpenAIProvider。
    支持厂商：DeepSeek、通义千问（阿里云）、智谱AI、月之暗面、豆包（火山引擎）、
    百川智能、MiniMax、阶跃星辰 等。

    参数可来自 config.yaml 或用户通过 Web UI 配置。若缺少 base_url 会自动
    从厂商名映射默认地址；api_key 为空时走环境变量 OPENAI_API_KEY。
    """
    resolved_url = _resolve_base_url(provider_name, base_url)

    # 未指定模型名时按厂商推荐默认值
    default_models = {
        "deepseek": "deepseek-chat",
        "阿里云": "qwen-max",
        "智谱AI": "glm-4-plus",
        "月之暗面": "moonshot-v1-8k",
        "火山引擎": "doubao-pro-128k",
        "百川智能": "baichuan2-turbo",
    }
    default_model = default_models.get(provider_name.strip(), model or "deepseek-chat")

    return OpenAIProvider(
        api_key=api_key,
        model=model or default_model,
        base_url=resolved_url,
    )
