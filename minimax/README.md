# 规有所依：面向软件持续演进的 SDD 上下文管理系统

本目录是 MiniMax × OceanBase 生态题的提交材料。系统为一个真实的 `tasklite` 业务工程构建任务上下文：从规格、代码、测试中选出与任务相关的内容，交给 MiniMax 完成代码修改，把工程资料/索引保存在嵌入式 seekdb，把任务进展保存在 PowerContext，并在重启后读取复用。

## 六项验收与落点

| 验收项 | 本仓库证据 |
| --- | --- |
| 能够运行 | 本 README、`sample/steps.md`、`sample/reset.sh`、`uv.lock` |
| MiniMax 实际参与 | `evidence/minimax/request.json`、`response.json`、`output.txt`、`picked.patch`、`patches/tasklite-min-priority.patch` |
| 上下文正确 | `evidence/context/context-v1.json`、`context-v2.json`、`sample/expected-context-v1.json`、`sample/expected-context-v2.json`、`evidence/context/spec-update-comparison.txt` |
| 数据库实际参与 | `src/sdd_context/store.py`、`evidence/db/ddl.sql`、`queries.sql`、`query-results.txt`、`ingest.log` |
| 状态能够复用 | `src/sdd_context/powercontext.py`、`evidence/powercontext/write.json`、`read-before-restart.json`、`read-after-restart.json`、`restart-compare.txt` |
| 代码修改有效 | `snapshots/before/`、`snapshots/after/`、`patches/`、`logs/before-tests.log`、`logs/after-tests.log`、`logs/regression.log`、`evidence/tests-identical.txt` |

评分是全有或全无：以上六项必须全部通过。

## 提交材料对照

| 材料 | 位置 |
| --- | --- |
| 完整源码 | `src/sdd_context/`、`scripts/`、`demo/tasklite/` |
| 运行说明 | `README.md`、`sample/steps.md` |
| 验证样例 | `sample/task.md`、`sample/spec-v1.md`、`sample/spec-v2.md`、`sample/initial-data/`、`sample/expected-context-v1.json`、`sample/expected-context-v2.json`、`sample/steps.md`、`sample/reset.sh` |
| 代码与测试 | `snapshots/before/`、`snapshots/after/`、`patches/tasklite-min-priority.patch`、`logs/`、`evidence/tests-identical.txt` |
| 技术使用记录 | `evidence/minimax/`、`evidence/db/`、`evidence/powercontext/` |
| 一页技术说明 | `TECHNICAL.md` |

## 提交状态

当前工作区基于 `2026-competition-ecosystem` 的 `c7f8f345`。`minimax/` 目录提交后的 commit SHA 以测评平台记录为准；测试日志同时记录了冻结基线 `d647275`、当前分支头和每个快照的 `snapshot sha256`。

## 目录结构

```text
minimax/
├── src/sdd_context/           # 上下文模块：CLI、解析、seekdb 存取、检索、PowerContext
├── demo/tasklite/             # 业务工程最终状态（修改后）
├── snapshots/before/tasklite/ # 修改前快照（目标测试已加入、目标行为缺失）
├── snapshots/after/tasklite/  # 修改后快照（13 个测试全部通过）
├── patches/                   # MiniMax 修改对应的 unified diff
├── sample/                    # 任务、规格 v1/v2、预期上下文、复现步骤、重置脚本
├── evidence/                  # 上下文、MiniMax、seekdb、PowerContext 证据
├── logs/                      # before / after / regression 测试日志
├── scripts/                   # MiniMax 连通性检查和补丁生成脚本
├── pyproject.toml / uv.lock   # 依赖
├── powercontext.env.example   # PowerContext server 配置模板（无凭据）
└── .env.example               # MiniMax/上下文模块配置模板（无凭据）
```

## 环境要求

- Python 3.11+（本次在 3.12.3 上验证）
- `uv` 0.13.4
- Linux 或 macOS（嵌入式 `pylibseekdb` 的平台限制）
- PowerContext 1.4.0（由项目 `server` extra 安装）
- 可选：MiniMax Token Plan/API key（仅重跑 MiniMax 调用时需要）

## 依赖版本

| 依赖 | 版本 |
| --- | --- |
| Python | >=3.11（验证 3.12.3） |
| uv | 0.13.4 |
| powercontext | 1.4.0 |
| pylibseekdb | 1.4.0.post1 |
| pymysql | 1.2.0 |
| pytest | 8.4.2 |

## 快速开始

