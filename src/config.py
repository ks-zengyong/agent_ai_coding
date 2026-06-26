"""Configuration management for TUI Coding Agent."""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field


class ModelProfile(BaseModel):
    id: str
    label: str = ""
    provider: str = "openai"
    model: str = "mock-model"
    api_url: str = "http://localhost:8080"
    api_key: Optional[str] = None
    timeout_secs: int = 60
    max_retries: int = 3

    def display_label(self) -> str:
        return self.label or f"{self.provider}/{self.model}"


class ProviderConfig(BaseModel):
    name: str = "mock"
    model: str = "mock-model"
    api_url: str = "http://localhost:8080"
    api_key: Optional[str] = None
    timeout_secs: int = 60
    max_retries: int = 3
    max_loop: int = 100


class ToolConfig(BaseModel):
    name: str
    description: str = ""
    enabled: bool = True


class PermissionConfig(BaseModel):
    auto_exec_readonly: bool = True
    confirm_write: bool = True
    confirm_shell: bool = True


class Config(BaseModel):
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    models: List[ModelProfile] = Field(default_factory=list)
    active_model: str = ""
    tools: List[ToolConfig] = Field(default_factory=list)
    permission: PermissionConfig = Field(default_factory=PermissionConfig)
    history_dir: Path = Path(".ai_history/logs")
    log_level: str = "info"
    config_sources: List[str] = Field(default_factory=list)

    def get_model_profiles(self) -> List[ModelProfile]:
        if self.models:
            return self._apply_shared_api_key(self.models)
        profile = ModelProfile(
            id="default",
            label=f"{self.provider.name}:{self.provider.model}",
            provider=self.provider.name,
            model=self.provider.model,
            api_url=self.provider.api_url,
            api_key=self.provider.api_key,
            timeout_secs=self.provider.timeout_secs,
            max_retries=self.provider.max_retries,
        )
        return [profile]

    def get_active_model_id(self) -> str:
        profiles = self.get_model_profiles()
        if self.active_model:
            for profile in profiles:
                if profile.id == self.active_model:
                    return profile.id
        return profiles[0].id

    def _apply_shared_api_key(self, profiles: List[ModelProfile]) -> List[ModelProfile]:
        shared = self.provider.api_key
        result: List[ModelProfile] = []
        for profile in profiles:
            data = profile.model_dump()
            if not data.get("api_key") and shared:
                data["api_key"] = shared
            result.append(ModelProfile(**data))
        return result

    @classmethod
    def load(
        cls,
        project_dir: Path | str = ".",
        config_path: Optional[Path | str] = None,
        config_dir: Optional[Path | str] = None,
    ) -> "Config":
        project_dir = Path(project_dir).resolve()
        layers: List[Config] = [cls()]
        source_labels: List[str] = ["defaults"]

        user_path = _user_config_path()
        if user_path and user_path.exists():
            layers.append(cls._load_from_file(user_path))
            source_labels.append(str(user_path))

        project_path = project_dir / ".tui_agent" / "config.toml"
        if project_path.exists():
            layers.append(cls._load_from_file(project_path))
            source_labels.append(str(project_path))

        if config_dir:
            dir_config = Path(config_dir).resolve() / "config.toml"
            if dir_config.exists():
                layers.append(cls._load_from_file(dir_config))
                source_labels.append(str(dir_config))

        explicit_path = _resolve_explicit_config_path(config_path)
        if explicit_path and explicit_path.exists():
            layers.append(cls._load_from_file(explicit_path))
            source_labels.append(str(explicit_path))

        config = layers[0]
        for layer in layers[1:]:
            config = _merge_configs(config, layer)

        env_key = os.environ.get("TUI_AGENT_API_KEY")
        if env_key:
            config.provider.api_key = env_key
            source_labels.append("env:TUI_AGENT_API_KEY")

        if not config.history_dir.is_absolute():
            config.history_dir = project_dir / config.history_dir

        config.config_sources = source_labels
        return config

    @classmethod
    def _load_from_file(cls, path: Path) -> "Config":
        try:
            import tomllib
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except ImportError:
            import tomli
            data = tomli.loads(path.read_text(encoding="utf-8"))
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict) -> "Config":
        provider = data.get("provider")
        if isinstance(provider, dict) and "active_model" in provider:
            data = dict(data)
            data.setdefault("active_model", provider.pop("active_model"))
            if provider:
                data["provider"] = provider
            else:
                data.pop("provider", None)
        return cls(**data)


def _resolve_explicit_config_path(config_path: Optional[Path | str]) -> Optional[Path]:
    env_path = os.environ.get("TUI_AGENT_CONFIG")
    if config_path:
        return Path(config_path).resolve()
    if env_path:
        return Path(env_path).resolve()
    return None


def _user_config_path() -> Optional[Path]:
    home = _get_home_dir()
    if not home:
        return None
    return home / ".tui_agent" / "config.toml"


def _merge_configs(base: Config, override: Config) -> Config:
    merged_provider = ProviderConfig(
        name=_pick(override.provider.name, base.provider.name, sentinel="mock"),
        model=_pick(override.provider.model, base.provider.model, sentinel="mock-model"),
        api_url=_pick(override.provider.api_url, base.provider.api_url, sentinel="http://localhost:8080"),
        api_key=override.provider.api_key or base.provider.api_key,
        timeout_secs=override.provider.timeout_secs,
        max_retries=override.provider.max_retries,
        max_loop=override.provider.max_loop,
    )

    merged_models = override.models if override.models else base.models
    merged_active = override.active_model or base.active_model
    merged_tools = override.tools if override.tools else base.tools

    merged_permission = PermissionConfig(
        auto_exec_readonly=override.permission.auto_exec_readonly,
        confirm_write=override.permission.confirm_write,
        confirm_shell=override.permission.confirm_shell,
    )

    history = (
        override.history_dir
        if str(override.history_dir) != ".ai_history/logs"
        else base.history_dir
    )

    return Config(
        provider=merged_provider,
        models=merged_models,
        active_model=merged_active,
        tools=merged_tools,
        permission=merged_permission,
        history_dir=history,
        log_level=_pick(override.log_level, base.log_level, sentinel="info"),
    )


def _pick(override_val: str, base_val: str, sentinel: str) -> str:
    return override_val if override_val != sentinel else base_val


def _get_home_dir() -> Optional[Path]:
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    return Path(home) if home else None


def mask_api_key(key: Optional[str]) -> str:
    if not key:
        return "(not set)"
    if len(key) <= 8:
        return "***"
    return key[:4] + "***" + key[-4:]
