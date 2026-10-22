# 2026 OceanBase 数据库大赛 · PowerContext 赛题

> 来源：语雀《seekdb》知识库 → [PowerContext](https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/gp9hi8ikdnef8ryc)（book id `81051108`）。
> 抓取时间 2026-10-20，正文取自 Yuque 公开 API 的 `sourcecode` 字段（原始 Markdown，未经改写）。
> 刷新：`python3 crawl_yuque.py`；只校验不写盘：`python3 crawl_yuque.py --check`。

## 题目一览

PowerContext 方向共 11 题，**合计 180 分**。

| # | 文件 | 编号 | 分值 | 任务 |
|---|---|---|---|---|
| 1 | [01-fork仓库.md](01-fork仓库.md) | `setup-00-repository-fork` | 10 | 创建 GitHub 公开 fork，提交仓库 URL（校验 `fork=true`、`source.id=1093180734`） |
| 2 | [02-CSV行级采集与原始记录回查.md](02-CSV行级采集与原始记录回查.md) | `source-01-table-evidence` | 10 | 按主键逐行采集离线 CSV，检索引用可回查完整原始行 |
| 3 | [03-Pi会话分支采集与断点恢复.md](03-Pi会话分支采集与断点恢复.md) | `source-02-pi-history` | 10 | 采集 Pi 所选分支的完整可见事件，进度持久化、重启续采、分支切换去重 |
| 4 | [04-财报PDF全文解析与证据页检索.md](04-财报PDF全文解析与证据页检索.md) | `source-03-pdf-retrieval` | **50** | 完整解析财报 PDF 全部物理页；自然语言问题命中可回查的证据页（FinanceBench） |
| 5 | [05-HTTP重排服务接入与跨批次排序.md](05-HTTP重排服务接入与跨批次排序.md) | `retrieval-01-reranking` | 10 | 接入 HTTP 评分服务：按批次上限拆分、跨批次统一排序、保留候选身份 |
| 6 | [06-上下文增量传输与完整重建.md](06-上下文增量传输与完整重建.md) | `context-02-incremental-pack` | 10 | Context Pack 增量 delta 传输，接收端仅凭旧包+delta 重建与 prepare 完全一致的正文 |
| 7 | [07-按新增来源条目数触发记忆处理.md](07-按新增来源条目数触发记忆处理.md) | `processing-01-count-trigger` | 10 | 按待处理 Source 条目数自动触发 Memory / Topic Memory 处理 |
| 8 | [08-Embedding并发合并与向量缓存.md](08-Embedding并发合并与向量缓存.md) | 待更新 | 10 | Embedding 并发请求合并与向量缓存 |
| 9 | [09-来源撤回后的记忆停用与恢复.md](09-来源撤回后的记忆停用与恢复.md) | `memory-01-source-lifecycle` | 20 | 按来源依赖传播停用/恢复，搜索与 Context 反映状态并保留历史引用 |
| 10 | [10-按查询和预算选择原文证据.md](10-按查询和预算选择原文证据.md) | `context-01-evidence-selection` | 20 | 按查询选原文片段，正文+引用+格式共同满足 UTF-8 字节预算（SciFact） |
| 11 | [11-seekdb混合检索单SQL候选查询.md](11-seekdb混合检索单SQL候选查询.md) | `retrieval-02-hybrid-search` | 20 | 一条 SQL 同时取 seekdb 全文+向量候选，保持过滤、当前版本引用与检索质量 |

总览页见 [00-总览.md](00-总览.md)。

## 共性规则（多题重复出现，已确认）

- **提交产物**：基于冻结版本的产品仓库 HTTPS URL + 完整 40 位 commit SHA。题一只需 URL。
- **冻结版本**：PowerContext **v1.2.0**，commit `9db1753434162c0aa7c9f1189a1722e617880a52`。
- **判题范围**：只加载产品的 `src/`，使用固定依赖与资源配置；自带测试/文档不进判题环境。
- **公开自测**：组织方在 PowerContext 仓库的**公开测评分支**发布 `challenges/<编号>/acceptance`，用
  `PYTHONPATH="$POWERCONTEXT_CANDIDATE/src:$POWERCONTEXT_PUBLIC" python -m pytest ...` 运行。
- 除题一（10 分制二值）外，多数题为"必测组全过得分，任一失败 0 分"，不设部分分（逐题以正文为准）。

## 抓取时发现的两件事

1. **题八正文尚未发布**：语雀上第 8 篇的标题是"Embedding 并发请求合并与向量缓存"，但正文与第 7 篇
   `processing-01-count-trigger` 完全相同（`word_count` 都是 1975，描述也一致）。属于组织方复制占位，
   需要等更新后重跑 `crawl_yuque.py`。
2. **本地 vendored 副本比冻结版新 176 个 commit**：`powercontext/` 当前 HEAD 为 `caf2a35a`（上游 master），
   冻结版 `9db1753` 是其祖先。**备赛实现应基于 `9db1753` 起分支，而不是直接在当前 HEAD 上改**。

## 文件清单

```
docs/powercontext-contest/
  README.md            本文件
  crawl_yuque.py       抓取脚本（可重跑/校验）
  manifest.json        各篇标题、slug、更新时间、字符数
  00-总览.md ... 11-*.md   12 篇正文
```
