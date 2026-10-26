# 验证样例：从空状态到「继续上次」

这条样例从一个空运行时开始，导入 6 篇英文论文笔记，完成一次检索、提问、写回记忆、重启服务、继续阅读和检索评测，最后用 Web 工作台核对结果。所有命令都可重复执行。

## 1. 初始数据

- 资料库：`sample/library/*.md`，6 篇带 front matter 的英文论文笔记，共 30 个章节分块。
- 评测集：`sample/eval/queries.json`，10 条带期望论文与章节的查询。
- 运行时：`qoder/.runtime/`，包含 seekdb 数据文件、PowerContext home、进程日志和 pid 文件；全部由脚本创建。
- 外部服务：PowerContext 监听 `127.0.0.1:8011`，Web 工作台监听 `127.0.0.1:8080`。

`seekdb` 是嵌入式数据库，不需要单独部署；`pylibseekdb` 会在 `.runtime/seekdb/` 下创建数据目录。

## 2. 准备

```bash
cd qoder
cp .env.example .env          # 填入 MINIMAX_API_KEY / MINIMAX_BASE_URL / MINIMAX_MODEL
cd app && uv sync --extra dev --extra server && cd ..
```

`scripts/run_scenario.sh` 和 `scripts/start_powercontext.sh` 会自动读取 `qoder/.env`。没有模型密钥时，全文检索、抽取式摘要和记忆读写仍可运行，但不会有向量召回和模型综述。

## 3. 一键运行

```bash
cd qoder
scripts/run_scenario.sh
```

脚本依次执行下列步骤，并把每一步的原始输出写入 `qoder/evidence/`。任何断言失败都会以非零状态退出。

### 3.1 清空运行时

```bash
sample/scenario/reset.sh
```

只允许清空 `qoder/` 目录内的路径；外部路径需要显式设置 `PAPERDESK_ALLOW_EXTERNAL_RESET=1`。

**期望**：停止旧 Web 与 PowerContext 进程，删除 `qoder/.runtime/`，下次启动得到全新的 scope 和数据库。

### 3.2 启动 PowerContext

```bash
scripts/start_powercontext.sh
```

**期望**：`PowerContext ready: http://127.0.0.1:8011`。配置了 MiniMax 时输出 `embedding: MiniMax embo-01 (1536 dimensions)`。

### 3.3 导入 seekdb

```bash
cd app && .venv/bin/paperdesk ingest --json
```

**期望**：

```json
{"documents": 6, "chunks": 30, "embedded": 30, "embedding_error": null}
```

表结构写入 `evidence/seekdb/ddl.sql`，可见：

- `pd_documents` 上的 `FULLTEXT KEY ft_title`；
- `pd_chunks` 上的 `FULLTEXT KEY ft_searchable` 与 `VECTOR KEY ix_chunk_embedding ... DISTANCE=COSINE, TYPE=HNSW`；
- `pd_chunks.embedding VECTOR(1536)`。

### 3.4 检索与过滤

```bash
.venv/bin/paperdesk search "chunk boundaries" --json
.venv/bin/paperdesk search "chunk boundaries" --year-min 2023 --json
.venv/bin/paperdesk search "rerank" --status reading --json
.venv/bin/paperdesk search "citation coverage" --tag citation --json
```

**期望**（`evidence/seekdb/query-results.txt` 与 4 个 `query-*.json`）：

- 无过滤时 `chunking-notes` 的章节排在最前，并同时带 `fts_score` 与 `vector_score`；脚本要求 4 组查询都带向量分，嵌入瞬时失败会重试；
- `--year-min 2023` 后 2022 年的 `chunking-notes` 不再出现；
- `--status reading` 只返回 front matter 标记为 `reading` 的 `rerank-notes`；
- `--tag citation` 只返回 `citation-grounding` 与 `eval-notes`。

### 3.5 写入记忆

```bash
.venv/bin/paperdesk memory set-goal --goal-id weekly \
  --text "本周读完 RAG 相关两篇，并整理 rerank 要点" --tags "rag,rerank"
.venv/bin/paperdesk memory set-profile --digest-sentences 3 --weekly-minutes 120
.venv/bin/paperdesk memory set-progress --paper-id rag-survey \
  --status reading --last-section Results --next-action "读 Evaluation 章"
```

**期望**（`evidence/powercontext/write.log`）：三个原子记忆条目创建成功，`progress` revision 为 1。

### 3.6 计划、综述与会话写回

```bash
.venv/bin/paperdesk plan --json
.venv/bin/paperdesk answer "rerank 在 RAG 里解决什么问题？" \
  --evidence ../evidence/llm/answer-session1.json
.venv/bin/paperdesk memory finish-session \
  --paper-id rag-survey --status reading --last-section Evaluation \
  --next-action "整理 rerank 与 RAG 的关系" \
  --questions "rerank 在 RAG 里解决什么问题？" \
  --next-steps "读完 Evaluation 章,整理 rerank 要点"
```

