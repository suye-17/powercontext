# 复现步骤

在 `minimax/` 目录下执行。默认使用嵌入式 seekdb 和本地 SQLite PowerContext。

## 0. 环境

- Python 3.11+
- uv
- Linux 或 macOS（嵌入式 `pylibseekdb` 的平台限制）
- PowerContext 1.4.0（项目 `server` extra 会安装）
- 可选：MiniMax Token Plan/API key，仅在需要重跑第 9 步时使用

## 1. 安装依赖

```bash
uv sync --extra dev --extra server
```

## 2. 启动 PowerContext（端口 8010）

```bash
mkdir -p .runtime
sed "s#/absolute/path/to/minimax#$PWD#" powercontext.env.example > .runtime/powercontext.env
PYTHONDONTWRITEBYTECODE=1 .venv/bin/powercontext server run --env-file .runtime/powercontext.env
```

另开终端确认：

```bash
curl -fsS http://127.0.0.1:8010/health/live
```

## 3. 导入 before 工程到 seekdb

```bash
uv run --extra dev sdd-context ingest --root snapshots/before/tasklite
```

预期：`documents=6`，`chunks=18`，其中 `spec=4`、`code=6`、`test=8`。

## 4. 初始上下文（不含状态）

```bash
TASK='实现 SPEC-003 的 min_priority 过滤和 SPEC-004 的 priority 校验，涉及 list_tasks 与 add_task，目标测试是 test_list_tasks_filters_by_min_priority 和 test_add_task_rejects_invalid_priority'
uv run --extra dev sdd-context build --task "$TASK" --top-k 3 --skip-state
```

预期：输出包含 `SPEC-003`、`SPEC-004`、`tasklite/service.py#list_tasks`、`tasklite/service.py#add_task`，以及两个目标测试。

## 5. before 目标测试（预期失败）

```bash
cd snapshots/before/tasklite
../../../.venv/bin/pytest -q
cd ../../..
```

预期：`6 failed, 7 passed`，退出码 1。失败原因是目标行为缺失（断言失败 / `DID NOT RAISE`），不是环境错误。

## 6. 写入 PowerContext 状态

```bash
uv run --extra dev sdd-context state write --file evidence/powercontext/state-before-edit.json
uv run --extra dev sdd-context state read > evidence/powercontext/read-before-restart.json
```

## 7. 构建带状态的上下文

```bash
uv run --extra dev sdd-context build --task "$TASK" --top-k 3 --output evidence/context/context-v1.json
```

预期：`state.task_id` 为 `tasklite-min-priority`，`specs` 含 `SPEC-003`/`SPEC-004`，`code` 含 `list_tasks`/`add_task`，`tests` 含两个目标测试。

## 8. 重启 PowerContext 并验证状态复用

停止 PowerContext server，然后用同一 `.runtime/powercontext.env` 重启：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/powercontext server run --env-file .runtime/powercontext.env
uv run --extra dev sdd-context state read > evidence/powercontext/read-after-restart.json
diff -u evidence/powercontext/read-before-restart.json evidence/powercontext/read-after-restart.json
uv run --extra dev sdd-context build --task "$TASK" --top-k 3 --output evidence/context/context-after-restart.json
```

预期：`diff` 无输出（状态一致）；`context-after-restart.json` 的 `state.task_id` 为 `tasklite-min-priority`。

## 9. MiniMax 生成并应用补丁（可选重跑）

已记录的调用证据在 `evidence/minimax/`，复现时可跳过此步。若要重新调用：

```bash
cp .env.example .env
# 编辑 .env，填入 MINIMAX_BASE_URL、MINIMAX_API_KEY、MINIMAX_MODEL
SDD_ENV_FILE="$PWD/.env" uv run --extra dev python scripts/minimax_edit.py \
  --context evidence/context/context-after-restart.json \
  --before snapshots/before/tasklite \
  --after snapshots/after/tasklite \
  --out evidence/minimax \
  --env-file "$PWD/.env"
```

脚本会保存 `request.json`、`response.json`、`output.txt`、`picked.patch` 和 `apply.log`。原始模型输出在 `output.txt`，最终可应用补丁在 `picked.patch`；`evidence/minimax/README.md` 记录了模型输出与最终 diff 的对应关系。

## 10. after 与回归测试（预期全部通过）

```bash
cd snapshots/after/tasklite
../../../.venv/bin/pytest -q
../../../.venv/bin/pytest -q -k "test_list_tasks_excludes_archived_by_default or test_list_tasks_include_archived or test_list_tasks_sorts_by_priority_desc_then_id or test_add_task_appends_valid_task"
cd ../../..
```

预期：第一组 `13 passed`，第二组 `4 passed`。也可以直接验证最终业务工程：

```bash
cd demo/tasklite
../../.venv/bin/pytest -q
cd ../..
```

## 11. 规格更新后重新导入

```bash
rm -rf /tmp/tasklite-v2 .runtime/seekdb-v2
cp -R snapshots/after/tasklite /tmp/tasklite-v2
cp sample/spec-v2.md /tmp/tasklite-v2/specs/tasklite.md
SDD_DB_PATH=.runtime/seekdb-v2 uv run --extra dev sdd-context ingest --root /tmp/tasklite-v2
SDD_DB_PATH=.runtime/seekdb-v2 uv run --extra dev sdd-context build --task "$TASK" --top-k 3 --skip-state --output evidence/context/context-v2.json
cat evidence/context/spec-update-comparison.txt
```

预期：`SPEC-003` 的文本从“只返回 priority >= min_priority 的任务”变为“只返回 priority 不低于 min_priority 的任务”；`changed: True`。默认数据库仍是 v1 语料，v2 使用独立的 `.runtime/seekdb-v2`。

## 12. 重置

先停止 PowerContext server，再执行：

```bash
./sample/reset.sh
```
