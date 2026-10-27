# 财报 PDF 全文解析与证据页检索：实现与验证

赛题编号：`source-03-pdf-retrieval`。产品仓库：<https://github.com/suye-17/powercontext>。实现分支：`feat/source-03-pdf-retrieval`，提交 `dbc093b5098fe7b08704855c47804d6129c1e038`。

冻结基线 `9db1753434162c0aa7c9f1189a1722e617880a52` 是 HEAD 的祖先。工作区位于 `/home/duan/work/wt-contest/source-03-pdf-retrieval`。

## 一、上次提测未通过的原因

公开自测 6 项全部通过，但它只验证解析、真实入库与精确引用，不测题面公布的三个质量门槛；质量 gold 不分发，上次提交时这三项被标为「未验证」。

用 FinanceBench 公开标注（固定 revision `cc39aeb4`，5 个问题共 8 个标注页）复算上次实现在公开集上的成绩：

| 门槛 | 要求 | 上次实现 | 结论 |
| --- | --- | --- | --- |
| 证据词项召回率（最差） | ≥ 0.95 | 0.996 | 通过 |
| 标注整页词项 F1（最差） | ≥ 0.95 | 0.979 | 通过 |
| evidence-page Recall@20 | ≥ 0.70 | **5/8 = 0.625** | **未通过** |

失败项只有证据页召回，原因有两条：

1. 原实现把问题切成 4 条 FTS 查询再用 RRF 合并。Memory 的 FTS 先按 BM25 取回 `limit` 条候选，再按查询词覆盖度准入（长问题要求命中约 25% 的不同查询词）。长问题在候选被截断后又被准入过滤，剩下的候选极少，召回被这一步压死。
2. 单页 BM25 无法把「问题措辞」与「页面措辞」对不上的页排进前 20。典型是 `legal battles` 对应的页面写的是 `Legal Proceedings`，`geographies` 对应 `GEOGRAPHIC OPERATIONS`，而 Analyzer 不做词干化；把词干化直接加到索引上会合并 `statement`/`state` 一类词、抬高 df，实测反而把公开集从 0.625 拉到 0.25，因此没有采用。

## 二、改动

`src/powercontext/sources/pdf_pages.py`

1. **离线排序替代 FTS 查询拼装。** 通过 `memory.list()` 读取 scoped Memory 的当前条目，只保留恰好引用一个 `pdf-page` Source 的条目，按条目文本建 BM25 索引（k1 = 1.2，b = 0.75）。题面明确允许「离线查询处理及重排」；引用仍逐条来自真实条目，不合成，不读 gold。
2. **标题字段加权。** 条目首行是文档身份行，其后是页面运行标题。10-K 的 `Item 3. Legal Proceedings`、`CONSOLIDATED STATEMENTS OF INCOME` 正是问题用词的来源，正文则多是数字表格；因此对前 6 行内的短行单独计权（权重 2.0）。
3. **相邻页扩展。** 财务报表跨物理页，取前 `ceil(limit × 0.6)` 个锚点页后，把它们 ±1、±2 且自身也有命中的页一并纳入，再按原排名补足。
4. **页 Source 名称自描述。** `pdf-page:<document_sha256>:<page_number>:<digest>`。末尾摘要仍由 `(document_id, document_sha256, page_number)` 决定身份，前置的文档摘要与页码让引用可直接按文档分组、定位物理页，无需解析 Source；最长 146 字符，在 256 上限内。
5. **保留 FTS 回退。** 原策略收为 `_search_by_lexical_hits`，仅在 Memory facade 没有 `list` 时使用。

`tests/test_pdf_pages.py`：旧用例冻结了 FTS 查询串与候选预算（属实现细节），改为断言可观察行为：只返回单页引用、同页去重、按匹配排序、标题字段影响排序、邻页提升、跨文档不串页、确定性、limit 上限、回退路径与入参校验。

## 三、验证证据

环境：Python 3.12.3、pypdf 6.19.0，公开评测 checkout `dab240063bfcfa7917898dc44da4baadb3747bd0`。全部在本机执行，未使用官方裁判容器或其资源配额。

公开测试集在**干净 checkout** 中复跑，排除本地环境污染：从 `upstream/oceanbase/powercontext` 拉取 `2026-competition` 分支，HEAD 为 `dab240063bfcfa7917898dc44da4baadb3747bd0`（与本机原 checkout 一致，上游没有新增提交），在 `/tmp/pub-clean` 新建 worktree 并用 `make install` 重建固定依赖后运行：

```bash
PRODUCT=/home/duan/work/wt-contest/source-03-pdf-retrieval make check-baseline
PRODUCT=/home/duan/work/wt-contest/source-03-pdf-retrieval make test-pdf
```

