---
name: append-task-log
description: 向 TASK.md 追加会话与任务达成记录。在完成功能开发、测试验证、问题修复或用户要求记录时使用。
---

# 追加任务记录

## 步骤

1. 读取 `TASK.md` 末尾，确认上一条记录
2. 在文件末尾追加新条目（不覆盖历史）
3. 如有对应 checklist 项，更新 `.trae/specs/tui-coding-agent/checklist.md`

## 模板

```markdown
## [YYYY-MM-DD HH:MM] 标题

**目标**：
**状态**：进行中 | 已完成 | 阻塞

### 完成内容
-

### 遗留 / 下一步
-

---
```

## 示例

```markdown
## [2026-06-26 14:30] 实现 DebugInfo 模块

**目标**：LLM request/response 可观测，异常友好展示
**状态**：已完成

### 完成内容
- 新增 `src/debug_info.py`，统一格式化 request/response
- OpenAI Provider 接入 DebugInfo
- 单元测试通过

### 遗留 / 下一步
- 贪吃蛇 Demo 端到端验证

---
```
