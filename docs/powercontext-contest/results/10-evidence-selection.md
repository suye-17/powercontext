# 赛题 10：实现与验证证据

**状态：赛题尚未完成。请求/Client 契约与纯 Builder 选择器已实现；独立开发质量关卡未通过，Memory-only Runtime 集成与真实 SQLite 历史引用验收未开始。**

## 工作区与提交

- 仓库：<https://github.com/suye-17/powercontext>。
- 分支：`feat/evidence-selection-10`；工作区：`/home/enovo/work/wt-contest/powercontext-contest-10`。
- 冻结产品提交：`9db1753434162c0aa7c9f1189a1722e617880a52`。
- 分支起点：`afeac9d0551d439560a36169dba5b3c44991f878`，从 `contest/base` 建立。本分支用于远端保存未完成的题 10 开发实现，精确提交 SHA 以本分支 Git 历史为准；不构成正式赛题提交，也不创建 PR。
- 原工作区的 `AGENTS.md` 与 `docs/powercontext-contest/copy.md` 改动保留；赛题参考正文未修改。

本分支仅包含源码、测试、benchmark 工具及本页结果。下文 `.artifacts/`、`.superpowers/` 和 `docs/superpowers/` 为本地忽略材料，数据下载、计划及未采用原型归档不随分支提交；关键结论已汇总在本页。

## 已实现行为与边界

- 严格布尔 `PrepareContextRequest.evidence_selection=False`；与 assembly/include_code 的冲突请求明确拒绝。
- Python Client 默认和显式 False 均省略新增字段，仅 True 发送。
- `PreparedContextBuild.fragments` 返回带 Scope/revision/entry/version 的不可变原文切片，与 origins、JSON items 保持同序、同数和精确身份。
- 按原始坐标生成分句和长句窗口，有限候选池与单遍 beam 选择；合并重叠/相邻区间及能节省字节的短间隙，保留最佳可容纳单窗口。
- 使用文档 BM25 先验、跨 entry 的全局加权词频饱和收益，避免每个来源重复获得整份查询收益。参数仅在固定 train 上选择。
- 真实 JSON、trust policy、引用及 envelope 统一按 UTF-8 计费；budget probe 复用选择器，区分相关性筛除与预算遗漏。
- OpenAPI 更新后通过生成器产生 HTTP 代码，原 HTTP 响应四字段结构不变。

当前功能限于 Builder 和契约阶段。Memory-only Runtime families 选择、真实 Client→HTTP→SQLite 历史 citation 回查、双语使用指南及最终提交均未完成，不宣称请求标志已经完成端到端集成。

## 数据与质量证据

数据为 SciFact 官方公开 train/dev/corpus；来源 README commit `68b98a56d93e0f9da0d2aab4e6c3294699a0f72e`，archive SHA-256 `11c621288d41ac144d29b13b0f8503b3820b7d6e8b1f6ff24dff335c196d76be`。署名和 CC BY 4.0 / ODC-By 1.0 说明见 `benchmark/evidence_selection/README.md`。数据与标签仅存在忽略的 `.artifacts/evidence-selection/`；产品只接收 query、原文候选与身份、budget。

每条请求含指定 gold 原始摘要及两篇固定 BM25 非 gold 原始摘要。外部 oracle 检查完整 gold 区间加引用和包装在 1400/1800 bytes 下均能容纳，不删减 gold 区间，也不按算法成绩筛样本。

固定 train 为 257 claims/514 请求。补充验证从未进入原实验的多文档 evidence claims 构造；任意官方 gold 文档与原案例 gold 重合即整条排除，查询去重后固定选择 doc_id 字符串字典序首个 gold，负例排除该 claim 的所有官方 gold。完整规则排除 9 条文档重合和 6 条预算不可行 claim，保留 32 claims/64 请求。清单在评分修改前冻结，参数及源码在首次补充验证前冻结。补充文本仍可能曾作为未标注负例出现，不构成全新语料或官方测试集。

| 当前选择器数据 | Claims | 请求 | 平均 token recall | 完整覆盖比例 |
| --- | ---: | ---: | ---: | ---: |
| train（参数选择） | 257 | 514 | 0.663240 | 0.540856 |
| 补充验证（首次独立运行） | 32 | 64 | 0.281492 | 0.187500 |

| 补充验证预算 | 请求 | 平均 token recall | 完整覆盖比例 |
| --- | ---: | ---: | ---: |
| 1400 bytes | 32 | 0.232931 | 0.125000 |
| 1800 bytes | 32 | 0.330053 | 0.250000 |