- `check-baseline`：`Frozen baseline is reachable from HEAD or a fetched branch.`
- `make test-pdf` 连跑两次：`6 passed in 58.49s`、`6 passed in 66.13s`

该题公开测试集共 6 个用例：`test_complete_public_report`、`test_blank_pages_and_document_revisions`、`test_empty_password_permission_marker_is_readable`，以及 `test_corrupt_documents_fail_atomically` 的三个参数化输入（空文件、非 PDF、截断）。完整输出见[公开验收日志](source-03-pdf-retrieval-public.txt)。

题面 `instruction.md` 里的裸命令少了 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`：直接照抄会因 `pytest_asyncio` 被自动加载、又与 `pytest.ini` 里的 `-p pytest_asyncio.plugin` 重复注册而抛 `ValueError: Plugin already registered under a different name: asyncio`。这是公开包自身的问题，与本实现无关；用 `make test-pdf`，或补上该环境变量即可复现通过。

仓库回归：

```bash
uv run --group dev python -m pytest tests/test_pdf_pages.py tests/e2e/test_pdf_page_sources.py \
  tests/test_sources.py tests/test_source_observations.py -q
```

结果 `34 passed in 4.72s`。静态检查 `uv run --group dev prek run -a` 的 ruff check、ruff format、ty check 与全部卫生钩子通过。

质量门槛按题面口径复算（`[a-z]+|\d+` 分词、按词频多重集合取交集、0 基标注页码转物理页）：

| 门槛 | 要求 | 公开集实测 |
| --- | --- | --- |
| evidence-page Recall@20 | ≥ 0.70 | **7/8 = 0.875** |
| 证据词项召回率（最差） | ≥ 0.95 | 0.996 |
| 标注整页词项 F1（最差） | ≥ 0.95 | 0.979 |

逐题：`01009` 2/2、`00735` 0/1、`01328` 1/1、`03620` 2/2、`04481` 2/2。`00735` 的标注页（第 26 页 `Item 3. Legal Proceedings`）与问题只共享 `legal` 一个实词，是公开集里唯一仍未命中的标注页。

跨文档解析质量（10 份 10-K、58 个标注页）：证据词项召回最差 0.980，整页 F1 最差 0.975，均达标。

留出集代理（AMD / American Express / Boeing / 3M，823 页、24 问）：Recall@20 = 10/28 = 0.357。该代理不是官方留出集，其中多数问题的标注页与问题只共享公司名或年份（例如 AMEX「geographies … primarily operates in」的标注页不含 `geographies`、`operates`、`2022` 中任何一个词），纯词法检索无法命中，因此这个数字不能当作官方留出集的预测。

## 四、残留风险

| 风险 | 说明 |
| --- | --- |
| 官方留出集不可验证 | 质量 gold 不分发。公开集 0.875 达标，但留出集若与上面的代理集一样偏向「问题与页面无词法重叠」，Recall@20 仍可能低于 0.70。 |
| Memory 条目 8192 字节上限 | Memory 单条正文上限 8192 UTF-8 字节。若某页 `DOCUMENT_ID\nPAGE_TEXT` 超过该值、而裁判的固定 pipeline 不分块，入库会抛 `InvalidMemoryCandidateError`，本题得 0。10 份样例中 `PFIZER_2021_10K` 有 4 页越界（最大 8582 字节）。本实现无法影响裁判侧 pipeline；截断页文本会违反「完整解析」与词项数门槛，因此保持全文。 |
| `memory.list()` 的口径 | 题面要求「通过公开 Memory API 搜索」，同时允许离线处理与重排。本实现用同一 facade 的公开 `list()` 读取条目再本地排序；若裁判按更窄口径要求必须调用 `search()`，该路径不被覆盖。 |
| 每次调用重建索引 | 1151 页约 0.3–0.5 s/次，24 问约 7–13 s。未做跨调用缓存，避免读到过期条目。 |
| 逐页词项数 90%–110% | 冻结值不分发。本次未改动解析路径，且与 FinanceBench 整页文本的 F1 最差 0.975，但该项仍无法在本地直接验证。 |

## 五、提交信息

- 仓库：<https://github.com/suye-17/powercontext>
- 分支：`feat/source-03-pdf-retrieval`
- 提交：`dbc093b5098fe7b08704855c47804d6129c1e038`
- 本题两个提交：
  - `c6298f2f918efc8c8ef7b3d8bc0b92a9d3b3b001` — 检索排序改为离线 BM25 + 标题加权 + 邻页扩展
  - `dbc093b5098fe7b08704855c47804d6129c1e038` — 邻页分组改用完整文档摘要，补跨文档回归
- 未推送、未开 PR、未对外提交。
