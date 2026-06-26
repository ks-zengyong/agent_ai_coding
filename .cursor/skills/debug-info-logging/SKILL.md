---
name: debug-info-logging
description: 实现或改进 Agent 的 DebugInfo 输出，包含 LLM request/response 与优雅异常展示。在调试 Provider、排查工具调用解析、或用户要求可观测性时使用。
---

# DebugInfo 日志规范

## 模块位置

建议统一在 `src/debug_info.py`（若不存在则创建），供 `llm/`、`agent.py`、`tools/` 调用。

## Request 日志

输出字段（API Key 脱敏为 `***`）：

- `provider`、`model`、`api_url`
- `messages`：条数 + 每条 role 与 content 前 200 字符
- `tools`：工具名列表
- `timeout_secs`、`max_retries`

## Response 日志

- `content` 摘要（过长截断）
- `tool_calls`：name + arguments 摘要
- `finish_reason`
- `usage`（若 API 返回）

## 异常展示

使用 Rich `Panel` 或等价格式化：

```
╭─ LLM Error ─────────────────╮
│ Type: HTTPStatusError       │
│ Status: 429                 │
│ Message: Rate limit exceeded│
│ Suggestion: 等待后重试或降低 max_loop │
╰─────────────────────────────╯
```

避免：
- 裸 `print(exc)` 无上下文
- 日志中完整 API Key
- 无限长度 message dump

## 集成点

1. `OpenAIProvider._chat_impl`：请求前 log_request，响应后 log_response
2. `OpenAIProvider._with_retry`：每次重试与最终失败 log_error
3. TUI 可选 Debug 模式开关（配置 `log_level = "debug"`）

## 验证

手动或测试触发一次 chat，确认 stderr/日志区可见 request/response 摘要。