质量关卡要求 recall≥0.65 且完整覆盖≥0.40，两项均未达到。64 个请求只有 28 个选择了指定 gold 文档，12 个完整覆盖。该失败结果保留，不以它继续调参后宣称独立验证；目前证据不能证明调整跨 entry 收益足以解决质量问题。

固定参数：全局 weighted TF；k1=10、b=0、tf_cap=8、document_prior_base=0、每 entry 窗口上限 32、beam_width=32。train 上略高分的 k1=10/cap=16 未通过独特证据回归，未采用。

- 当前选择器 SHA-256：`775dd29d0c290dedcba319b4a8cb1c79a46650c1f09ad9895349875c1da9b047`。
- train 案例 SHA-256：`2928d4d3cb97c402a6a9cd4f75a62c55ca068a64428d45107dff9a75018ef8d3`。
- 补充案例 SHA-256：`f59d1a8466719e24544f38e0a69111ad544039ced85093ce3e0f4a2f5ed94ebd`。
- 冻结记录：`.artifacts/evidence-selection/global-parameter-freeze.json`；当前结果：`global-train.json`、`global-supplementary.json`。

另保留原选择器首次单文档 dev 验证证据：148 claims/296 请求，recall=0.6231874067851108、complete=125/296，recall 关卡失败；独立 subagent 曾逐项复算确认。这不是当前选择器的测量，不与补充集直接比较效果；相关原始报告为 `validation.json`，原冻结记录为 `parameter-freeze.json`。

## 优化前后同集复现对照

优化前为按 entry 累加收益、k1=3/b=0/cap=8 的实现；当前为跨 entry 全局收益、k1=10/b=0/cap=8。旧源码从本任务完整源文件读取记录恢复到隔离快照，SHA-256 与当时冻结值 `a376bb66ddc0ac37c2590a2c10aaac5df9062f703023f81c38b6d48455b0ec1d` 完全一致，未根据结果近似重写。当前源码仍为 `775dd29d0c290dedcba319b4a8cb1c79a46650c1f09ad9895349875c1da9b047`。

两个版本均重新执行固定 train 514 请求、原 validation 296 请求、supplementary 64 请求，共 1748 次 Builder 调用；相同案例 hash、原文、候选顺序、身份、预算和 scorer。旧版仅在独立进程加载快照，不覆盖产品源文件。无调参或样本变化。这是目标与参数整体变更的对照，不能单独归因于某一项。

旧版原 train/validation 重跑结果与历史报告逐请求一致；当前三个集合重跑结果也与已有记录一致。对齐核对 recall、complete、content/body/format bytes、fragment 数量和 selected entries，差异数均为 0；计时不作为复现一致性字段。

| 数据与指标 | 优化前 | 当前 | 变化（百分点） |
| --- | ---: | ---: | ---: |
| train，514 请求，recall | 65.4529% | 66.3240% | +0.8711 |
| train，514 请求，完整覆盖 | 49.8054% | 54.0856% | +4.2802 |
| 原验证，296 请求，recall | 62.3187% | 64.0626% | +1.7439 |
| 原验证，296 请求，完整覆盖 | 42.2297% | 46.6216% | +4.3919 |
| 补充验证，64 请求，recall | 28.0397% | 28.1492% | +0.1095 |
| 补充验证，64 请求，完整覆盖 | 17.1875% | 18.7500% | +1.5625 |

按数据集汇总，两个质量指标均改善；逐请求和部分预算档仍有退化：

- train recall：56 个上升、34 个下降、424 个不变；完整覆盖新增 36 个、丢失 14 个。
- 原验证 recall：31 个上升、21 个下降、244 个不变；完整覆盖新增 19 个、丢失 6 个。
- 补充验证 recall：5 个上升、5 个下降、54 个不变；完整覆盖新增 2 个、丢失 1 个。
- 补充验证 1800-byte 档 recall 从 0.3303961427402924 降至 0.33005311864787096，下降 0.0343 个百分点；同档完整覆盖从 21.875% 升至 25%。1400-byte recall 从 0.2303981190460662 升至 0.23293086256241627，完整覆盖均为 12.5%。

原验证两档总体均改善：1400-byte recall 为 0.577424→0.597491、完整覆盖 0.358108→0.398649；1800-byte recall 为 0.668951→0.683761、完整覆盖 0.486486→0.533784。原验证平均 recall 仍未达到 0.65，补充集两项仍未达标；重跑不产生新的独立验证证据。

