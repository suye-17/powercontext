# 第 9 题：来源撤回后的记忆停用与恢复

## 仓库与基线

- 产品仓库：<https://github.com/suye-17/powercontext>。
- 赛题编号：`memory-01-source-lifecycle`，20 分，必测组全部通过才得分。
- 冻结提交：`9db1753434162c0aa7c9f1189a1722e617880a52`。
- 分支起点：`contest/base`，`bfd62d03f131a1324ca9d90e8d58b9576447a39d`；其产品源码、测试和依赖与冻结提交一致。
- 实现分支：`feat/source-lifecycle-09`。
- 本地工作树：`/home/enovo/work/wt-contest/powercontext-contest-09`。
- 实现提交：`2e1dcf60400ab8b7c9976d6166ec25d53d3a27b3`，主题 `feat(memory): propagate source lifecycle availability`。

## 产品行为

`powercontext.builtin.runtime.source_lifecycle` 导出 `SourceLifecycleError(ValueError)` 与严格校验的
`SourceLifecycleEvent`。`open_builtin_runtime()` 返回的 Runtime 提供 `lifecycle.for_scope(scope_id)`，支持异步
`register_source`、`register_memory`、`register_topic`、`apply` 和 `resolve`。

每个 Scope 保存独立的显式依赖 DAG。Source 初始可用；派生节点注册时立即根据父节点当前状态和 all/any 策略求值。
注册引用须存在且能在该 Scope 读取，相同绑定幂等，变更引用、父节点、策略或生命周期版本则冲突。
父节点必须先注册，不允许重复父节点、自环或通过重新绑定形成环。

事件只作用于 Source，生命周期 revision 必须精确匹配。每个 Source 独立记录最近接受的完整事件；
低 sequence、同 sequence 不同内容均拒绝。相同事件重放返回当前全部可用节点的排序列表，不新增 Memory 修订。
来源撤回与恢复按拓扑顺序传播到所有后代，不根据正文猜测依赖，不改写摘要。

Memory 通过原生 `forget()` / `reactivate()` 改变状态，绑定的 entry version 与正文保持不变。
Topic 使用独立的精确引用资格投影，各原生全文/向量通道在候选截断前排除不可用引用。
原生当前列表、搜索和 Context 使用传播后的资格；未注册内容保持既有行为。
多个节点绑定同一个原生目标时，仍有任一可用绑定支持该目标，它就继续参与原生检索。

`resolve(..., purpose="generation")` 拒绝不可用节点，`purpose="audit"` 允许读取其原始引用。
Topic 的 Head、发布时间、完整正文、chunk 和向量保留；精确历史读取不受检索资格限制。
恢复后返回注册时的原始 SourceRef、MemoryCitation 或 ArtifactRef，不能替换为当前 Memory Head 的新 citation。

图、事件、Memory 状态修订和 Topic 资格在同一数据库事务提交。原生写入失败或操作取消会整体回滚；
重试同一事件可完成传播，提交后丢失响应也可安全重放。事务内临时 MemoryService 的缓存不进入后续操作。

## 约束与兼容性

- 每个 Scope 为单写者，注册后不改图；绑定期间不从外部修订 Memory 正文版本。
- 生命周期状态使用版本化 JSON，不对 node_id 增加 SQL 主键长度限制；未知持久化 schema 明确拒绝。
- 初始化新增两张表；旧库没有生命周期记录时，既有内容保持可用，无需改写原正文或历史证据。
- 中英文 OBLOADER 恢复清单在第 2 层包含两张生命周期表，先恢复 Scope 与 Artifact 父表；源导出缺表时可省略对应表。
- 图状态每次只读取/保存目标 Scope。拓扑计算与状态序列化为 O(V+E)，状态文档存在同 Scope 重写成本。
- 原生 Memory 状态转换仍承担现有 manifest/检索投影成本。没有性能提升或大图成本保证；测试覆盖 20 节点图。
- 不新增依赖、不修改 OpenAPI 或生成客户端，不新增 HTTP/MCP 操作。既有 HTTP/MCP 读取复用原生检索行为。
- SQLite 已实际执行 FTS 与 sqlite-vec 测试。OceanBase/seekdb 的适配路径已调整，但 SQL 编译/捕获不能替代真实服务验收。

## 验证证据

