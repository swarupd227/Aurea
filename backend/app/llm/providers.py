"""Concrete LLM providers. Anthropic-first (spec §5 build posture), OpenAI optional.

API keys are passed in per call (resolved by the LLM service from the firm's in-app config or
the environment), so a key set in the Admin console takes effect without a restart."""
from __future__ import annotations

from typing import Any, AsyncGenerator

from app.core.config import settings
from app.core.logging import get_logger
from app.llm.base import LLMMessage, LLMResult

log = get_logger("aurea.llm")

# Cache one client per key so we don't rebuild on every call.
_anthropic_clients: dict[str, object] = {}
_openai_clients: dict[str, object] = {}


class AnthropicProvider:
    name = "anthropic"

    def _client(self, api_key: str):
        if api_key not in _anthropic_clients:
            from anthropic import AsyncAnthropic

            _anthropic_clients[api_key] = AsyncAnthropic(api_key=api_key)
        return _anthropic_clients[api_key]

    async def complete(
        self, *, system: str, messages: list[LLMMessage], model: str, api_key: str,
        max_tokens: int = 1024, temperature: float = 0.4,
    ) -> LLMResult:
        kwargs = dict(
            model=model, system=system, max_tokens=max_tokens,
            messages=[{"role": m.role, "content": m.content} for m in messages],
        )
        # Some newer models (e.g. claude-opus-4-8) deprecate the `temperature` parameter.
        if "opus-4-8" not in model:
            kwargs["temperature"] = temperature
        resp = await self._client(api_key).messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return LLMResult(text=text, model=model, provider=self.name, stop_reason=resp.stop_reason,
                         usage={"input_tokens": resp.usage.input_tokens,
                                "output_tokens": resp.usage.output_tokens})

    async def stream(
        self, *, system: str, messages: list[dict[str, Any]], model: str, api_key: str,
        tools: list[dict[str, Any]] | None = None, max_tokens: int = 1024, temperature: float = 0.4,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream a completion. `messages` is raw Anthropic message-format dicts (not
        LLMMessage) — content may be a plain string or a list of content blocks
        (text/tool_use/tool_result), so a multi-round tool-use loop can pass back its
        own turn plus the tool results Claude is waiting on.

        Yields {"type": "text", "text": delta} chunks as prose arrives, then a final
        {"type": "done", "tool_uses": [...], "content_blocks": [...], "stop_reason": ...}.
        Tool-use input JSON is never streamed as text — the SDK's text_stream only
        carries text content blocks, so a tool call Claude decides to make is never
        partially visible to the user. content_blocks is Claude's full turn (text +
        tool_use, JSON-serializable dicts) for the caller to append as the next
        "assistant" message if it continues the loop with tool results.
        """
        kwargs: dict[str, Any] = dict(model=model, system=system, max_tokens=max_tokens, messages=messages)
        if "opus-4-8" not in model:
            kwargs["temperature"] = temperature
        if tools:
            kwargs["tools"] = tools

        async with self._client(api_key).messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield {"type": "text", "text": text}
            final = await stream.get_final_message()

        content_blocks = []
        tool_uses = []
        for b in final.content:
            btype = getattr(b, "type", "")
            if btype == "text":
                content_blocks.append({"type": "text", "text": b.text})
            elif btype == "tool_use":
                content_blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                tool_uses.append({"id": b.id, "name": b.name, "input": b.input})

        yield {
            "type": "done", "tool_uses": tool_uses, "content_blocks": content_blocks,
            "stop_reason": final.stop_reason,
        }


class OpenAIProvider:
    name = "openai"

    def _client(self, api_key: str):
        if api_key not in _openai_clients:
            from openai import AsyncOpenAI

            _openai_clients[api_key] = AsyncOpenAI(api_key=api_key)
        return _openai_clients[api_key]

    async def complete(
        self, *, system: str, messages: list[LLMMessage], model: str, api_key: str,
        max_tokens: int = 1024, temperature: float = 0.4,
    ) -> LLMResult:
        resp = await self._client(api_key).chat.completions.create(
            model=model or settings.openai_model, max_tokens=max_tokens, temperature=temperature,
            messages=[{"role": "system", "content": system}]
            + [{"role": m.role, "content": m.content} for m in messages],
        )
        choice = resp.choices[0]
        return LLMResult(text=choice.message.content or "", model=model, provider=self.name,
                         stop_reason=choice.finish_reason)


def get_provider(name: str) -> AnthropicProvider | OpenAIProvider | None:
    if name == "anthropic":
        return AnthropicProvider()
    if name == "openai":
        return OpenAIProvider()
    return None
