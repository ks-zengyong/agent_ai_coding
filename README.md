# TUI Coding Agent

一个运行在终端中的图形化交互编码Agent，支持自然语言下达开发任务，结合代码仓库上下文理解任务、规划步骤、调用工具并持续推理。

## 功能特性

- **TUI界面**：基于Textual框架的终端图形界面，展示对话、工具调用日志、运行状态
- **Agent Loop**：完整的模型决策→工具调用→结果回传→继续推理循环
- **仓库操作**：文件读写、目录浏览、代码搜索、Shell命令执行等
- **权限控制**：只读操作自动执行，写入/Shell操作需用户确认
- **会话管理**：完整的会话历史，支持持久化和上下文压缩
- **配置管理**：用户级+项目级配置，项目级优先
- **内置命令**：/help、/clear、/models、/status、/exit

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
│   └── tui.py                 # Textual界面
├── tests/
│   ├── test_agent.py
│   ├── test_tools.py
│   └── test_context.py
└── pyproject.toml
```

## 安装

```bash
pip install -e .
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
- `/models` - 查看当前模型
- `/status` - 查看运行状态
- `/exit` - 退出程序

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