# 一页技术说明

## 1. 模块结构

`src/sdd_context/` 是上下文模块，CLI 入口为 `sdd-context`：

- `config.py`：读取 `.env` / `SDD_ENV_FILE`，解析 MiniMax、seekdb 和 PowerContext 配置。
- `ingest.py`：解析规格 Markdown、Python 代码和测试，切成带来源定位的 chunk。
- `store.py`：启动嵌入式 seekdb（`pylibseekdb`），通过 `pymysql` 建表、写入、全文检索、精确命中查询。
- `select.py`：按任务选择规格/代码/测试，合并全文分数与标识符精确命中分数。
- `powercontext.py`：PowerContext HTTP 客户端，负责 Scope 解析、Memory 写入和 FTS 检索。
- `render.py` / `cli.py`：JSON/Markdown 输出与 `ingest`、`build`、`state`、`reset` 子命令。

## 2. 上下文选择

1. 规格按 `## SPEC-xxx` 切分，每个条目保留 `path`、`spec_id`、起止行和正文。
2. 代码用 Python `ast` 提取顶层函数/类，保留 `symbol`、起止行和源码。
3. 测试用 `ast` 提取 `test_*` 函数，保留 `test_name`、起止行和源码。
4. 任务文本提取 `SPEC-003` 等规格编号和 `min_priority`、`list_tasks` 等标识符。
5. 对 `spec`/`code`/`test` 三类分别执行 `MATCH ... AGAINST` 全文检索；再对任务中出现的标识符做 `spec_id`/`symbol`/`test_name` 精确匹配，并给予更高权重。
6. 每类取 Top-K，输出真实 `path` 和行号/符号/测试名；规格更新后重新导入，chunk 文本和行号会变化，输出随之变化。

## 3. seekdb 存储

- `pc_corpus_documents`：文件级原文和内容哈希。
- `pc_corpus_chunks`：chunk 级正文、`searchable_text`、来源定位字段。
- `searchable_text` 上建 `FULLTEXT` 索引；`spec_id`、`symbol`、`test_name` 上建普通索引。
- 查询记录见 `evidence/db/queries.sql` 和 `query-results.txt`。

## 4. PowerContext 状态

- 显式 Memory：`kind="sdd-task-state"`，保存任务目标、已完成、待办和验证结果。
- 写入 `POST /v1/memory/remember`，读取 `POST /v1/memory/search`（`mode="fts"`）。
- 状态从空开始写入；停止并重启 PowerContext Server 后读取结果一致，并进入 `build` 输出的 `state` 字段。
- 不使用生成/embedding 模型，避免额外模型依赖。

## 5. MiniMax 接入

- 方式：MiniMax HTTP API，`POST /v1/chat/completions`，模型 `MiniMax-M3.1-Flash`。
- `scripts/minimax_edit.py` 把任务、规格、当前代码和目标测试组成提示词，要求模型返回 unified diff。
- 保存 `request.json`、`response.json`、`output.txt`、`picked.patch` 和 `apply.log`；响应中的 `reasoning_content` 被删除。
- 模型输出与最终 `patches/tasklite-min-priority.patch` 对应；测试未被修改。

## 6. 六项验收

| 验收项 | 实现 |
| --- | --- |
| 能够运行 | `uv sync --extra dev --extra server`；`sample/steps.md` 可从干净环境复现 |
| MiniMax 实际参与 | HTTP API 调用记录 + 模型原始输出 + 最终 diff |
| 上下文正确 | 四类输出 + 规格 v1/v2 重导入对比 |
| 数据库实际参与 | seekdb 三张表 + 全文/精确查询 + 查询结果进入上下文 |
| 状态能够复用 | Memory 写入 → 重启 Server → 读取 → 进入上下文 |
| 代码修改有效 | before 6 failed/7 passed → after 13 passed；回归 4 passed |

## 7. 取舍与限制

- 业务工程选择自写的小型 `tasklite`，依赖少、测试快，便于评审复现。
- 检索采用全文 + 标识符加权，不引入 embedding，保证确定性和离线可复现性。
- 演示规模使用全量重建索引；未宣称增量索引性能。
- 嵌入式 seekdb 仅支持 Linux/macOS；PowerContext 固定 1.4.0。