补充集优化前同样约 28% recall，低分在全局目标修改前已经存在。原验证和补充集构造不同，不能将跨集合的分数差归因于算法修改。最初原型 train 指标另为 recall=0.486228、完整覆盖=0.305447，保存在 `train-baseline.json`，不作为本轮优化前的版本定义。

完整对照：`.artifacts/evidence-selection/reproduction-comparison.json`。两版原始重跑报告为 `reproduction-before-{train,validation,supplementary}.json` 和 `reproduction-current-{train,validation,supplementary}.json`；历史报告保持原样。旧版快照和复现脚本位于 `.superpowers/sdd/2026-10-25-10-evidence-selection-quality/`，均被 Git 忽略。

复现旧版的命令（独立进程加载已验证快照，运行三个集合）：

```bash
PYTHONPATH=. .venv/bin/python .superpowers/sdd/2026-10-25-10-evidence-selection-quality/reproduce-before.py
```

当前版使用 `benchmark.evidence_selection.evaluate` 对同三个案例文件运行。该比较仅衡量已固定公开开发工作负载，不能证明正式成绩或统计显著性，未改变质量关卡。

## 句子与上下文评分实验（未采用）

2026-10-25 至 2026-10-26 按批准方案实现并评测22种训练配置（含基线）。最高 Recall 原型为0.671387，对比基线0.663240，但完整覆盖从278/514=0.540856下降到258/514=0.501946，两预算完整覆盖均下降。按预先冻结的规则没有合格配置，三折均回退基线，未运行本轮新 dev/supp，也没有继续扩大调参。

原型 gold 文档命中408→448次，条件 Recall却从0.835553降至0.770297；平均来源1.3405→1.7160，格式765.92→839.33 bytes，正文761.33→714.43 bytes。这些是相关观察，尚未单独识别因果效应。

独立 subagent 复核准入、部分窗口公式与关键行为，确认不能默认采纳。原型源码、测试、hash和全部报告完整归档到 `.superpowers/sdd/2026-10-25-10-sentence-evidence/rejected-prototype/`；生产选择器恢复原SHA `775dd29d0c290dedcba319b4a8cb1c79a46650c1f09ad9895349875c1da9b047`，没有保留双算法。恢复后132项定向测试通过；隔离原型147项通过；49项契约通过；恢复后的514个训练请求与基线逐项一致。句子评分实验阶段未运行全量测试或创建提交。

完整实验、成本、局限和复现命令见 `docs/superpowers/reviews/2026-10-25-10-sentence-scoring-review.md`。主赛题状态和独立质量关卡不变。

## 低召回根因诊断

详细数据、受控诊断与代码分析见 `docs/superpowers/reviews/2026-10-25-10-recall-root-cause.md`。保持源码和参数不变，结果位于 `.artifacts/evidence-selection/quality-root-cause.json` 等只读诊断输出。

- 原验证 65/296、补充 36/64 次完全未选 gold；保持三篇文档原评分/费用、仅允许 gold 窗口参与的标签辅助诊断，recall 为 0.856683/0.755202。该实验同时改变文档和搜索竞争，不能将全部提升精确归因于某个先验。
- 当前保留 gold 窗口的预算可行组合，标签最大平均 recall 为 train 0.978908、原验证 0.990603、补充 0.974980；完整组合存在于 492/514、284/296、60/64 次请求。多数失败发生在已有证据的最终取舍阶段。
- 对无法完整覆盖的请求逐阶段枚举：train 为 20 次零得分过滤、2 次窗口粒度；原验证为 11 次过滤、1 次粒度；补充为 4 次过滤。本批完整覆盖可行性没有观察到 top32 引入损失，不等于它对全部召回指标绝对无损。
- 6 个有界小失败案例经三文档精确穷举，beam 的完整排名和最终状态已是当前目标最优，低 evidence 质量仍存在；不能由该样本排除其他复杂案例的搜索损失。
- 自建负例是全 corpus 中 BM25 最高的非 gold，补充集又固定从多 gold 取字典序首篇；其 gold 字面匹配更低、证据更长、文档先验更常排第三。该工作负载与官方 24 claims 的代表性未知。

独立 subagent 复算补充集全部 gold-only/窗口枚举和六个精确案例，与报告一致；其余主代理全量统计的复核范围在详细报告中列明。未改算法、未调参、未改变关卡。

