#!/usr/bin/env python3
"""贪吃蛇端到端自我反馈验证。

用法:
    python scripts/run_snake_validation.py              # 完整验证（Agent + 反馈循环）
    python scripts/run_snake_validation.py --dry-run    # 仅打印清单
    python scripts/run_snake_validation.py --local-only # 跳过 LLM，仅验证本地 snake.py
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent import CodingAgent
from src.agent_runner import run_agent_until_done, save_session
from src.config import Config
from src.feedback import (
    capture_run_output,
    suggest_agent_fixes,
    validate_snake_python,
)
from src.main import register_tools
from src.llm.model_registry import ModelRegistry
from src.permissions import PermissionGuard
from src.tools import ToolRegistry

DELIVERABLES = ROOT / "deliverables"

PROMPTS = {
    "python": (
        "在 deliverables/snake_demo/ 目录创建 snake.py："
        "实现控制台贪吃蛇游戏，支持 --self-test 自动化测试模式（不依赖用户输入），"
        "自测模式下模拟若干步移动、吃到食物、打印棋盘并返回 exit 0。"
        "完成后运行 python deliverables/snake_demo/snake.py --self-test 验证。"
    ),
    "cpp": (
        "在 deliverables/snake_demo_cpp/ 用 C++ 实现控制台贪吃蛇，"
        "提供 Makefile，支持 make test 自动化测试。"
    ),
}

MAX_ATTEMPTS = 3


def _bootstrap_snake(snake_path: Path) -> None:
    """Ensure reference snake exists for local validation fallback."""
    ref = ROOT / "deliverables" / "snake_demo" / "snake.py"
    if ref.exists() and not snake_path.exists():
        snake_path.parent.mkdir(parents=True, exist_ok=True)
        snake_path.write_text(ref.read_text(encoding="utf-8"), encoding="utf-8")


async def run_agent_attempt(
    config: Config,
    prompt: str,
    attempt: int,
) -> CodingAgent:
    os.chdir(ROOT)
    model_registry = ModelRegistry(config)
    tool_registry = ToolRegistry()
    register_tools(tool_registry)
    provider = model_registry.apply_tools_to_active(tool_registry.get_tool_definitions())

    permission_guard = PermissionGuard()
    agent = CodingAgent(
        provider=provider,
        tool_registry=tool_registry,
        permission_guard=permission_guard,
        max_loop=min(config.provider.max_loop, 30),
    )

    print(f"\n=== Agent Attempt {attempt} ===")
    state = await run_agent_until_done(agent, prompt, auto_approve=True, max_steps=60)
    save_session(agent, config.history_dir, prefix=f"snake_attempt_{attempt}")
    print(f"Agent finished with state: {state.value}")
    return agent


async def run_validation(lang: str, local_only: bool) -> int:
    snake_dir = DELIVERABLES / ("snake_demo" if lang == "python" else "snake_demo_cpp")
    snake_path = snake_dir / ("snake.py" if lang == "python" else "snake.cpp")
    snake_dir.mkdir(parents=True, exist_ok=True)

    if lang != "python":
        print("C++ validation: scaffold only — Python path is primary.")
        return 0

    config = Config.load(project_dir=ROOT)
    if not config.provider.api_key and (ROOT / ".tui_agent" / "config.toml").exists():
        cfg2 = Config.load(project_dir=ROOT)
        config = cfg2

    prompt = PROMPTS[lang]
    last_result = None

    if not local_only and config.provider.name.lower() != "mock":
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                await run_agent_attempt(config, prompt, attempt)
            except Exception as e:
                print(f"Agent error on attempt {attempt}: {e}")
                prompt = f"Previous run failed with error: {e}\n{prompt}"

            last_result = validate_snake_python(snake_path)
            if last_result.success:
                break

            fix_prompt = suggest_agent_fixes(last_result)
            print(f"\nValidation failed:\n{last_result.summary()}")
            prompt = fix_prompt
    else:
        print("[local-only] Skipping LLM agent run.")
        _bootstrap_snake(snake_path)
        last_result = validate_snake_python(snake_path)

    if last_result is None:
        last_result = validate_snake_python(snake_path)

    out_file = DELIVERABLES / "snake_demo_run.txt"
    if snake_path.exists():
        capture_run_output(snake_path, out_file)
        print(f"Run output saved to {out_file}")

    if last_result.success:
        print("\n✓ Snake validation PASSED")
        return 0

    print(f"\n✗ Snake validation FAILED:\n{last_result.summary()}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="贪吃蛇自我反馈验证")
    parser.add_argument("--lang", choices=("python", "cpp"), default="python")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--local-only", action="store_true", help="Skip LLM, validate local snake.py")
    args = parser.parse_args()

    if args.dry_run:
        print("=== 贪吃蛇验证清单 ===")
        for i, item in enumerate([
            "Agent 理解任务并规划步骤",
            "创建/写入源文件",
            "execute_command 运行 --self-test",
            "失败时自我反馈并重试",
            "输出保存至 deliverables/",
        ], 1):
            print(f"  {i}. {item}")
        print(f"\nPrompt:\n{PROMPTS[args.lang]}")
        return 0

    return asyncio.run(run_validation(args.lang, args.local_only))


if __name__ == "__main__":
    sys.exit(main())
