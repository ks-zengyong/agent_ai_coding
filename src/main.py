from __future__ import annotations

import argparse
from pathlib import Path

from src.config import Config
from src.tools import (
    ToolRegistry,
    ReadFileTool,
    WriteFileTool,
    EditFileTool,
    ListDirectoryTool,
    GlobTool,
    SearchCodeTool,
)
from src.tools.shell import ExecuteCommandTool, DetectBuildToolchainTool
from src.agent import CodingAgent
from src.permissions import PermissionGuard
from src.llm.model_registry import ModelRegistry
from src.llm.prefix_cache import PrefixCacheManager
from src.agent_runner import build_system_prompt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TUI Coding Agent")
    parser.add_argument(
        "--project-dir",
        type=str,
        default=".",
        help="Project directory (default: current directory)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Explicit config file (highest priority). Also via TUI_AGENT_CONFIG env.",
    )
    parser.add_argument(
        "--config-dir",
        type=str,
        default=None,
        help="Directory containing config.toml (priority above project/user configs)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )
    return parser.parse_args()


def register_tools(tool_registry: ToolRegistry) -> None:
    tool_registry.register(ReadFileTool())
    tool_registry.register(WriteFileTool())
    tool_registry.register(EditFileTool())
    tool_registry.register(ListDirectoryTool())
    tool_registry.register(GlobTool())
    tool_registry.register(SearchCodeTool())
    tool_registry.register(DetectBuildToolchainTool())
    tool_registry.register(ExecuteCommandTool())


def main() -> None:
    from src import ensure_vendor_deps

    ensure_vendor_deps()
    args = parse_args()

    project_dir = Path(args.project_dir).resolve()
    config_path = Path(args.config) if args.config else None
    config_dir = Path(args.config_dir) if args.config_dir else None

    config = Config.load(
        project_dir=project_dir,
        config_path=config_path,
        config_dir=config_dir,
    )

    model_registry = ModelRegistry(config)
    tool_registry = ToolRegistry()
    register_tools(tool_registry)
    tool_definitions = tool_registry.get_tool_definitions()
    provider = model_registry.apply_tools_to_active(tool_definitions)

    # 启动 LLM 请求日志 session：生成唯一 ID，后续每次请求自动写入
    from src import debug_info
    debug_info.start_session(config.history_dir)
    debug_info.configure(enabled=True, log_level=config.log_level)

    if args.verbose:
        from src.config import mask_api_key
        debug_info.configure(enabled=True, log_level="debug")
        print("Config sources (low → high priority):")
        for src in config.config_sources:
            print(f"  - {src}")
        print("\nConfigured models:")
        print(model_registry.list_display())
        active = model_registry.get_active_profile()
        print(f"\nActive: {active.id} ({active.provider}/{active.model})")
        print(f"API URL: {active.api_url}")
        print(f"API key: {mask_api_key(active.api_key)}")

    permission_guard = PermissionGuard(config.permission)

    active_profile = model_registry.get_active_profile()
    system_prompt = build_system_prompt(active_profile.display_label())

    prefix_cache = None
    if config.cache.enabled:
        prefix_cache = PrefixCacheManager(
            system_prompt=system_prompt,
            tools=tool_definitions,
        )

    agent = CodingAgent(
        provider=provider,
        tool_registry=tool_registry,
        permission_guard=permission_guard,
        max_loop=config.provider.max_loop,
        prefix_cache=prefix_cache,
        cache_log_interval=config.cache.log_interval,
    )

    from src.agent_runner import save_session
    from src.tui import run_tui
    import asyncio

    try:
        asyncio.run(run_tui(agent, permission_guard, config, model_registry, tool_registry))
    finally:
        if agent.session.messages:
            path = save_session(agent, config.history_dir, prefix="tui")
            if args.verbose:
                print(f"Session saved to {path}")


if __name__ == "__main__":
    main()
