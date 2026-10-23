# 赛题 11：seekdb 单 SQL 混合候选检索

编号：`retrieval-02-hybrid-search`。产品仓库：[suye-17/powercontext](https://github.com/suye-17/powercontext)。

产品实现及独立审核对应提交：`dfd0a12bb5eaaa52a213820942fa185b7722c702`。该提交为本地提交，远端可获取性未验证。

冻结提交：`9db1753434162c0aa7c9f1189a1722e617880a52`。分支基线：`contest/base` 的 `afeac9d0551d439560a36169dba5b3c44991f878`；分支为 `feat/seekdb-hybrid-search-11`。

## 产品行为

`SeekDBMemoryIndex` 在传入的 `AsyncConnection` 上，以一条 `UNION ALL` statement 返回全文和向量的两路有界候选。各通道在数据库内应用 scope、Memory artifact、tag 与 candidate limit，SQL 使用已有的全文、向量投影及原生 L2 查询。应用层仅分流有界结果，继续使用现有 admission、RRF、去重和精确正文读取。

带 tag 的向量通道从符合 tag any/all 条件的身份集合驱动原生向量查找，使用 `LEADING` / `USE_NL` 保持 tag 资格先于该通道的全局 top-k。实际执行计划包含 `TEXT RETRIEVAL SCAN` 和 `VECTOR INDEX ADAPTIVE SCAN (PRE-FILTER)`。没有 Python 全表匹配或距离计算。

引用使用请求中已验证的当前 Memory ref，不用旧 projection 的 `head_revision` 排除仍活跃的条目。沿用现有事务内的投影维护、搜索前后 head 校验及历史 head capability 拒绝行为。普通 FTS、vector 查询委托原策略；公开 API、schema、依赖声明和 SQLite/OceanBase 的索引选择保持原合同。

## 验证环境与证据边界

- 实际运行：Linux x86_64，Python `3.12.3`，`pylibseekdb==1.3.0.post5`，SQLAlchemy `2.0.51`，pyobvector `0.2.28`，pytest `9.1.1`。运行依赖来自仓库 `uv.lock` 的 server 配置，pylibseekdb 单独安装，没有使用会升级它的 seekdb extra 同步。
- 题目指定 `pylibseekdb==1.3.0.post4`。本地无该包，PyPI 与清华镜像的版本列表均未列出该版本，用户亦无该 wheel 或环境。因此下述真实后端执行属于 **post5 辅助验证**，不能证明 post4 兼容。
- 未取得组织方 `challenges/retrieval-02-hybrid-search/acceptance` 以及题述真实 173 文档、20 查询、固定向量和评分 qrels。正式公开验收及正式质量门未验证。
- 没有执行全量测试；按用户要求留给用户手动运行。没有推送、创建 PR 或对外提交。

## 功能验证

真实 embedded seekdb 专项覆盖：单候选 statement、无 tag 和 tag any/all 的双索引 EXPLAIN、每路候选上限、scope/Memory 隔离、超过上限的同距离候选稳定性、FTS/vector 与冻结策略对照、空全文通道，以及正常产品装配。

产品服务测试覆盖：追加推进 head 后旧 projection 的活跃条目仍可检索；正文修订、停用和恢复后的当前 entry version；历史 head 拒绝；matched_by 非空且只含 fts/vector；融合后身份无重复；重复查询顺序稳定；通过 `MemoryService.expand` 逐字回读正文。

已执行检查：

- 聚焦回归：8 个测试文件，共 **58 passed in 36.29s**；其中新增真实 seekdb 专项为 8 项，未跳过。
- `uv run --locked --no-sync .../prek run -a`：全部 hooks 通过，含 Ruff 和全项目 `ty check`。静态工具环境为独立的 Python 3.13 工具环境；没有将它作为真实后端测试环境。
- 变更文件的 Ruff 与 Python 3.11 语义类型检查、`git diff --check` 均通过；`uv lock --locked --offline` 通过，依赖文件未修改。

## 独立审核

独立 subagent 按 `requesting-code-review` 和仓库 `REVIEW.md` 审核了上述实现提交及相关调用链，未发现 Critical、Important 或 Minor 缺陷。审核结论为可本地交付；正式固定版本和真实语料验收仍未验证。

[独立审核验证记录](11-seekdb-hybrid-search-review.json)包括：

- 重新执行同一组聚焦测试：**58 passed in 38.41s，无 skip**。
- 串行重新执行 60 次合成同库对照：每 query Recall/MRR 不退化，平均均为 1.0，p50 为 46.57 ms，候选 statement 为 1 对 2。
- 额外建立 12 条不同向量距离的本 scope 候选、其他 scope 和其他 Memory 各 40 条更近的干扰记录；无 tag、any、all 均返回正确的向量 top-3，并核对真实双索引执行计划。
- 变更文件 Ruff、格式、Python 3.11 语义类型检查及 diff 检查均通过。

精简的 Python 3.12 原生运行环境直接做全项目 `ty check --python .venv` 时出现 65 条诊断，涉及未安装的 langchain、time_machine、datamodel_code_generator、typer 等开发/集成依赖。完整 Python 3.13 工具环境执行全项目类型检查通过。两种环境结果分别保留，不将精简环境的检查报告为通过。

## 同库对照测量

[逐查询结果及 EXPLAIN 算子](11-seekdb-hybrid-search-synthetic.json)来自独立的合成语料：20 个主题，前 13 个主题各 9 篇、其余各 8 篇，共 173 篇；每个主题使用一个 384 维单位基向量，查询只包含该主题词。它覆盖规定的输入规模与候选访问成本，但主题区分容易，不能代表真实语料检索难度。

三个独立 scope 共 60 个测量查询。候选与冻结双查询策略共享同一数据库、Memory head、已入库身份和固定 embedding；逐 query 各预热一次，测量顺序交替。计时包含完整 `MemoryService.search`，精确回读与评分在计时外进行；没有与其他数据库测试并行计时。

| 指标 | 合成输入实测 |
| --- | ---: |
| hybrid 候选 statements / query | 1（冻结路径为 2） |
| 命中有效性与无重复检查 | 全部通过 |
| 每 query Recall@10 / MRR@10 不退化 | 60/60 |
| 平均 Recall@10 | 1.0 |
| 平均 MRR@10 | 1.0 |
| 完整搜索 p50 | 47.65 ms |
| p50 / 冻结路径 p50 | 0.9712 |
| 完整身份、正文、通道、排序 tuple 一致率 | 0.0 |

完整 tuple 一致率不是题目的通过门槛；原生向量同距离候选的排序变化会改变 RRF 分数。上述每 query 质量检查才是该合成输入上的不退化证据。延迟比仅作观测，不宣称端到端加速。

候选次数不包括既有 head 和向量完整性校验；完整性检查仍逐 Memory 读取活跃投影元数据，存在既有线性成本。带 tag 的资格聚合及向量查找成本随选中 tag 身份数增长，candidate limit 约束最终通道候选数，不代表数据库只处理这些条目。本文未证明更大集合的成本上界。

## 复现

建立独立 Python 3.12 环境并安装锁定运行依赖及辅助后端：

```bash
uv sync --locked --no-default-groups --extra server --python 3.12
uv pip install --python .venv/bin/python pytest==9.1.1 pylibseekdb==1.3.0.post5
```

聚焦验证，不运行全量测试：

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q -p no:cacheprovider tests/builtin/persistence/test_seekdb_memory_index.py tests/e2e/test_memory_hybrid_seekdb.py tests/builtin/persistence/test_memory.py tests/builtin/persistence/test_seekdb_profile.py tests/builtin/artifacts/memory/test_fusion.py tests/builtin/artifacts/memory/test_service.py tests/builtin/artifacts/memory/test_reranking.py tests/builtin/runtime/test_composition_embedding.py
```

`scripts/contest_11_hybrid_search.py` 接受 JSON：`documents` 中每项为 `id`、`text`、`embedding`；`queries` 中每项为 `text`、`embedding`、`relevant_document_ids`。embedding 必须为 384 维单位向量；规范化后的 document text 和 id 必须唯一。标签仅由评分进程使用，不传给产品。

```bash
PYTHONPATH=src .venv/bin/python scripts/contest_11_hybrid_search.py --dataset /path/to/dataset.json --output /path/to/result.json --scopes 3
```

合成输入的生成方法如下，可保存到临时 JSON 后传给该命令：

```python
import json
from pathlib import Path

themes = "astronomy botany chemistry geology ecology genetics neurology physics robotics algebra geometry climatology immunology zoology virology metallurgy linguistics archaeology economics oceanography".split()
documents, queries = [], []
for number, theme in enumerate(themes):
    vector = [float(i == number) for i in range(384)]
    ids = []
    for entry in range(9 if number < 13 else 8):
        identity = f"{theme}-{entry}"
        ids.append(identity)
        documents.append({"id": identity, "text": f"{theme} research evidence record {entry}. Controlled offline document for native retrieval.", "embedding": vector})
    queries.append({"text": theme, "embedding": vector, "relevant_document_ids": ids})
Path("/tmp/pc11-synthetic.json").write_text(json.dumps({"name": "synthetic-173-documents-20-topics", "documents": documents, "queries": queries}), encoding="utf-8")
```

正式验收文件与固定 post4 环境可用后，运行题目公开命令；不能用上述合成结果代替：

```bash
export POWERCONTEXT_CANDIDATE=/path/to/product-checkout
export POWERCONTEXT_PUBLIC=/path/to/public-evaluation-checkout
PYTHONPATH="$POWERCONTEXT_CANDIDATE/src:$POWERCONTEXT_PUBLIC" /path/to/fixed-python-3.12 -m pytest -q -p no:cacheprovider "$POWERCONTEXT_PUBLIC/challenges/retrieval-02-hybrid-search/acceptance"
```

全量测试由用户手动运行 `make test`；需要跨版本检查时运行 `tox`。