## 独立审查与失败分层

独立 subagent 重新调用全部 64 次 Builder，并自行实现字符集合并集与词频计分，逐请求与结果文件一致；独立重建补充清单并枚举序列化 gold 费用，未发现协议或指标错误。审查记录见 `docs/superpowers/reviews/2026-10-25-10-quality-gate-review.md`。

已证明至少存在两类限制：claim 275 的证据窗口能装入预算，但与 query 无共同词而被得分过滤；claim 955 的全部完整覆盖窗口已保留，评分却明显偏向非 gold 文本。补充集 canonical 文档可能只支持复合 claim 的一部分，不能由它推断官方难度，也不因此取消当前关卡。

冻结结果的只读分层显示：12 个请求完整覆盖，4 个请求在正得分过滤后丧失覆盖可能，48 个请求的保留窗口并集能覆盖 gold 但实际结果不完整。并集覆盖未考虑窗口同时装入的费用，不能把后者全部归因于评分或 beam。尚无证据支持只扩候选池或只调 beam 就能达标。

## 定向检查与限制

- Python 3.12.3，工作区锁定依赖；组织方专项固定环境尚未取得。
- Builder 新旧行为、Client、指标和补充构造测试：**127 passed**。
- `make contract-test`：**49 passed**，包含 API/JS 生成一致性与契约检查。
- 变更文件定向 ruff、format、ty 与 `git diff --check` 通过。
- 原 train/validation 重新构造与原冻结文件逐字节一致；已有 subagent 独立核验 405 个原案例的 gold 最小序列化费用。
- 官方题 10 acceptance 未取得；已检查 upstream `2026-competition` 与 `2026-competition-ecosystem` 的固定树，未找到专项验收目录，用户也没有可提供的路径。不能宣称正式通过或得分。
- 全量测试和全量检查未运行，按用户要求由用户手动执行。

本地复现（复现相同案例不产生新的独立证据）：

```bash
uv run python -m pytest -q tests/builtin/runtime/test_evidence_selection.py tests/builtin/runtime/test_prepared_context.py tests/test_evidence_selection_metrics.py tests/test_client.py
make contract-test
uv run python -m benchmark.evidence_selection.evaluate --cases .artifacts/evidence-selection/supplementary/supplementary.jsonl --output .artifacts/evidence-selection/supplementary-reproduction.json
uv run python -m benchmark.evidence_selection.measure --cases .artifacts/evidence-selection/cases/train.jsonl --output .artifacts/evidence-selection/costs-reproduction.json --budget 1800 --repeats 3
```

## 成本证据

无在线选择服务或模型调用。每场景预热一次、计时三次；p95 为三次最大值，tracemalloc 另测一次。未控制后台负载，不能外推生产 SLA。

| 场景 | 输入 UTF-8 bytes | 生成/保留窗口 | 扩展含 omission 探测 | p50 秒 | p95 秒 | 内存峰值 MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| three_abstracts | 4292 | 90/68 | 2006 | 0.0219 | 0.0220 | 0.85 |
| sixteen_near_limit | 131072 | 8736/512 | 16760 | 0.2750 | 0.2758 | 8.92 |
| dense_short_sentences | 131072 | 39312/512 | 16767 | 0.5040 | 0.5045 | 10.17 |
| direct_long_tail | 32017 | 9954/11 | 95 | 0.3061 | 0.3067 | 4.09 |

公开脚本 `benchmark/evidence_selection/measure.py` 记录输入模板/hash、query、budget、环境、全部计时和源码 hash；原始结果 `.artifacts/evidence-selection/global-costs.json`。每条 8192-byte 持久化正文边界与 Builder 直调长输入分开测量，不宣称相对原前缀组装有性能改进。

## 完成条件

已批准计划规定，独立质量失败时保持集成关卡。因此当前不进入 Runtime 集成。后续评分或验证协议若有实质变更，应先形成有证据支持的方案并获得批准；不能降低门槛、删失败样本或把已使用验证集继续称为独立集。本次按用户要求保存当前开发分支；后续完成质量、端到端 citation、自查及独立 review 后才能作为完成成果交付。

用户需要全量检查时，在本工作区手动执行 `make check`、`make test`；跨版本与完整文档验证分别为 `tox`、`make docs-test`。

AI 使用：Codex 辅助实现、测试和文档；独立 subagent 审核源码、数据与验证结果。
