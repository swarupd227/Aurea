"""Provider-agnostic LLM interfaces. Anthropic is the default; others plug in behind these."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class LLMMessage:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class LLMResult:
    text: str
    model: str
    provider: str
    stop_reason: str | None = None
    usage: dict = field(default_factory=dict)
    # True when produced by the deterministic fallback rather than a live model.
    is_fallback: bool = False
    # Number of PII entities masked before the prompt was sent (0 if redaction off).
    redacted_count: int = 0


class LLMProvider(Protocol):
    name: str

    async def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.4,
    ) -> LLMResult: ...

    def available(self) -> bool: ...
