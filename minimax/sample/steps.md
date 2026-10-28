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

> ⚠️ **务必等待 `/health/live` 返回 OK 再执行后续带状态的命令**（步骤 6、7、8），否则会报 `PowerContext server unreachable`。`server run` 是前台进程，请另开终端执行后续步骤；启动时打印的「未配置推理模型」告警是正常现象，本任务只用显式 Memory + FTS，不依赖生成/embedding 模型，可忽略。

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

> ⚠️ **退出码 1 是预期结果**：这 6 个失败正是第 6 项「代码修改有效」所需的修改前失败证据，请勿据此判为不通过。失败项是 `test_list_tasks_filters_by_min_priority` 与 `test_add_task_rejects_invalid_priority` 的 5 个参数化用例（断言失败 / `DID NOT RAISE`），不是缺依赖或无法启动。

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

> ⚠️ **必须先停止旧的 server 再用同一 env 文件重启**（Ctrl+C 停止，不要直接开第二个），否则端口 8010 冲突会导致重启失败。重启后同样等待 `/health/live` 返回 OK 再继续。

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

> ⚠️ 重置会删除 `.runtime/` 下的 seekdb 与 SQLite 文件，请务必先停止 server 再执行，避免 SQLite 被占用。

## 13. 常见问题排查（FAQ）

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `PowerContext server unreachable at http://127.0.0.1:8010` | 未启动 server 或未就绪 | 先执行步骤 2 启动 server，等 `/health/live` 返回 OK |
| `uv sync` 报网络错误 | 离线或 PyPI 不可达 | 需要联网；`powercontext==1.4.0`、`pylibseekdb==1.4.0.post1` 版本已锁定在 `uv.lock` |
| `pylibseekdb` 安装失败 | 平台不是 Linux/macOS x86_64 | 嵌入式 seekdb 仅支持 Linux/macOS，请在符合平台的环境执行 |
| 启动时打印「未配置推理模型」 | 未配置 generation/embedding 模型 | 正常现象，本任务只用显式 Memory + FTS，忽略即可 |
| 步骤 5 出现 `6 failed, 7 passed`、退出码 1 | 目标行为尚未实现 | 预期结果，是「代码修改有效」的修改前证据，不是失败 |
| 端口 8010 被占用 | 已有进程占用 | 释放端口，或改用其它端口并同步设置 `POWERCONTEXT_SERVER_URL` |
| `state read` 输出 `{}` | 尚未写入状态，或顺序颠倒 | 先 `state write` 再 `state read`，严格按步骤 6→7→8 顺序 |
| 步骤 8 `diff` 有差异 | 重启用了不同的 env/SQLite 文件 | 用同一个 `.runtime/powercontext.env` 重启 |
