"""The LLM service the rest of the platform calls.

Responsibilities:
  * choose the model for a task class (advice / narrative / classify), honouring per-firm
    overrides set in the Admin console;
  * resolve the API key from the firm's in-app config first, then the environment;
  * route to the default provider (Anthropic) and fall back to a secondary provider;
  * ALWAYS return something usable — when no key is configured or a call fails, it returns a
    deterministic, clearly-labelled fallback so regulated workflows still produce an artefact."""
from __future__ import annotations

from typing import Callable

from app.core.config import settings
from app.core.logging import get_logger
from app.llm.base import LLMMessage, LLMResult
from app.llm.providers import get_provider

log = get_logger("aurea.llm")

TASK_DEFAULTS = {
    "advice": settings.model_advice,
    "narrative": settings.model_narrative,
    "classify": settings.model_classify,
}


def firm_llm_creds(firm) -> dict:
    """Credentials for a firm: its in-app config (Admin) overrides the environment."""
    cfg = (getattr(firm, "llm_config", None) or {})
    return {
        "anthropic_api_key": cfg.get("anthropic_api_key") or settings.anthropic_api_key,
        "openai_api_key": cfg.get("openai_api_key") or settings.openai_api_key,
    }


class LLMService:
    def __init__(self) -> None:
        self.primary = "anthropic"
        self.secondary = "openai"

    def model_for(self, task: str, firm_model_config: dict | None = None) -> str:
        if firm_model_config and firm_model_config.get(task):
            return firm_model_config[task]
        return TASK_DEFAULTS.get(task, settings.model_advice)

    def _keys(self, creds: dict | None) -> dict:
        creds = creds or {}
        return {
            "anthropic": creds.get("anthropic_api_key") or settings.anthropic_api_key,
            "openai": creds.get("openai_api_key") or settings.openai_api_key,
        }

    def enabled(self, creds: dict | None = None) -> bool:
        k = self._keys(creds)
        return bool(k["anthropic"] or k["openai"])

    async def generate(
        self,
        *,
        task: str,
        system: str,
        prompt: str,
        firm_model_config: dict | None = None,
        creds: dict | None = None,
        max_tokens: int = 1200,
        temperature: float = 0.4,
        fallback: Callable[[], str] | None = None,
        pii_terms: list[str] | None = None,
        pii_categories: list[str] | None = None,
        redact: bool = False,
        allow_fallback: bool = True,
        force_fallback: bool = False,
    ) -> LLMResult:
        model = self.model_for(task, firm_model_config)
        keys = self._keys(creds)

        # PII redaction — mask client identifiers before anything leaves the process.
        mapping: dict[str, str] = {}
        if redact:
            from app.llm import redaction
            system, m1 = redaction.redact(system, pii_terms or [], pii_categories)
            prompt, m2 = redaction.redact(prompt, pii_terms or [], pii_categories)
            mapping = {**m1, **m2}
        messages = [LLMMessage(role="user", content=prompt)]

        # Cost cap reached → don't call a live model; fall through to the deterministic path.
        providers = () if force_fallback else (
            (self.primary,) if not allow_fallback else (self.primary, self.secondary))
        for provider_name in providers:
            key = keys["anthropic"] if provider_name == "anthropic" else keys["openai"]
            if not key:
                continue
            provider = get_provider(provider_name)
            use_model = model if provider_name == "anthropic" else settings.openai_model
            try:
                result = await provider.complete(
                    system=system, messages=messages, model=use_model, api_key=key,
                    max_tokens=max_tokens, temperature=temperature,
                )
                if mapping:
                    from app.llm import redaction
                    result.text = redaction.restore(result.text, mapping)
                    result.redacted_count = len(mapping)
                return result
            except Exception as exc:  # pragma: no cover - network dependent
                log.warning("llm_provider_failed", provider=provider_name, error=str(exc))
                continue

        text = fallback() if fallback else _generic_fallback(system, prompt)
        return LLMResult(text=text, model="deterministic-fallback", provider="none", is_fallback=True)


def _generic_fallback(system: str, prompt: str) -> str:
    return (
        "[Deterministic fallback — no LLM provider configured]\n\n"
        "An AI-authored narrative is unavailable because no model provider is connected. "
        "The structured data, evidence and lineage behind this item remain complete and "
        "auditable; configure an Anthropic API key in Configuration → Models to enable "
        "plain-language rationale."
    )


llm_service = LLMService()
