# PaperDesk 研读

一个面向科研阅读者的论文笔记工作台：用 **seekdb** 检索英文论文笔记，用 **PowerContext** 记住阅读目标、偏好、进度和会话摘要，让下一次打开工作台时能直接接着上次读。

## 解决的痛点

阅读论文笔记时，信息往往散落在多个 Markdown 文件里：

- 想找「上次读到哪、下一步做什么」，只能重新翻文件；
- 每次做阅读计划都要重新翻库，计划前后不连续；
- 摘要长度、语言、是否要出处等偏好，每次都要重复说明；
- 普通检索只返回片段，纯生成又容易脱离原文。

PaperDesk 把「资料检索」和「阅读记忆」分开：seekdb 负责找到证据，PowerContext 负责记住人读到哪里、想读什么，两边在计划、速读和综述里汇合。

## 核心功能

| 功能 | 说明 |
| --- | --- |
| 资料导入 | 解析 Markdown front matter、标题、作者、年份、标签、阅读状态与章节，写入嵌入式 seekdb |
| 混合检索 | FULLTEXT + 标识符加权 + 标签/年份/状态过滤；配置嵌入后与 HNSW 向量排名做 RRF 融合 |
| 阅读计划 | 结合目标标签、记忆中的进度和检索结果排序，给出下一篇与下一节 |
| 继续上次 | 启动时读取目标、偏好、进度和会话摘要，生成继续卡片 |
| AI 综述 | 检索分块交给可配置的 OpenAI 兼容模型，生成带 `[C1]` 引用的中文综述；引用不合法或调用失败时回退为抽取式摘要 |
| 会话写回 | 结束阅读会话时修订进度并保存问题与下一步，重启后仍可复用 |
| 可核对性 | 检索调试页显示 FTS / 向量得分与融合排名；记忆页显示 kind、版本和内容；评测输出 recall@k 与 MRR |

## 技术分工

```text
Markdown 笔记
   │ 解析
   ▼
seekdb（嵌入式，pylibseekdb）
   ├─ pd_documents：标题、作者、年份、标签、状态、原文
   └─ pd_chunks：章节分块 + FULLTEXT + 结构化索引 + VECTOR(1536) + HNSW
   │ 检索结果（章节、文件、行号、得分）
   ▼
PaperDesk 服务层
   ├─ 阅读计划 / 继续卡片
   ├─ 单篇速读 / 带引用的 AI 综述
   └─ 检索调试与评测
   │ 目标、偏好、进度、会话摘要（原子记忆 artifact，ETag 修订）
   ▼
PowerContext 1.4.0（本地 HTTP 服务）
```

- **seekdb 是权威资料与检索层**：论文元数据、章节分块、全文索引、向量索引和过滤条件都在这里；检索结果直接决定计划顺序、速读内容和综述引用。
- **PowerContext 是跨会话记忆层**：只保存阅读上下文，不保存论文正文；它提供 scope、原子记忆、版本修订和 hybrid 语义召回。重启服务后再次读取，进度与摘要会改变计划与继续卡片。
- 两边职责不重叠：删掉 seekdb 就没有可引用的证据，删掉 PowerContext 就没有「上次读到哪」。

## 快速开始

完整步骤见 [RUNBOOK.md](RUNBOOK.md)。最短路径：

```bash
# 1. 安装（需要 Python >= 3.11 与 uv）
cd qoder/app
uv sync --extra dev --extra server

# 2. 配置模型（可省略；省略后使用全文检索与抽取式摘要）
cd ..
cp .env.example .env
# 编辑 .env，填入 MINIMAX_API_KEY / MINIMAX_BASE_URL / MINIMAX_MODEL

# 3. 从空状态运行完整验证样例
scripts/run_scenario.sh

# 4. 打开工作台
# http://127.0.0.1:8080/
```

`scripts/run_scenario.sh` 会清空 `qoder/.runtime/`，启动 PowerContext，导入 6 篇样例笔记，写入记忆，重启 PowerContext 验证复用，运行评测，最后启动 Web 工作台。验证步骤、期望结果和证据位置见 [sample/scenario/steps.md](sample/scenario/steps.md)。

## 目录

