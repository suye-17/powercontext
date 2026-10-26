# Source-03 财报 PDF 检索实现计划

> **供代理执行：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，按任务逐项实施。步骤使用 checkbox（`- [x]`）跟踪。

**目标：** 完整解析财报 PDF 的所有物理页，将每页作为不可变的 `pdf-page` Source 保存，并为不同证据页检索真实、精确的 Memory citation。

**架构：** 在 `powercontext.sources.pdf_pages` 中实现赛题 API，沿用仓库的 Source Definition 和 `powercontext.text-evidence` 投影契约。使用 pypdf 从完整 PDF 字节中解析所有页面；以文档 ID、完整 PDF 的 SHA-256 和物理页码生成页身份。检索先建立原始问题的单次 FTS 基线，再执行有界候选 overfetch 与通用离线查询拆分，以确定性 RRF 合并候选；逐条通过 scoped Memory 的公开 get API 验证来源后去重，只返回真实 Memory citation，不增加平行索引或伪造引用。

**技术栈：** Python 3.11+、Pydantic、锁定版本的 pypdf、现有 Source Definition/observation API、scoped Runtime Memory API、SQLite 端到端测试。

**赛题依据：** `docs/powercontext-contest/04-财报PDF全文解析与证据页检索.md`，编号 `source-03-pdf-retrieval`，50 分。赛题冻结基线为 `9db1753434162c0aa7c9f1189a1722e617880a52`。开发分支基于 `origin/contest/base`（`afeac9d0`），其中包含冻结产品提交之后的赛题工作流文档；两者应分别记录，不得混淆。

## 全局约束

- 严格保留题目声明的公开 API 和类型：`PDFDocumentInput`、`PDFPageInput`、`parse_pdf_document`、`PDF_PAGE_SOURCE_DEFINITION`、异步函数 `search_pdf_pages(memory, question, limit=10)`。
- 输入必须是完整 PDF 字节；不得读取评测答案、gold、预解析文本、证据页清单或其他评测资产，也不得硬编码问题答案或文档结果。
- 按顺序解析每个物理页，包括空白页；物理页码从 1 开始。
- SHA-256 必须基于完整原始 PDF 字节，使用小写十六进制；计算前不得规范化或重编码 PDF。
- 页身份由 `(document_id, document_sha256, page_number)` 稳定决定，长度不超过 256 个字符。具体结构化字节编码见任务 1。PDF 修订后不得使旧引用失效。
- 每页都是 `pdf-page`、版本 `1` 的 captured Source；标准文本投影的 metadata 必须恰好包含题目要求的字段，正文格式为 `DOCUMENT_ID\nPAGE_TEXT`。
- 检索及精确引用只能使用公开 scoped Memory API。查询处理与重排必须是确定性的离线算法；不调用模型或在线服务。
- 保留已有改动；不修改生成的 HTTP API 文件。
- 本工作流不运行全量测试；如需全量测试，由用户手动执行。实现获批后运行针对性检查、早期解析预检以及可用的赛题公开验收；若验收资产不可用，应如实说明。

## 审查重点

- 空文件、非 PDF、截断文件或其他无法解析的输入必须以 `ValueError` 失败，不能返回部分页；分别覆盖这些输入类别。
- 无需用户提供密码、仅带空密码权限标记且可读取的 PDF 必须成功解析；用生成的加密 PDF fixture 验证。
- 空白页必须保留页序且文本为空字符串；用“有文本页、空白页、有文本页”的混合 PDF 验证。
- 长文档 ID、Unicode ID 或含分隔符的 ID 不得造成字段混淆，身份长度不得超过 256 字符；验证修订身份变化和边界编码。
- 多查询结果可能重复，Memory 命中也可能没有 PDF 来源、来源类型不符或关联多个 PDF 页；验证候选有界、来源精确、去重确定且最终页数不超过 `limit`。

---

### 任务 1：解析 PDF、定义页 Source 并尽早预检公开样例

**文件：**
- 新建：`src/powercontext/sources/pdf_pages.py`
- 修改：仅当声明的公开 API 需要包级导出时，修改 `src/powercontext/sources/__init__.py`；题目要求的导入路径本身是 `powercontext.sources.pdf_pages`。
- 测试：`tests/test_pdf_pages.py`；公共 PDF fixture：`tests/pdf_support.py`

**接口：**
- 提供 `PDFDocumentInput(document_id: str, content: bytes)` 和 `PDFPageInput(document_id: str, document_sha256: str, page_number: int, text: str)`。
- 提供 `parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]`。
- 提供名为 `pdf-page`、版本为 `1` 的 `PDF_PAGE_SOURCE_DEFINITION`，将每页解析为 captured Source，并可读回对应的 `PDFPageInput`。
- 提供标准文本投影；metadata 必须恰好包含 `document_id`、`document_sha256`、`page_number`，正文为 `document_id + "\n" + text`。
- 页 Source ID 的编码固定为：按字段顺序 `(document_id, document_sha256, page_number)`；`document_id` 使用原始 UTF-8 字节（不做 Unicode 规范化），前置其 8 字节无符号大端字节长度；`document_sha256` 解码为 32 个原始摘要字节；`page_number` 编为 8 字节无符号大端整数；整个 payload 前置域分隔符 `powercontext:pdf-page:v1\0`。对完整 payload 计算 SHA-256 小写十六进制，Source ID 使用 `pdf-page:sha256:<digest>` 格式。该编码定长、字段边界明确，并可容纳任意合理长度的文档 ID。

