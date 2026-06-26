---
name: snake-game-validation
description: 通过生成并运行贪吃蛇小游戏验证 Agent 端到端能力，支持 Python 与 C++ 实现。遇解析失败或不支持能力时自我反馈并完善 Agent。用于交付验证、回归测试或扩展能力时。
---

# 贪吃蛇自我反馈验证

## 目标

用 Agent 完成「从零生成可运行贪吃蛇」验证完整链路：理解任务 → 写代码 → 执行 → 根据结果修复。

## 验证流程

```
Task Progress:
- [ ] 1. 清空/指定输出目录（如 deliverables/snake_demo/）
- [ ] 2. 通过 Agent 或脚本发起任务：「用 Python 实现终端贪吃蛇，可键盘控制」
- [ ] 3. 运行生成代码，记录 stdout/stderr
- [ ] 4. 失败则分析：模型 response、工具调用解析、权限、运行时错误
- [ ] 5. 修复 Agent 能力（解析器/工具/提示词），重试
- [ ] 6. 可选：C++ 版本（g++ 编译运行）
- [ ] 7. 截图保存到 deliverables/
- [ ] 8. 追加 TASK.md 记录
```

## 自我反馈检查清单

| 现象 | 可能原因 | 修复方向 |
|------|----------|----------|
| 无 tool_calls | 模型未走工具格式 | 检查 tools schema、system prompt |
| JSON 解析失败 | 非标准 tool_call 格式 | 增强 `openai_provider` 解析容错 |
| 写入被拒绝 | 权限未确认 | TUI 权限流程或测试 mock |
| 代码无法运行 | 依赖缺失/语法错误 | Agent 应能 read + execute 自修复 |
| 流式中断 | 超时/重试不足 | 调整 timeout、max_retries |

## 建议任务提示词

**Python：**
> 在当前项目的 deliverables/snake_demo/ 目录下，用 Python 实现一个可在终端运行的贪吃蛇游戏，使用 curses 或类似库，支持方向键控制，显示分数，游戏结束可重新开始。

**C++：**
> 在 deliverables/snake_demo_cpp/ 用 C++ 实现控制台贪吃蛇，提供 Makefile 或编译说明，支持 WASD 控制。

## 成功标准

- 游戏可启动、蛇可移动、吃到食物增长、撞墙/自身游戏结束
- 关键运行截图在 `deliverables/`
- `pytest tests/ -v` 仍全部通过
