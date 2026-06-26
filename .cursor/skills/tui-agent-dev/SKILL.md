---
name: tui-agent-dev
description: 开发与扩展 TUI Coding Agent 核心模块（Agent Loop、工具、权限、TUI、配置）。在修改 src/ 下 Agent 逻辑、添加工具、或对照 TOPIC.md 补齐功能时使用。
---

# TUI Agent 开发指引

## 启动

```bash
pip install -e ".[dev]"
python -m src.main --project-dir .
# 或
tui-agent --project-dir .
```

## 内置命令

`/help` `/clear` `/models` `/status` `/exit`

## 添加新工具

1. 在 `src/tools/` 实现，返回统一 `ToolResult`
2. 在 `registry.py` 注册 schema（name、description、parameters）
3. 在 `permissions.py` 声明只读或需确认
4. 在 `tests/test_tools.py` 补充测试

## Agent Loop 扩展

状态：`IDLE` → `THINKING` → `EXECUTING` / `AWAITING_PERMISSION` → `DONE` / `ERROR`

修改 `agent.py` 时保持 `step()` 可单步测试（见 `tests/test_agent.py`）。

## 配置优先级

显式参数 > `--config-dir` > `./.tui_agent/config.toml` > `~/.tui_agent/config.toml` > 默认值

## 参考实现

- 开源参考（只读）：`opencode/`、`codex/`、`DeepSeek-Reasonix/`
