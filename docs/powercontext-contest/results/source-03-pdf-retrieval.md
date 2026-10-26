# 财报 PDF 全文解析与证据页检索：实现与验证

赛题编号：`source-03-pdf-retrieval`。产品仓库：<https://github.com/suye-17/powercontext>。实现分支：`feat/source-03-pdf-retrieval`。

赛题冻结产品提交为 `9db1753434162c0aa7c9f1189a1722e617880a52`；开发分支基于 `origin/contest/base` 的 `afeac9d0551d439560a36169dba5b3c44991f878`，二者之间的增量是赛题工作流及辅助文件。工作区位于 `/home/duan/work/wt-contest/source-03-pdf-retrieval`。

## 实现

`src/powercontext/sources/pdf_pages.py` 提供题目规定的 `PDFDocumentInput`、`PDFPageInput`、`parse_pdf_document`、`PDF_PAGE_SOURCE_DEFINITION` 和 `search_pdf_pages`。

解析器使用与公开评测一致的 `pypdf==6.19.0`，计算完整原 PDF 字节的 SHA-256，按物理页顺序提取所有页面，保留空白页。对加密文件只尝试空密码；需要用户密码的文件及无法完整解析的文件以 `ValueError` 失败。解析前显式验证页面 Contents 单流或流数组的结构，避免 pypdf 把非法成员静默变为空白文本。

页 Source 为 `pdf-page`、版本 `1`、captured materialization。身份由原始 UTF-8 文档 ID、完整文档摘要和物理页码经过有边界的字节编码与 SHA-256 生成，长度固定为 80 字符。不同 PDF 修订拥有不同页身份，旧 Source 仍可精确回读。标准文本投影的正文及三个 metadata 字段完全遵循赛题。

检索通过 scoped Memory 的公开 search/get 操作完成：原始问题加最多三个通用词项组执行 FTS，使用确定性 RRF 合并候选；内部候选上限为 `min(100, max(20, 5 * limit))`，最多四次搜索、100 次精确 get。只返回恰好关联一个 `pdf-page` Source 的 Memory citation，按页身份去重，最终结果数不超过调用方的 `limit`。

## 验证环境与命令

公开评测 checkout：`/tmp/powercontext-public-eval-2026-competition`，提交 `dab240063bfcfa7917898dc44da4baadb3747bd0`。实际使用该 checkout 已安装的锁定依赖环境，Python 3.12.3、pypdf 6.19.0。验收在本机执行，未执行官方裁判容器或其资源配额。

```bash
POWERCONTEXT_CANDIDATE=/home/duan/work/wt-contest/source-03-pdf-retrieval
POWERCONTEXT_PUBLIC=/tmp/powercontext-public-eval-2026-competition/competition
PYTHONPATH="$POWERCONTEXT_CANDIDATE/src:$POWERCONTEXT_PUBLIC" \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
"$POWERCONTEXT_PUBLIC/.venv/bin/python" -m pytest -q -s \
  -c "$POWERCONTEXT_PUBLIC/pytest.ini" --confcutdir="$POWERCONTEXT_PUBLIC" \
  "$POWERCONTEXT_PUBLIC/challenges/source-03-pdf-retrieval/acceptance"
```

其余已执行检查：

```bash
uv run --group dev pytest tests/test_pdf_pages.py tests/e2e/test_pdf_page_sources.py tests/test_sources.py tests/test_source_observations.py -q
uv run --group dev prek run -a
uv run --group dev ty check
uv lock --check
```

## 分项结果

| 项目 | 结果与证据 |
| --- | --- |
| 全页解析、真实入库与物理页数 | 公开验收通过，完整解析并持久化 503 页 |
| 公开问题 citation | 五问分别返回 20 条不同页的 citation，逐条 Memory/Source 精确回查通过 |
| 公开验收 | 最终运行 `6 passed in 63.79s`；完整输出见[公开验收日志](source-03-pdf-retrieval-public.txt) |
| 自有与相关 Source 回归 | `35 passed in 4.87s`，包括标量/数组损坏页原子失败、合法流数组、空白页、空密码权限、摘要、Unicode/长 ID、修订身份、来源投影、候选预算、严格单页引用、重启和幂等 |
| 类型、格式和锁文件 | `ty check`、全部 `prek` hooks、`uv lock --check` 通过 |
| 独立审查 | subagent 按 `requesting-code-review` 和仓库 `REVIEW.md` 完成审查并执行公开验收；已处理损坏 Contents 数组的拒绝问题，新增失败到通过的回归覆盖 |
| 全量测试 | 未运行；按用户要求由用户手动执行 |

公开问题的返回数：

| 问题 ID | citation 数量 |
| --- | ---: |
| financebench_id_01009 | 20 |
| financebench_id_00735 | 20 |
| financebench_id_01328 | 20 |
| financebench_id_03620 | 20 |
| financebench_id_04481 | 20 |

解析预检还记录了原始公开 PDF 的以下诊断。词项按题目定义的 `[a-z]+|\d+` 计算；这些数值是本实现的提取结果，不是官方 gold。

| 诊断 | 数值 |
| --- | --- |
| 物理页码 | 1 至 503 连续 |
| 空白页位置 | 无 |
| 提取词项总数 | 191777 |
| 单页词项数范围 | 20 至 755 |
| 最低词项页 | 217 页：20；241 页：21；172、213 页：23；433 页：24 |
| 单页提取文本最大 UTF-8 长度 | 4934 字节 |

## 官方质量门槛状态

公开测试没有分发质量 gold。下列门槛均未获得官方结果，公开行为验收通过不代表它们已达标。

| 门槛 | 状态 |
| --- | --- |
| 每页词项数在冻结值的 90%–110% 之间 | 未验证 |
| 标注证据词项召回率 ≥ 0.95 | 未验证 |
| 标注整页词项 F1 ≥ 0.95 | 未验证 |
| 公开集 evidence-page Recall@20 ≥ 0.70 | 未验证 |
| 留出集 evidence-page Recall@20 ≥ 0.70 | 未验证 |

## 设计约定与验证边界

- 页身份使用规范字节编码后的 SHA-256，提供实际抗碰撞性；定长身份无法对无限长度输入提供数学上的绝对无碰撞保证。
- pypdf 版本与当前公开依赖清单精确一致；评测清单变更后需重新验证兼容性。
- 检索只依赖两个公开 Memory 操作；其他请求/响应形态的 facade 需要适配。
- RRF 平分时纳入完整 Memory 身份；多 Source 条目被排除，以保证一个 citation 对应一个可确认的页。若固定 pipeline 改为合并多页，需相应重新定义页引用策略。
- 本地端到端 fixture 与公开 `EvidencePipeline` 的提取语义一致；官方公开验收使用原始公开 fixture，验证真实 Source→Memory 路径；公开 fixture 变更时需同步适配本地链路验证。
- 全量测试由用户手动运行；现有证据覆盖本题及直接相关 Source 行为，未排除其他全量用例的失败。
- 官方 gold 与留出集缺少可用评测结果，因此不宣称获得 50 分或通过所有评分门槛。

如需手动运行全量测试，在隔离工作区执行：

```bash
cd /home/duan/work/wt-contest/source-03-pdf-retrieval
make test
```

分支和 worktree 保留于本地；未推送、创建 PR 或对外提交。