```text
qoder/
├── app/                 # FastAPI + Jinja2 应用、CLI、测试与 uv.lock
├── sample/
│   ├── library/         # 6 篇英文论文笔记（30 个章节分块）
│   ├── eval/            # 10 条检索评测查询
│   ├── expected/        # 机器可读的期望结果
│   └── scenario/        # 重置脚本与 seekdb 查询样例
├── scripts/             # 启动/停止 PowerContext、一键场景
├── evidence/            # 实际运行记录：seekdb、PowerContext、模型、评测、Web
├── site/                # Qoder Sites 静态站点
├── .env.example         # 配置占位符，无密钥
└── RUNBOOK.md           # 安装、运行、重置与排错
```

## 验证

- 应用测试：`cd qoder/app && .venv/bin/python -m pytest -q`，57 项通过（解析、存储、检索、记忆、模型校验、评测、服务层）。
- 场景验证：`scripts/run_scenario.sh`，断言导入数量、过滤结果、记忆版本、重启一致性、引用映射、评测阈值与 8 个 Web 页面状态。
- 实际运行记录：`qoder/evidence/`；技术参与方式与证据对照见 [evidence/README.md](evidence/README.md)。

检索评测参考值（固定语料与查询集，MiniMax `embo-01`）：

| 模式 | recall@1 | recall@3 | recall@5 | MRR |
| --- | --- | --- | --- | --- |
| 关键词基线 | 0.600 | 0.900 | 1.000 | 0.753 |
| 混合检索 | 0.600 | 0.900 | 1.000 | 0.753 |

两种模式在此语料上的头部命中一致：关键词通道保护精确术语，向量通道补足改写后的语义候选。脚本同时断言 `embedding` 开关（关键词为 false、混合为 true）以及 `recall@5 >= 0.9`、`MRR >= 0.6`。

## 赛题验收对照

| 验收项 | 落点 | 证据 |
| --- | --- | --- |
| 网站可访问 | `site/` 静态站点，待由发布者使用个人 Qoder 账号发布为公开站点 | 发布步骤见 [site/PUBLISH.md](site/PUBLISH.md)；URL 与无痕截图由发布者补充 |
| 项目能够运行 | `uv.lock`、`.env.example`、`RUNBOOK.md`、一键场景脚本 | `sample/scenario/steps.md` 与 `evidence/` |
| Qoder 开发可核对 | 需求拆解、实现、调试与验证均在 Qoder 中完成 | [evidence/qoder/README.md](evidence/qoder/README.md) 记录对话材料、UID 与 commit 对应关系 |
| 技术结合有效 | seekdb 检索进入计划/速读/综述；PowerContext 记忆跨重启复用 | `evidence/seekdb/`、`evidence/powercontext/`、`evidence/llm/` |

## 已知限制

- 样例语料是英文论文笔记，全文索引使用空格分词；中文查询主要依靠向量通道。未接入中文分词器。
- 嵌入与生成依赖外部模型服务。密钥缺失或服务不可用时，应用仍可启动，检索退化为全文关键词，综述退化为抽取式摘要；但 `scripts/run_scenario.sh` 会断言真实嵌入、LLM 综述与 hybrid 记忆召回，因此完整场景需要可用的 MiniMax 密钥。
- 向量列与 HNSW 索引在首次导入时创建；更换嵌入模型或维度需要清空运行时重新导入。
- 当前定位是单用户本地工作台，没有登录、多租户和并发写冲突以外的权限模型。
- PowerContext 的 hybrid 记忆检索在嵌入服务限流时会回退到 fts，客户端会在结果里标明 `fallback_reason`。
- 阅读偏好中的引用样式会持久化，但当前只渲染行内编号 `[C1]`；其他样式尚未实现。

## 数据与依赖

- 样例论文笔记为本项目编写，用于演示解析、检索与引用校验，不包含第三方受版权保护的全文。
- 依赖及版本见 `app/pyproject.toml` 与 `app/uv.lock`；PowerContext 固定为 `1.4.0`，seekdb 通过 `pylibseekdb` 嵌入运行。
- 仓库不包含任何密钥；`.env` 已被忽略，配置模板只含占位符。
