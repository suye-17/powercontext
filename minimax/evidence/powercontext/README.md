# PowerContext 状态记录

## 环境

- PowerContext 1.4.0
- 地址：`http://127.0.0.1:8010`
- 数据库：SQLite，`.runtime/powercontext.db`
- Access mode：`disabled`
- 未配置生成/embedding 模型；只使用显式 Memory 写入和 FTS 检索

## 写入

- Scope：`scp_294k05kdwdk8arbryah0rb4g99`（`minimax-sdd`）
- 请求：`POST /v1/memory/remember`
- `kind`：`sdd-task-state`
- 内容大小：445 bytes
- 返回 Artifact：`art_5ccfa4002e094062b03453eace875543`，revision 1
- 记录：`write.json`

写入的状态包含：

- `task_id`：`tasklite-min-priority`
- `objective`：实现 `SPEC-003` 与 `SPEC-004`
- `completed`：规格/代码/测试导入、before 快照、上下文构建
- `todo`：MiniMax 补丁、after 测试、回归测试
- `verification`：before 测试 `6 failed, 7 passed`

## 重启读取

1. 写入后读取：`read-before-restart.json`
2. 停止 PowerContext Server，再用同一 SQLite 文件重启
3. 再次读取：`read-after-restart.json`
4. 对比：`restart-compare.txt`，结果为 `IDENTICAL`
5. 重启后的上下文：`evidence/context/context-after-restart.json`，其中 `state.task_id` 为 `tasklite-min-priority`

这说明状态从空开始写入，重启后仍能读取，并进入后续上下文输出。
