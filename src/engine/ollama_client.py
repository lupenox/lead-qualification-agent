from __future__ import annotations

import logging
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel

from config import Settings, get_settings

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


def resolve_client_config(settings: Settings | None = None) -> tuple[str, str, str]:
    settings = settings or get_settings()
    provider = settings.llm_provider.lower()
    if provider == "groq":
        return (
            "https://api.groq.com/openai/v1",
            settings.groq_api_key or settings.llm_api_key,
            settings.default_model,
        )
    if provider == "openai":
        return (
            settings.openai_base_url,
            settings.openai_api_key or settings.llm_api_key,
            settings.default_model,
        )
    return settings.ollama_base_url, settings.llm_api_key or "ollama", settings.default_model


def build_raw_client(settings: Settings | None = None) -> OpenAI:
    base_url, api_key, _ = resolve_client_config(settings)
    return OpenAI(base_url=base_url, api_key=api_key or "ollama")


def get_structured_completion(
    prompt: str,
    response_model: type[T],
    system_prompt: str = "",
    settings: Settings | None = None,
) -> T:
    """Ask the configured model for a Pydantic object. Raises on transport/parse failure."""
    settings = settings or get_settings()
    _, _, model = resolve_client_config(settings)
    client = build_raw_client(settings)
    try:
        import instructor

        patched = instructor.from_openai(client, mode=instructor.Mode.JSON)
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return patched.chat.completions.create(
            model=model,
            response_model=response_model,
            messages=messages,
            temperature=0.0,
        )
    except Exception:
        logger.exception("Structured LLM call failed; caller should use the heuristic fallback")
        raise