**期望**：

- 计划前两名是 `rag-survey`、`rerank-notes`；继续卡片指向 `rag-survey · Results`。
- 综述由模型生成（`mode=llm`、覆盖率不低于 0.50、无 `fallback_reason`），含 `[C1]` 形式的引用，引用编号全部对应检索分块；`evidence/llm/citation-report.json` 记录覆盖率和校验结果。
- 结束会话后 `progress` 修订到 revision 2，并新增一条 `paperdesk.session` 摘要。

### 3.7 重启并复用记忆

脚本停止并重新启动 PowerContext，然后重新读取计划。

**期望**（`evidence/powercontext/`）：

- `list-before.json` 与 `list-after.json` 归一化后完全一致，`restart-compare.txt` 为 `restart compare: no differences`；
- 继续卡片变为 `rag-survey · Evaluation`，下一步为「整理 rerank 与 RAG 的关系」；
- `plan-session2.json` 的第一名仍是 `rag-survey`。

### 3.8 单篇速读与记忆召回

```bash
.venv/bin/paperdesk digest rag-survey --evidence ../evidence/llm/digest-rag-survey.json
.venv/bin/paperdesk memory search "上次读到哪篇论文，下一步做什么" --mode hybrid --json
.venv/bin/paperdesk memory search "上次读到哪篇论文，下一步做什么" --mode fts --json
```

**期望**（`evidence/powercontext/search-hybrid.json` 与 `recall-summary.txt`）：hybrid 模式第一名是 `paperdesk.progress`，正文包含 `rag-survey`；fts 模式对这条中文自然语言问句没有命中，说明语义召回由向量完成，客户端在服务端 hybrid 不可用时会自动回退到 fts。

### 3.9 检索评测

```bash
.venv/bin/paperdesk eval --queries ../sample/eval/queries.json --no-embed --json
.venv/bin/paperdesk eval --queries ../sample/eval/queries.json --json
```

**期望**（`evidence/eval/metrics.txt`，参考值）：

```text
keyword {'recall@1': 0.6, 'recall@3': 0.9, 'recall@5': 1.0, 'mrr': 0.7533, 'embedding': False}
hybrid  {'recall@1': 0.6, 'recall@3': 0.9, 'recall@5': 1.0, 'mrr': 0.7533, 'embedding': True}
```

脚本断言关键词模式 `embedding=false`、混合模式 `embedding=true`，以及两种模式的 `recall@5 >= 0.9`、`MRR >= 0.6`；嵌入失效时不会静默退化为关键词基线。换用不同嵌入模型时具体数值可能变化，阈值断言不变。

### 3.10 Web 工作台

脚本启动 `paperdesk serve --port 8080`，请求 8 个页面并把 HTML 保存到 `evidence/web/`。

**期望**：`http-status.txt` 中 8 行全部为 `200`；`health.json` 中 `papers=6`、`chunks=30`、`embeddings=30`、`llm_available=true`、`powercontext=true`。

浏览器打开 `http://127.0.0.1:8080/` 可以看到：

- 首页的「继续上次」卡片指向 `rag-survey · Evaluation`；
- 资料库的检索结果附带 FTS / 向量得分；
- `/debug/retrieval` 展示融合排名与引用映射；
- `/memory` 展示 4 个条目的 kind、版本和内容。

## 4. 结果对照

| 检查点 | 机器可读期望 | 实际记录 |
| --- | --- | --- |
| 导入 6 篇 / 30 块 / 30 向量 | `sample/expected/scenario.json` | `evidence/seekdb/ingest.json` |
| 过滤生效 | 同上 | `evidence/seekdb/query-results.txt` |
| 记忆写入与修订 | 同上 | `evidence/powercontext/write.log`、`scope.json` |
| 重启后记忆不变 | 同上 | `evidence/powercontext/restart-normalized.json` |
| 继续卡片与计划 | 同上 | `evidence/powercontext/plan-session1.json`、`plan-session2.json` |
| 引用校验 | 同上 | `evidence/llm/citation-report.json` |
| 评测阈值 | 同上 | `evidence/eval/metrics.txt` |
| Web 页面 | 同上 | `evidence/web/http-status.txt` |
| 本轮完整性 | `run_status=complete` | `evidence/run-status.txt` |

## 5. 重置

```bash
sample/scenario/reset.sh
```

然后重新执行 `scripts/run_scenario.sh` 即可得到同样的初始状态。评测数值由固定语料和固定查询集产生；模型综述的措辞和覆盖率会随采样略变，但引用校验规则与不低于 0.50 的阈值保持一致。

## 6. 技术分工

- **seekdb**：保存论文与章节分块，提供 FULLTEXT、结构化过滤和 HNSW 向量索引；检索结果直接进入计划、速读与综述，不是旁路演示。
- **PowerContext**：保存阅读目标、偏好、进度与会话摘要；进度通过原子记忆 artifact 的 ETag 修订，重启后仍可读取，并改变下一次计划和继续卡片。
