"""LLM provider factory."""
from __future__ import annotations

from src.config import ModelProfile
from src.llm.base import BaseLLMProvider
from src.llm.mock_provider import MockProvider


def create_provider_from_profile(profile: ModelProfile, log_level: str = "info") -> BaseLLMProvider:
    provider_name = profile.provider.lower()
    if provider_name == "mock":
        return MockProvider(model=profile.model)
    if provider_name == "openai":
        from src.llm.openai_provider import OpenAIProvider
        return OpenAIProvider(
            api_url=profile.api_url,
            api_key=profile.api_key,
            model=profile.model,
            timeout_secs=profile.timeout_secs,
            max_retries=profile.max_retries,
            max_tokens=profile.max_tokens,
            temperature=profile.temperature,
            log_level=log_level,
        )
    if provider_name == "anthropic":
        from src.llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider(
            api_url=profile.api_url,
            api_key=profile.api_key,
            model=profile.model,
            timeout_secs=profile.timeout_secs,
            max_retries=profile.max_retries,
            max_tokens=profile.max_tokens,
            temperature=profile.temperature,
            log_level=log_level,
        )
    raise ValueError(f"Unknown provider: {profile.provider}")
