"""Multi-model registry with runtime switching."""
from __future__ import annotations

from typing import List

from src.config import Config, ModelProfile
from src.llm.base import BaseLLMProvider
from src.llm.factory import create_provider_from_profile


class ModelRegistry:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._profiles = config.get_model_profiles()
        if not self._profiles:
            raise ValueError("No models configured")
        self._active_id = config.get_active_model_id()
        self._providers: dict[str, BaseLLMProvider] = {}

    @property
    def profiles(self) -> List[ModelProfile]:
        return list(self._profiles)

    @property
    def active_id(self) -> str:
        return self._active_id

    def get_active_profile(self) -> ModelProfile:
        for profile in self._profiles:
            if profile.id == self._active_id:
                return profile
        return self._profiles[0]

    def get_active_provider(self) -> BaseLLMProvider:
        return self._get_or_create(self._active_id)

    def list_display(self) -> str:
        lines = []
        for i, profile in enumerate(self._profiles, 1):
            marker = " *" if profile.id == self._active_id else "  "
            lines.append(
                f"{marker}{i}. [{profile.id}] {profile.display_label()} "
                f"({profile.provider}/{profile.model})"
            )
        lines.append("")
        lines.append("Switch: /models <id>  or  /models <number>")
        return "\n".join(lines)

    def switch(self, selector: str) -> ModelProfile:
        selector = selector.strip()
        if not selector:
            raise ValueError("Model selector is empty")

        if selector.isdigit():
            index = int(selector) - 1
            if index < 0 or index >= len(self._profiles):
                raise ValueError(f"Model index out of range: {selector}")
            profile = self._profiles[index]
            self._active_id = profile.id
            return profile

        for profile in self._profiles:
            if profile.id == selector or profile.label == selector:
                self._active_id = profile.id
                return profile

        matches = [
            p for p in self._profiles
            if selector in p.id or selector in p.model or selector in p.label
        ]
        if len(matches) == 1:
            self._active_id = matches[0].id
            return matches[0]
        if len(matches) > 1:
            ids = ", ".join(p.id for p in matches)
            raise ValueError(f"Ambiguous model '{selector}', matches: {ids}")

        raise ValueError(f"Unknown model: {selector}")

    def apply_tools_to_active(self, tool_definitions: list) -> BaseLLMProvider:
        provider = self.get_active_provider()
        if hasattr(provider, "set_tool_definitions"):
            provider.set_tool_definitions(tool_definitions)
        return provider

    def _get_or_create(self, model_id: str) -> BaseLLMProvider:
        if model_id not in self._providers:
            profile = self._find_profile(model_id)
            self._providers[model_id] = create_provider_from_profile(
                profile, log_level=self._config.log_level
            )
        return self._providers[model_id]

    def _find_profile(self, model_id: str) -> ModelProfile:
        for profile in self._profiles:
            if profile.id == model_id:
                return profile
        raise KeyError(f"Model not found: {model_id}")
