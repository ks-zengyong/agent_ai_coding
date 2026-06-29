"""Configuration management for TUI Coding Agent.

使用标准库 ``dataclasses`` 实现（原 pydantic 已移除），以便项目可零依赖运行。
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class ModelProfile:
    id: str
    label: str = ""
    provider: str = "openai"
    model: str = "mock-model"
    api_url: str = "http://localhost:8080"
    api_key: Optional[str] = None
    timeout_secs: int = 60
    max_retries: int = 3
    max_tokens: int = 8192
    temperature: float = 0.0

    def display_label(self) -> str:
        return self.label or f"{self.provider}/{self.model}"


@dataclass
class ProviderConfig:
    name: str = "mock"
    model: str = "mock-model"
    api_url: str = "http://localhost:8080"
    api_key: Optional[str] = None
    timeout_secs: int = 60
    max_retries: int = 3
    max_loop: int = 100
    max_tokens: int = 8192
    temperature: float = 0.0


@dataclass
class ToolConfig:
    name: str
    description: str = ""
    enabled: bool = True


@dataclass
class PermissionConfig:
    auto_exec_readonly: bool = True
    confirm_write: bool = True
    confirm_shell: bool = True


@dataclass
class TuiConfig:
    stream: bool = True
    show_thinking: str = "collapsed"  # collapsed | expanded | hidden
    render_markdown: bool = True
    tool_result_preview: int = 500


@dataclass
class Config:
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    models: List[ModelProfile] = field(default_factory=list)
    active_model: str = ""
    tools: List[ToolConfig] = field(default_factory=list)
    permission: PermissionConfig = field(default_factory=PermissionConfig)
    tui: TuiConfig = field(default_factory=TuiConfig)
    history_dir: Path = field(default_factory=lambda: Path(".ai_history/logs"))
    log_level: str = "info"
    config_sources: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # toml 中 history_dir 是字符串，统一转为 Path（原 pydantic 自动转换的等价行为）
        if not isinstance(self.history_dir, Path):
            self.history_dir = Path(self.history_dir)

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
            max_tokens=self.provider.max_tokens,
            temperature=self.provider.temperature,
        )
        return [profile]

    def get_active_model_id(self) -> str:
        profiles = self.get_model_profiles()
        if self.active_model:
            for profile in profiles:
                if profile.id == self.active_model:
                    return profile.id
        return profiles[0].id

    def apply_env_api_key(self, env_key: str) -> None:
        """用环境变量 key 填充未独立配置 api_key 的模型。

        - 已在 ``[[models]]`` 中写明 ``api_key`` 的模型：保持不变（独立 key 优先）
        - 未配置 ``api_key`` 的模型：用 env_key 填充
        - 同时写入 ``provider.api_key``，使单 provider 回退路径也能用上 env key
        """
        self.provider.api_key = env_key
        for profile in self.models:
            if not profile.api_key:
                profile.api_key = env_key

    def _apply_shared_api_key(self, profiles: List[ModelProfile]) -> List[ModelProfile]:
        """用 ``provider.api_key`` 兜底未独立配置 api_key 的模型。

        已独立配置 ``api_key`` 的模型保持原值（独立 key 优先于共享池）。
        """
        shared = self.provider.api_key
        result: List[ModelProfile] = []
        for profile in profiles:
            data = asdict(profile)
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
            # env key 仅作为兜底，填充未独立配置 api_key 的模型；
            # 已在 [[models]] 中写明 api_key 的模型不被覆盖（独立 key 优先）。
            config.apply_env_api_key(env_key)
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
        data = dict(data)
        provider = data.get("provider")
        if isinstance(provider, dict) and "active_model" in provider:
            data.setdefault("active_model", provider.pop("active_model"))

        # dataclass 不会自动把嵌套 dict 转成 dataclass 实例（pydantic 会），需手动转换
        if isinstance(data.get("provider"), dict):
            data["provider"] = _build_dataclass(ProviderConfig, data["provider"])
        if isinstance(data.get("permission"), dict):
            data["permission"] = _build_dataclass(PermissionConfig, data["permission"])
        if isinstance(data.get("tui"), dict):
            data["tui"] = _build_dataclass(TuiConfig, data["tui"])
        if isinstance(data.get("models"), list):
            data["models"] = [
                _build_dataclass(ModelProfile, m) if isinstance(m, dict) else m
                for m in data["models"]
            ]
        if isinstance(data.get("tools"), list):
            data["tools"] = [
                _build_dataclass(ToolConfig, t) if isinstance(t, dict) else t
                for t in data["tools"]
            ]

        # 仅保留 Config 已知字段，避免 toml 中出现额外键时 dataclass 构造报错
        filtered = _build_dataclass(cls, data)
        return filtered


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


def _build_dataclass(dc_cls, data: dict):
    """仅用 dataclass 已知字段构造，忽略 toml 中的额外键（等价 pydantic extra='ignore'）。"""
    known = set(dc_cls.__dataclass_fields__)
    filtered = {k: v for k, v in data.items() if k in known}
    return dc_cls(**filtered)


def _merge_models(
    base: List[ModelProfile], override: List[ModelProfile]
) -> List[ModelProfile]:
    """按 ``id`` 合并两级配置的模型列表。

    - override 中同 id 的模型：以 override 为准，但若 override 该模型某字段为空而 base 非空，
      则取 base 值（让 user 级独立 api_key 能被 project 级部分覆盖所保留）。
    - base 独有 id：保留（user 级独立配置的模型在 project 级未提及时仍可用）。
    - override 独有 id：追加（project 级新增模型）。
    """
    if not override:
        return list(base)
    if not base:
        return list(override)

    merged: List[ModelProfile] = []
    base_by_id = {p.id: p for p in base}
    override_by_id = {p.id: p for p in override}

    # 先按 override 顺序，保持 project 级定义的模型顺序
    for ov in override:
        base_p = base_by_id.get(ov.id)
        if base_p is None:
            merged.append(ov)
            continue
        merged.append(_merge_one_model(base_p, ov))

    # 再追加 base 独有的模型（user 级配置但 project 级未提及）
    for b in base:
        if b.id not in override_by_id:
            merged.append(b)

    return merged


def _merge_one_model(base: ModelProfile, override: ModelProfile) -> ModelProfile:
    """合并同 id 模型：override 整体替换 base，仅 ``api_key`` 做 fallback。

    设计取舍：dataclass 无法可靠区分"显式设为默认值"与"未设置"，因此除
    ``api_key`` 外的字段不做字段级合并，override 同 id 模型即整体替换。
    ``api_key`` 特殊处理是为了让 user 级配置的独立 key 在 project 级同 id
    模型未写 ``api_key`` 时仍能保留——这是"per-model 独立 key 跨级生效"的核心。
    """
    return ModelProfile(
        id=override.id,
        label=override.label,
        provider=override.provider,
        model=override.model,
        api_url=override.api_url,
        api_key=override.api_key or base.api_key,
        timeout_secs=override.timeout_secs,
        max_retries=override.max_retries,
        max_tokens=override.max_tokens,
        temperature=override.temperature,
    )


def _merge_configs(base: Config, override: Config) -> Config:
    merged_provider = ProviderConfig(
        name=_pick(override.provider.name, base.provider.name, sentinel="mock"),
        model=_pick(override.provider.model, base.provider.model, sentinel="mock-model"),
        api_url=_pick(override.provider.api_url, base.provider.api_url, sentinel="http://localhost:8080"),
        api_key=override.provider.api_key or base.provider.api_key,
        timeout_secs=override.provider.timeout_secs,
        max_retries=override.provider.max_retries,
        max_loop=override.provider.max_loop,
        max_tokens=override.provider.max_tokens,
        temperature=override.provider.temperature,
    )

    merged_models = _merge_models(base.models, override.models)
    merged_active = override.active_model or base.active_model
    merged_tools = override.tools if override.tools else base.tools

    merged_permission = PermissionConfig(
        auto_exec_readonly=override.permission.auto_exec_readonly,
        confirm_write=override.permission.confirm_write,
        confirm_shell=override.permission.confirm_shell,
    )

    merged_tui = TuiConfig(
        stream=override.tui.stream,
        show_thinking=override.tui.show_thinking,
        render_markdown=override.tui.render_markdown,
        tool_result_preview=override.tui.tool_result_preview,
    )

    # 用 Path 相等比较，避免 Windows 下 str(Path) 含反斜杠导致误判
    history = (
        override.history_dir
        if override.history_dir != Path(".ai_history/logs")
        else base.history_dir
    )

    return Config(
        provider=merged_provider,
        models=merged_models,
        active_model=merged_active,
        tools=merged_tools,
        permission=merged_permission,
        tui=merged_tui,
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
