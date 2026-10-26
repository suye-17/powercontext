# 运行证据

本目录保存 `scripts/run_scenario.sh` 在验证环境中真实运行产生的记录，用于核对 seekdb 与 PowerContext 是否实际参与产品流程。

## 生成方式

```bash
cd qoder
scripts/run_scenario.sh
```

脚本从空运行时开始，依次执行导入、检索、记忆写入、模型综述、会话写回、PowerContext 重启、评测和 Web 请求。任一步骤的断言失败都会终止脚本。每次运行先清空 `seekdb/`、`powercontext/`、`llm/`、`eval/`、`web/` 五个生成目录，再由 `run-status.txt` 记录 `running` / `complete` / `failed`、起止时间与当前 commit；只有 `status=complete` 的目录可作为一轮完整结果。

## 验证环境

| 项 | 值 |
| --- | --- |
| 系统 | WSL2 Linux x86_64 |
| Python | 3.12.3 |
| PowerContext | 1.4.0（`powercontext[cli,server]`） |
| seekdb | `pylibseekdb` 1.4.0.post1（嵌入式） |
| Web | FastAPI 0.162.0、uvicorn 0.54.0、Jinja2 3.1.6 |
| 嵌入模型 | MiniMax `embo-01`，1536 维，单位归一化 |
| 生成模型 | 通过 `MINIMAX_*` 配置的 OpenAI 兼容对话模型 |
| 语料 | `sample/library/*.md`：6 篇、30 个分块 |

所有记录来自真实服务进程和真实模型调用；测试中的 fake transport 只用于单元测试，不进入本目录。

## 目录与核对点

`run-status.txt` 位于本目录根部，记录本轮运行的状态、时间与 commit；只有 `status=complete` 时，下面的生成目录才构成一轮完整证据。

### `seekdb/`

| 文件 | 内容 | 证明 |
| --- | --- | --- |
| `ingest.json` / `ingest.log` | 导入结果：6 篇、30 块、30 个向量、无嵌入错误 | 论文与分块实际写入 seekdb |
| `ddl.sql` | `SHOW CREATE TABLE` 输出 | `FULLTEXT KEY`、`VECTOR(1536)`、`VECTOR KEY ... DISTANCE=COSINE, TYPE=HNSW` 实际存在 |
| `query-results.txt` | 4 组检索：无过滤、年份过滤、状态过滤、标签过滤，含 FTS 与向量得分 | seekdb 的 FULLTEXT、向量通道与结构化过滤结果；应用层再做加权 RRF |
| `query-*.json` | 上述 4 组检索的逐条 JSON，供脚本断言 | 脚本要求每组查询 `note=null` 且 `vector_score` 非空；嵌入瞬时失败会重试，不会静默回退 |
| `queries.sql` | 可手动执行的 SQL：全文查询、余弦距离查询、schema 与统计 | 评审可重跑同一查询 |

### `powercontext/`

| 文件 | 内容 | 证明 |
| --- | --- | --- |
| `write.log` | 目标、偏好、进度三次写入及 revision | 记忆写入经过 PowerContext HTTP API |
| `list-before.json` / `list-before.txt` | 完成会话后的 4 条记忆 | goal / profile / progress / session 各一条，progress 为 revision 2 |
| `session1.txt` | 结束会话写回结果 | 进度修订与会话摘要同时保存 |
| `scope.json` | scope、条目统计、kind 分布与 hybrid 检索 | scope 内记忆可被语义召回，第一名是 progress |
| `list-after.json` / `list-after.txt` | 重启后的同一批记忆 | 记忆不依赖进程内存 |
| `restart-normalized.json` / `restart-compare.txt` | 重启前后归一化比较 | `restart compare: no differences` |
| `plan-session1.json` / `plan-session2.json` | 会话前后的计划与继续卡片 | 重启后的计划读取到新进度：`rag-survey · Evaluation` |
| `search-hybrid.json` / `search-fts.json` / `recall-summary.txt` | 同一条中文问句的两种召回 | hybrid 第一名是 `paperdesk.progress`；fts 对中文问句无命中 |

### `llm/`

| 文件 | 内容 | 证明 |
| --- | --- | --- |
| `answer-session1.json` / `.txt` | 检索问题、分块、模型综述、引用映射、请求与校验记录 | 综述基于 seekdb 召回内容，引用全部映射到真实分块 |
| `citation-report.json` | 覆盖率、使用到的引用、检索分块 ID 与校验器输出 | 引用不是装饰：每个 `[Cn]` 都能回溯到章节和行号 |
| `digest-rag-survey.json` / `.txt` | 单篇速读与 5 条引用 | 单篇输出同样绑定章节出处 |
| `answer-extractive.json` / `.txt` | 强制抽取式回退 | 模型不可用时可降级，且仍保留引用 |

### `eval/`

| 文件 | 内容 |
| --- | --- |
| `retrieval-metrics-keyword.json` | 纯全文基线：recall@1/3/5 与 MRR，逐条查询命中 |
| `retrieval-metrics.json` | 混合检索：同一查询集，`embedding=true` |
| `metrics.txt` | 两种模式的汇总与阈值断言结果 |

### `web/`

| 文件 | 内容 |
| --- | --- |
| `http-status.txt` | 8 个页面路径与 HTTP 状态，全部为 200 |
| `*.html` | 工作台、资料库、单篇、会话、检索调试、记忆页、提问页的实际响应 |
| `health.json` | `papers=6`、`chunks=30`、`embeddings=30`、`llm_available=true`、`powercontext=true` |

## 验收对照

| 验收项 | 证据 |
| --- | --- |
| 技术结合有效 | `seekdb/` 的 DDL、导入与查询记录；`powercontext/` 的写入、重启复用与语义召回；`llm/` 的引用映射 |
| 项目能够运行 | 本目录全部记录可由 `scripts/run_scenario.sh` 重新生成；`web/` 证明服务启动并可访问 |
| 验证样例 | 初始数据在 `sample/library/`，输入命令与期望结果在 `sample/scenario/steps.md`，实际结果在本目录 |
| 网站可访问 | 站点内容在 `site/`；发布记录与无痕截图由发布者在 `evidence/qoder/` 补充 |

## 真实性说明

- 记忆检索的 hybrid 模式使用真实嵌入服务；如果服务端限流，客户端会回退 fts 并在 `fallback_reason` 中注明。成功运行的 `scope.json` 与 `search-hybrid.json` 应为 `mode=hybrid`。
- 评测数值由固定语料和固定查询集产生，嵌入模型变化会导致数值变化；脚本断言关键词模式 `embedding=false`、混合模式 `embedding=true`，并检查 `recall@5 >= 0.9` 与 `MRR >= 0.6`，避免嵌入失效时静默退化为关键词基线。
- 模型综述的措辞可能因采样而变化，但脚本会断言 `mode=llm`、`coverage=1.0` 且无 `fallback_reason`，引用映射逐条校验。
- 本目录不包含 API 密钥、令牌或个人信息；密钥只存在于被忽略的 `qoder/.env`。