测试环境为本地 Python 3.13 与仓库锁定依赖，未调用在线生成模型；向量场景使用固定离线 Embedding。

| 范围 | 结果 |
| --- | --- |
| 用户运行的实现前基线 | 87 passed, 26 skipped |
| 用户运行的事件模型/注册/Runtime 首阶段测试 | 51 passed |
| 传播、事务失败、取消与重试专项 | 60 passed |
| 原生 Topic/Context、重启与各通道 candidate_limit=1 红绿检查 | 红灯 5 failed；实现后 5 passed |
| 生命周期、Topic 持久化和 OceanBase 查询专项 | 115 passed, 1 skipped |
| 受影响 Runtime、Context、HTTP Topic、Memory 与持久化回归 | 201 passed, 27 skipped |
| `uv run --locked ty check` | 通过 |
| 改动文件 Ruff 检查 | 通过 |
| 用户运行的全量 `make test` | 1 failed, 3718 passed, 138 skipped, 6 warnings；尚无全量全绿结果 |
| 双语恢复清单与 MySQL schema 专项 | 5 passed；覆盖全部 48 张表及外键父表优先顺序 |
| 仓库 `make check` | 通过；含 hooks、锁文件、类型检查和 30 项 integration manifest 测试 |
| 独立 subagent 审查 | 实现提交审查无阻塞问题；独立专项复跑 115 passed, 1 skipped |
| 官方公开验收 | 未取得测评文件，未运行 |

全量测试记录中的唯一失败为恢复文档清单覆盖检查；两份清单现已覆盖 48 张内置表，原有检查通过。
全量结果中的 6 条警告均来自已有 LangGraph `create_react_agent` 弃用提示，不等同于测试失败。
专项通过不替代用户运行的全量复验结果。

20 节点样例测得撤回后 12 个节点可用，持久化状态为 5571 字节，产生 5 个 Memory 状态修订；
相同事件重放新增修订为 0，正文版本保持不变。该测量仅代表此样例，不构成大图性能保证。
实际删除并重建 SQLite 全文检索投影后，撤回内容仍不进入搜索，精确历史正文保持可读。

受影响回归曾出现一次 SQLAlchemy 连接清理警告；对应升级场景独立执行为 2 passed，新增/既有取消场景在每例强制
GC 并将清理警告提升为错误后为 2 passed，未重现。未据此断言该间歇警告已消除。

必测组与行为证据映射：

| 必测组 | 行为测试 |
| --- | --- |
| 图与事件 | 严格字段、注册幂等/冲突、依赖校验、20 节点多层 all/any、撤回后注册、独立 Source 序号 |
| 原生传播 | Memory active/inactive、Topic 四通道、候选截断、Context、独立事实与共享绑定 |
| 引用与用途 | 原始引用类型/字段、旧 Memory citation、不可用生成拒绝、审计原文、恢复后的原引用 |
| 重启与重放 | file-backed SQLite 重启、跨 Scope 同编号隔离、成功重放零新修订、真实写入后异常与取消回滚 |

专项命令：

```bash
uv run --locked python -m pytest -q \
  tests/builtin/runtime/test_source_lifecycle.py \
  tests/e2e/test_source_lifecycle.py \
  tests/builtin/persistence/test_topic_memory.py \
  tests/builtin/persistence/test_topic_memory_oceanbase.py
```

取得组织方文件后，在规定的 Python 3.12 和固定依赖环境中运行：

```bash
export POWERCONTEXT_CANDIDATE=/home/enovo/work/wt-contest/powercontext-contest-09
export POWERCONTEXT_PUBLIC=/path/to/public-evaluation-checkout
PYTHONPATH="$POWERCONTEXT_CANDIDATE/src:$POWERCONTEXT_PUBLIC" \
  python -m pytest -q -p no:cacheprovider \
  "$POWERCONTEXT_PUBLIC/challenges/memory-01-source-lifecycle/acceptance"
```

本地测试不构成官方通过或得分证明；真实后端跳过项也不构成后端验收证据。

## AI 使用说明

使用 OpenAI Codex 辅助合同分析、方案设计、代码与测试实现，并使用独立 subagent 进行代码审查。
专项命令由代理在本地执行；基线、首阶段测试与全量测试由用户手动执行。没有推送、创建 PR 或对外提交。
