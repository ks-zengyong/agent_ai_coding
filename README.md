# TUI Coding Agent

一个运行在终端中的图形化交互编码Agent，支持自然语言下达开发任务，结合代码仓库上下文理解任务、规划步骤、调用工具并持续推理。

## 功能特性

- **TUI界面**：基于 Rich 的 Claude 风格终端交互（流式输出、Thinking 指示、工具卡片、Markdown 渲染）
- **Agent Loop**：完整的模型决策→工具调用→结果回传→继续推理循环
- **仓库操作**：文件读写、目录浏览、代码搜索、Shell命令执行等
- **权限控制**：只读操作自动执行，写入/Shell操作需用户确认
- **会话管理**：完整的会话历史，支持持久化和上下文压缩
- **配置管理**：用户级+项目级配置，项目级优先
- **内置命令**：/help、/clear、/models、/status、/debug、/expand、/exit

## 项目结构

```
coding_agent_tui/
├── deliverables/              # 验证产物截图
├── .ai_history/logs/          # 会话记录
├── src/
│   ├── main.py                # 入口
│   ├── config.py              # 配置管理
│   ├── llm/
│   │   ├── base.py            # LLM Provider抽象
│   │   ├── openai_provider.py # OpenAI兼容实现
│   │   └── mock_provider.py   # Mock用于测试
│   ├── tools/
│   │   ├── registry.py        # 工具注册与执行
│   │   ├── filesystem.py      # 文件/目录操作
│   │   └── shell.py           # Shell命令执行
│   ├── agent.py               # Agent主循环
│   ├── context.py             # 会话管理 & 上下文压缩
│   ├── permissions.py         # 权限控制
│   ├── tui.py                 # Rich TUI 界面
│   └── tui_format.py          # TUI 格式化辅助
├── tests/
│   ├── test_agent.py
│   ├── test_tools.py
│   └── test_context.py
└── pyproject.toml
```

## 安装

**推荐（免 pip 安装运行时依赖）**：仓库已内置 `src/_vendor/`，克隆后直接运行：

```bash
python -m src.main --project-dir .
```

`src/__init__.py` 启动时自动将 vendor 置于 `sys.path` 最前，优先于系统 site-packages，避免版本差异。

若 `src/_vendor/` 为空（仅 `.gitkeep`），在项目根执行一次：

```bash
python scripts/vendor_deps.py
```

会将 `httpx`、`rich`、`pygments`、`urllib3`、`prompt_toolkit` 及传递依赖下载到 `src/_vendor/`。锁定版本见 `src/_vendor/VENDOR.lock`。

**开发/测试**（可选）：

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

### Vendor 维护

```bash
python scripts/vendor_deps.py --list      # 查看将下载的包
python scripts/vendor_deps.py --dry-run   # 仅打印 pip 命令
```

## 使用

```bash
python -m src.main --project-dir .
```

或

```bash
tui-agent --project-dir .
```

### 内置命令

- `/help` - 显示帮助信息
- `/clear` - 清空当前会话
- `/models` - 查看或切换模型
- `/status` - 查看运行状态
- `/debug` - 切换 LLM 请求/响应调试面板
- `/expand` - 展开折叠的工具结果或 Thinking 块
- `/exit` - 退出程序

### TUI 交互

- **流式输出**：模型回复逐 token 显示
- **Thinking**：等待期 Spinner；若 API 返回 reasoning 则显示折叠 Thinking 块
- **工具卡片**：`⏺ tool_name(args)` 即时展示，执行时 Spinner + 耗时
- **Markdown**：含代码块/标题的回复自动格式化渲染

### 配置

支持多模型配置（`[[models]]`），运行时通过 `/models` 切换。优先级从低到高：

1. 内置默认值
2. `~/.tui_agent/config.toml`
3. `./.tui_agent/config.toml`（相对于 `--project-dir`）
4. `--config-dir /path/to/dir`（读取其中 `config.toml`）
5. `--config /path/to/file.toml` 或环境变量 `TUI_AGENT_CONFIG`

合并后，环境变量 `TUI_AGENT_API_KEY` 可覆盖各模型未单独配置的 `api_key`。

```toml
active_model = "deepseek-openai"

[[models]]
id = "deepseek-openai"
label = "DeepSeek V4 (OpenAI)"
provider = "openai"
model = "deepseek/deepseek-v4-pro"
api_url = "https://ai-kas.kso.net/codeplan/v1"

[[models]]
id = "deepseek-anthropic"
label = "DeepSeek V4 (Anthropic)"
provider = "anthropic"
model = "deepseek/deepseek-v4-pro"
api_url = "https://ai-kas.kso.net/codeplan/anthropic"
```

```bash
# TUI 内切换模型
/models              # 列出所有模型
/models 2            # 按序号切换
/models deepseek-anthropic
```

可选 TUI 配置：

```toml
[tui]
stream = true
show_thinking = "collapsed"   # collapsed | expanded | hidden
render_markdown = true
tool_result_preview = 500
```

## 测试

```bash
pytest tests/ -v
python scripts/run_snake_validation.py --local-only   # 本地验证贪吃蛇
python scripts/run_snake_validation.py              # LLM + 自我反馈验证
```

## 支持的工具

| 工具 | 描述 | 权限 |
|------|------|------|
| read_file | 读取文件内容 | 只读 |
| write_file | 写入文件内容 | 写入 |
| edit_file | 编辑文件（字符串替换） | 写入 |
| list_directory | 列出目录内容 | 只读 |
| glob | 文件模式匹配 | 只读 |
| search_code | 代码内容搜索 | 只读 |
| execute_command | 执行Shell命令 | Shell |