- [x] 编写行为测试，覆盖完整字节的 SHA-256、从 1 开始的物理页码、空白页、逐页文本、空或纯空白文档 ID、损坏输入的原子失败、空密码加密 PDF、重复调用身份稳定、PDF 修订后身份变化、含分隔符和 Unicode 的文档 ID、长 ID 及不超过 256 字符的身份。
- [x] 运行 `uv run pytest tests/test_pdf_pages.py -q`；预期：因 PDF 页 API 尚未实现而失败。
- [x] 使用锁定的 pypdf API 实现 PDF 模型、解析器、适配器、上述规范字节编码、投影和 Definition；解析失败统一转为 `ValueError`，不得返回部分结果。
- [x] 再运行 `uv run pytest tests/test_pdf_pages.py -q`；预期：通过。
- [x] 若官方公开 PepsiCo PDF 已在评测 checkout 中提供，立即运行解析预检，再开始 Memory 检索实现：断言解析页数为 503 且页码连续；报告每页词项数、空白页位置、词项数最低页及解析失败原子性。使用题目相同的 `[a-z]+|\d+` 词项计数，检查空页与低词项页是否来自真实解析结果，不将该预检误报为质量 gold 通过。

预检使用实际找到的官方 PDF 文件路径执行，例如：

```bash
uv run python -c 'import re,sys; from pathlib import Path; from powercontext.sources.pdf_pages import PDFDocumentInput,parse_pdf_document; p=parse_pdf_document(PDFDocumentInput(document_id="pepsico-2022",content=Path(sys.argv[1]).read_bytes())); counts=[len(re.findall(r"[a-z]+|\d+",x.text.lower())) for x in p]; print({"pages":len(p),"page_numbers_contiguous":[x.page_number for x in p]==list(range(1,len(p)+1)),"blank_pages":[x.page_number for x in p if not x.text.strip()],"lowest_token_pages":sorted(enumerate(counts,1),key=lambda x:x[1])[:20]}); assert len(p)==503' /path/to/official/pepsico-2022.pdf
```

若官方 PDF 尚不可用，应记录预检未运行，并在取得资产后尽早补跑；正式公开 acceptance 仍在任务 3 执行。

### 任务 2：建立 FTS 基线并实现有界候选召回、重排与页级去重

**文件：**
- 修改：`src/powercontext/sources/pdf_pages.py`
- 测试：`tests/test_pdf_pages.py`

**接口：**
- 使用 scoped Memory 的公开方法 `search(SearchMemoryRequest(query=..., limit=..., mode="fts"))` 和 `get(GetMemoryEntryRequest(citation=...))`。
- 提供 `async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]`。
- 内部候选上限与最终页数上限分开：`candidate_limit = min(100, max(20, 5 * limit))`。每个 Memory search 调用最多请求 `candidate_limit` 条；每次 `search_pdf_pages` 最多执行 4 次 search，合并后最多保留 `candidate_limit` 个不同 Memory citation，最多执行同数目的精确 get 回读。对 `limit <= 0` 遵循现有 Memory API 的参数错误行为。

- [x] 先写测试，验证原始问题按原文执行一次 FTS 基线；断言 `limit=20` 时候选上限为 100，较小 `limit` 的 5 倍规则及 100 上限正确，每次 search 数不超过 4，候选 get 总数不超过 `candidate_limit`。
- [x] 添加多查询测试：由 `question.casefold()` 中按顺序提取唯一 `[a-z]+|\d+` 词项，将词项平衡拆成至多 3 个非空连续组；在原始问题 FTS 之外，以这些通用词组作为最多 3 个附加 FTS query，去除与已有 query 完全相同的变体。验证不访问问题答案、gold、文档内容之外的评测资产，也不包含问题/文档专用分支。
- [x] 添加确定性融合与去重测试：同一 citation 在多个 query 出现时合并名次；按 RRF `Σ 1 / (60 + rank)` 排序，平分时依次按最佳单查询名次、`entry_id`、`entry_version_id` 及完整 Memory 身份排序。对每个候选调用公开 `memory.get`，仅保留只有一个 SourceRef 且类型为 `pdf-page` 的条目，并按 `(source_type, source_id)` 去重，最多返回最终 `limit` 个页 citation。
- [x] 添加候选不足、重复命中密集、过滤后不足最终 `limit`、无 PDF 来源、非 PDF 来源、多 PDF 页来源、相同页跨 query 重复及 limit 边界测试。结果不足时返回已验证的较短 tuple，不伪造引用或突破候选预算。
- [x] 运行 `uv run pytest tests/test_pdf_pages.py -q`；预期：新增检索测试在实现前失败。
- [x] 实现单次原始问题 FTS 基线、通用分词与 query 组、4 次搜索预算、100 条候选硬上限、RRF 确定性合并、精确 get 验证和页身份去重。
- [x] 再运行 `uv run pytest tests/test_pdf_pages.py -q`；预期：候选预算、召回策略及来源回查测试全部通过。