```bash
cd minimax
uv sync --extra dev --extra server
```

启动 PowerContext（端口 8010，SQLite 持久化）：

```bash
mkdir -p .runtime
sed "s#/absolute/path/to/minimax#$PWD#" powercontext.env.example > .runtime/powercontext.env
PYTHONDONTWRITEBYTECODE=1 .venv/bin/powercontext server run --env-file .runtime/powercontext.env
```

另开终端，导入 before 工程并构建上下文：

```bash
uv run --extra dev sdd-context ingest --root snapshots/before/tasklite
TASK='实现 SPEC-003 的 min_priority 过滤和 SPEC-004 的 priority 校验，涉及 list_tasks 与 add_task，目标测试是 test_list_tasks_filters_by_min_priority 和 test_add_task_rejects_invalid_priority'
uv run --extra dev sdd-context build --task "$TASK" --top-k 3
```

上下文包包含四类内容：适用规格条目、需要修改的代码文件/符号、验证用测试、已完成/待办/验证结果。每项都带真实 `path` 与行号/符号/测试名。

完整的 before/after、状态重启和规格更新步骤见 [sample/steps.md](sample/steps.md)。

## 上下文模块调用入口

```bash
sdd-context ingest --root <project>          # 解析规格/代码/测试并写入 seekdb
sdd-context build --task "<task>" --top-k 3  # 检索并输出上下文包（JSON 或 Markdown）
sdd-context state write --file <state.json>  # 写入 PowerContext Memory
sdd-context state read                       # 从 PowerContext 读取状态
sdd-context reset                            # 清空 seekdb 语料表
```

配置覆盖：`SDD_ENV_FILE`、`SDD_DB_PATH`、`POWERCONTEXT_SERVER_URL` 环境变量。默认数据库在 `.runtime/seekdb`，默认 PowerContext 地址是 `http://127.0.0.1:8010`。

## MiniMax 接入

- 方式：HTTP API，`POST https://api.minimaxi.com/v1/chat/completions`
- 接口版本：OpenAI-compatible `/v1/chat/completions`
- 模型：`MiniMax-M3.1-Flash`（以实际 Token Plan 为准）
- 调用日期：2026-10-26
- 开发记录：`evidence/minimax/README.md`
- 密钥：不进入仓库。重跑时复制 `.env.example` 为 `.env`，填入 `MINIMAX_API_KEY` 与 `MINIMAX_MODEL`；`.env` 已被 `.gitignore` 忽略。
- 评审不需要重新调用模型；`evidence/minimax/` 已保存请求、响应、模型原始输出和最终补丁，并与 `patches/`、`logs/` 对应。

## 数据库设计

seekdb 保存三张表：

- `pc_corpus_documents`：规格/代码/测试文件原文
- `pc_corpus_chunks`：按规格条目、顶层函数/类、测试函数切分后的分块，带路径、起止行、`spec_id`/`symbol`/`test_name`
- `pc_corpus_chunks.searchable_text` 上的 `FULLTEXT` 索引：用于 `MATCH ... AGAINST` 检索

检索时先按 `spec`/`code`/`test` 三类做全文检索，再对任务中出现的 `SPEC-003`、`list_tasks`、目标测试名等标识符做精确命中加权。查询结果直接进入上下文包。

## PowerContext 状态

- 显式 Memory：`kind="sdd-task-state"`，内容为任务目标、已完成、待办、验证结果。
- 写入：`POST /v1/memory/remember`
- 读取：`POST /v1/memory/search`，`mode="fts"`
- 重启验证：停止 server，用同一 SQLite 文件重启，`state read` 输出与重启前一致；`build` 输出中的 `state.task_id` 为 `tasklite-min-priority`。

## 已知限制

- 嵌入式 seekdb 仅支持 Linux/macOS。
- 当前检索是全文检索 + 标识符精确加权，没有使用向量/语义检索；这是为了减少模型依赖并保证可复现。
- PowerContext 只使用显式 Memory 和 FTS，不依赖生成/embedding 模型。
- `tasklite` 是小型示例业务工程，不是大型生产系统；赛题允许自选/自写业务工程。
- MiniMax 调用需要外部网络和 Token Plan；评审可只核对已记录的开发记录，不需要重新生成补丁。

## 安全

- 仓库不包含真实凭据；`.env`、`.env.*`、`.runtime/` 已在 `.gitignore` 中忽略。
- MiniMax 响应中的 `reasoning_content` 已从保存的记录中删除，不提交模型私有思维链。
