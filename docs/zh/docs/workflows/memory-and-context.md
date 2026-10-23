---
title: 保存与召回 Memory
description: 保存项目决策、检索相关历史并纠正过时 Memory。
---

# 保存与召回 Memory

Memory 保存持久的决策、约束和事实。PreparedContext 为单次请求筛选相关历史，属于临时结果，不会再创建一条 Memory。

## 保存、召回与纠正

1. 完成 [Quick Start](../get-started/quickstart.md)，让不同会话解析到相同 Scope。
2. 显式请 Agent 保存信息。直接调用 `remember_memory` 不需要模型。
3. 使用 `search_memory` 搜索，或在宿主提供对应工具时用 `list_memory_entries` 和 `get_memory_entry` 检查条目。
   引用结果时保留返回的 citation。
4. 使用当前 citation 修订错误信息，或退役不应继续召回的信息。退役会将条目移出活跃召回范围，同时保留历史。

各宿主工具名称不同，见[接入 Agent](../integrations/index.md)。完整请求结构和并发要求见 [HTTP API](../develop/http-api.md)。

FTS 的候选检索和准入使用相同的查询词。查询归一化会排除较长查询中的常见英语虚词，并识别问题前后独立的执行指令，
例如“Use only supplied context”和“Do not call tools, read files, inspect old sessions, or delegate”。
这些指令不能为无关事实提供匹配依据。显式加引号的词始终参与检索，包括 `"AND" "OR" precedence` 中作为标识符的虚词。
包含领域信息的指令和未识别的措辞仍参与检索；这是一项保守的词项规则，
并非通用意图分类器。默认准入要求匹配剩余全部不同查询词的 25%，至少两个；只有一两个词的查询要求匹配一个词。
直接搜索和上下文准备使用相同规则，可选的 recall gate 放宽门槛时也如此。存储文本和向量查询保持不变，
词语重合本身不能证明语义相关。

## 自动上下文与提取

召回 Hook 向 Server 请求有大小上限的 PreparedContext。没有相关信息时返回 `empty` 是正常结果。
历史内容不能覆盖当前指令或项目现状。

按[输出标准上下文文本](prepare-context-text.md)选择 Memory、Experience、Profile 和 Topic Memory，
设置输出顺序、各类别条数及总条数上限。

采集 Prompt 会创建 Source 证据，不保证提取出 Memory。按[模型配置](../get-started/configure-models.md)
启用生成模型与 Source 处理；只有需要[向量或混合搜索](configure-vector-search.md)时才配置 Embedding。
记录项目信息前，检查宿主的 Prompt 采集开关。

Topic Memory 有独立的处理与检索入口，包括 MCP 的 `search_topic_memory` 和 `get_topic_memory`。
基本的显式 Memory 写入不会启用 Topic Memory；相关设置和运行时能力见[配置](../operate/configuration.md)。

交接当前任务边界使用 [Handoff](memory-and-handoff.md)，保留原始证据使用 [Sources](sources.md)。

## 按来源依赖停用与恢复

内置 Python Runtime 的 `runtime.lifecycle.for_scope(scope_id)` 支持显式注册 Source、Memory 和 Topic Memory
的证据依赖。每个 Scope 使用单写者；父节点须先注册，绑定后不能更换引用、父节点或策略。
`all` 表示所有父节点共同支持整份正文，`any` 表示任一父节点都能独立支持整份正文。

```python
from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleEvent

lifecycle = runtime.lifecycle.for_scope(scope_id)
await lifecycle.register_source("source-a", captured.source_ref, revision=3)
await lifecycle.register_memory(
    "memory-a", saved.entry.citation, parents=("source-a",), policy="all"
)
await lifecycle.register_topic(
    "topic-a", topic_ref, parents=("memory-a",), policy="any"
)

active_ids = await lifecycle.apply(
    SourceLifecycleEvent(
        node_id="source-a", revision=3, sequence=1,
        available=False, reason="来源已撤回",
    )
)
audit_reference = await lifecycle.resolve("memory-a", purpose="audit")
```

这里的 `runtime` 来自 `open_builtin_runtime()`；`scope_id` 已注册；`captured` 是该 Scope 的 Source 捕获结果，
`saved` 是显式 Memory 写入结果且包含 `entry`，`topic_ref` 是已经发布、可精确读取的 Topic Memory ArtifactRef。
生命周期版本 `revision` 由调用方指定，与 Memory 或 Topic 的正文版本无关。注册引用必须属于当前 Scope 并可读取。
这个入口属于内置 Python Runtime，不增加 HTTP 或 MCP 操作。

Source 注册时可用。派生节点注册时立即继承父节点的可用状态；撤回后再注册的节点也遵循同一规则。
每个 Source 的事件序号独立且单调递增；版本不匹配、低序号、同序号不同内容都会报 `SourceLifecycleError`。
同一个事件重放不会新增 Memory 修订。恢复时发送更大序号、`available=True` 的事件即可。

一次注册或事件传播在同一数据库事务中更新图、原生 Memory 状态和 Topic 检索资格。写入失败或操作取消会回滚；
调用方可以重试同一事件。注册、事件与资格跨重启保留，不同 Scope 互不影响。失败后不需要改正文或重新生成摘要。

不可用 Memory 退出当前列表、搜索和 PreparedContext；使用 `include_inactive=True` 仍可检查其状态。
不可用 Topic 退出当前浏览、原生搜索和 PreparedContext，但原始 ArtifactRef 仍可精确读取完整正文。
`purpose="generation"` 拒绝不可用节点，`purpose="audit"` 返回保留的原始引用；恢复后生成用途也返回该原始引用。
Memory 的状态修订可以前进，绑定的正文版本和历史 citation 保持不变。绑定期间不要从外部改写该 Memory 正文版本。
