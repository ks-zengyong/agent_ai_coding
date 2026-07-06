# TUI Coding Agent — AI 协作指引

## 必读

| 文件 | 用途 |
|------|------|
| `TOPIC.md` | 题目与功能要求 |
| `INIT.md` | 初始化说明、API 配置、扩展要求 |
| `TASK.md` | 历史任务记录（每次工作后追加） |
| `.trae/specs/tui-coding-agent/` | 规范、checklist、tasks |

## 项目技能（`.cursor/skills/`）

- **append-task-log** — 向 TASK.md 追加记录
- **snake-game-validation** — 贪吃蛇端到端自我反馈验证
- **debug-info-logging** — Request/Response 与异常展示
- **tui-agent-dev** — 开发 Agent 核心模块

## 快速命令

```bash
pip install -e ".[dev]"
pytest tests/ -v
python -m src.main --project-dir .
```

### Windows 一键启动

双击根目录 `start.bat` 即可运行（自动探测 Python、按需下载 vendor 依赖、启动 TUI）。

## 约束

- 自主实现，不以 `opencode/`、`codex/`、`DeepSeek-Reasonix/` 为提交基底
- 只读工具自动执行；写入/Shell 需用户确认
- API Key 不得暴露在日志或 UI 中
- 会话持久化至 `.ai_history/logs/`

## 参考源码（只读）

- `opencode/` — TUI 与工具设计参考
- `codex/` — CLI Agent 架构参考
- `DeepSeek-Reasonix/` — Agent 实现参考