### 任务 3：用赛题一致的 Source→Memory 流程验证持久化、重启、检索并分项报告

**文件：**
- 新建：`tests/e2e/test_pdf_page_sources.py`
- 修改：`pyproject.toml` 和 `uv.lock`，声明与公开评测一致的 `pypdf==6.19.0`。
- 验证报告：`docs/powercontext-contest/results/source-03-pdf-retrieval.md`。

**接口：**
- 使用 `manifest_for_definition`、`project_source_for_transport`、`SubmitSourceObservation`、`open_builtin_contexts`、赛题固定 Memory pipeline 的同一 fixture/config、正常 Memory 搜索/读取 API 和 `search_pdf_pages`。

- [x] 编写 SQLite Runtime 场景：解析多页 PDF，注册 Definition，按正常 Source observation 路径提交所有页面，重复提交验证幂等性，关闭并重开 Runtime；检查持久化后的每个 Source 与标准投影，包括空白页和修订身份。
- [x] 测试 Memory 路径时，复用与赛题固定 pipeline 一致的 fixture/config；不得写一个绕过既定处理流程、直接把测试文本伪装成 Memory 结果的 candidate pipeline。通过真实 Source→observation→Memory pipeline 提交数据，再在重启后搜索和 get 回读。
- [x] 断言检索 citation 经精确 Memory get 后，其 `pdf-page` SourceRef、Source 回读页码、完整 PDF 摘要和页面文本完全匹配；重复入库不增加 Source 或重复页面 citation。
- [x] 运行 `uv run pytest tests/e2e/test_pdf_page_sources.py -q`；预期：Source、projection、Memory persistence、重启和 citation 回查通过。若固定 pipeline fixture/config 不在本地仓库，应记录依赖并在公开验收资源取得后复用，不以自制流程冒充赛题链路。
- [x] 若公开验收 checkout 可用，运行题目指定的公开命令：

```bash
PYTHONPATH="$POWERCONTEXT_CANDIDATE/src:$POWERCONTEXT_PUBLIC" \
  python -m pytest -q -s -p no:cacheprovider \
  "$POWERCONTEXT_PUBLIC/challenges/source-03-pdf-retrieval/acceptance"
```

- [x] 形成分项验证报告，不把不同层次合并成一个“通过”结论：
  1. **全页解析/物理页数：** 官方 PDF 是否完整解析为 503 页，页码连续，空白页与最低词项页清单。
  2. **公开问题 citation：** 公开 acceptance 的逐题结果、命中 citation 数及精确 Source 回读结果。
  3. **自有可验证指标：** 本地 fixture 的页身份、投影、幂等、重启、候选/查询预算和页级去重结果。
  4. **官方 gold 门槛状态：** 每页词项数 90%–110%、证据页召回率至少 0.95、标注整页词项 F1 至少 0.95，公开集与留出集的 evidence-page Recall@20 各至少 0.70；无官方 gold 结果时逐项标记“未验证”。
  5. **留出集状态：** held-out Recall@20 至少 0.70；没有官方留出集 gold 结果时标记“未验证”，不得外推公开样例成绩。
- [x] 对受影响的 Python 代码运行 `uv run prek run -a` 和 `uv run ty check`；预期：检查通过。除非用户亲自运行，否则不运行 `make test`/`tox` 全量测试。

## 风险与依赖

- 公开验收套件、原始 PepsiCo PDF、固定 pipeline 与锁定依赖已在本地公开评测 checkout 中确认；解析预检、SQLite e2e 和公开验收的证据边界在验证报告中分别记录。
- 产品声明的 pypdf 版本与公开评测的 6.19.0 一致；更换解析器版本需要重新验证全文提取与持久化契约。
- 赛题允许离线查询处理与重排，但 Recall@20 是否达标仍需公开 acceptance 或官方 gold 评测证据；候选 overfetch、查询组和 RRF 不构成质量通过证明。
- Memory 条目可能关联多个 Source。为了返回可精确解释的页引用，检索只保留恰好关联一个 `pdf-page` SourceRef 的 citation；如果赛题固定 pipeline 会把多页合并到一个 Memory entry，需要先依据公开接口和验收行为更新策略与计划。
- 题面要求页身份“无碰撞”，同时只要求文档 ID 非空且将 Source ID 限制为 256 字符。上述定长 SHA-256 编码字段边界明确且具备实际抗碰撞性，但不能在数学意义上保证绝对无碰撞；若评测将其理解为必须绝对无碰撞，则需进一步明确文档 ID 长度约束。
- 50 分为全有或全无；身份、解析质量、持久化及公开/留出集 Recall@20 门槛必须全部满足。局部测试或公开问题通过都不足以单独支持满分结论。
